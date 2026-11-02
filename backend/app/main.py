"""FastAPI app: index a GitHub repo, search it (keyword / semantic / hybrid), ask questions."""
from __future__ import annotations

import json
import logging
import threading
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from .embeddings import Embedder, FastEmbedEmbedder
from .hybrid import HybridRetriever, dedupe_overlaps, rrf
from .indexer import index_repo
from .llm import LLM, build_llm
from .rag import Assistant, source_card
from .repo import parse_github_url
from .store import AtlasStore, MemoryStore, Store

log = logging.getLogger("spelunk")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Spelunk", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def unexpected_error(request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"Server error. {type(exc).__name__}: {exc}"[:300]})


# ---- dependencies (overridable in tests) ---------------------------------- #
@lru_cache
def get_store() -> Store:
    if settings.store == "memory":
        log.warning("Using the in-memory store: indexes are lost on restart.")
        return MemoryStore()
    return AtlasStore(settings.mongodb_uri, settings.mongodb_db, settings.vector_index_name, settings.text_index_name)


@lru_cache
def get_embedder() -> Embedder:
    return FastEmbedEmbedder(settings.embed_model, settings.embed_cache_dir)


@lru_cache
def get_llm() -> LLM | None:
    try:
        return build_llm(settings)
    except RuntimeError as e:  # search still works without an LLM key
        log.warning("LLM disabled: %s", e)
        return None


def get_retriever(store: Store = Depends(get_store), embedder: Embedder = Depends(get_embedder)) -> HybridRetriever:
    return HybridRetriever(store, embedder, candidates=settings.candidates, rrf_k=settings.rrf_k)


def get_assistant(store: Store = Depends(get_store), retriever: HybridRetriever = Depends(get_retriever),
                  llm: LLM | None = Depends(get_llm)) -> Assistant:
    return Assistant(store, retriever, llm, top_k=settings.top_k, context_chars=settings.context_chars)


def require_writable():
    if settings.read_only:
        raise HTTPException(403, "This is a read-only demo: indexing is disabled. Run it locally to index your own repos.")


def ready_repo(repo_id: str, store: Store) -> dict:
    repo = store.get_repo(repo_id)
    if repo is None:
        raise HTTPException(404, f"{repo_id} is not indexed yet.")
    if repo.get("status") != "ready":
        raise HTTPException(409, f"{repo_id} is still being indexed ({repo.get('status')}).")
    return repo


# ---- schemas -------------------------------------------------------------- #
ModeT = Literal["hybrid", "keyword", "semantic"]


class IndexRequest(BaseModel):
    url: str = Field(min_length=3, max_length=300)


class SearchRequest(BaseModel):
    repo: str
    query: str = Field(min_length=2, max_length=500)
    mode: ModeT = "hybrid"
    k: int = Field(default=8, ge=1, le=30)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    repo: str
    question: str = Field(min_length=2, max_length=2000)
    mode: ModeT = "hybrid"
    history: list[Message] = []


# ---- routes: repos -------------------------------------------------------- #
_indexing: set[str] = set()
_indexing_lock = threading.Lock()


@app.get("/health")
def health(llm: LLM | None = Depends(get_llm)):
    return {
        "status": "ok",
        "store": settings.store,
        "llm": llm.name if llm else None,
        "embed_model": settings.embed_model,
        "read_only": settings.read_only,
        "default_repo": settings.default_repo,
    }


@app.get("/repos")
def list_repos(store: Store = Depends(get_store)):
    return [{k: v for k, v in r.items() if k != "overview"} for r in store.list_repos()]


@app.get("/repos/{owner}/{name}")
def get_repo(owner: str, name: str, store: Store = Depends(get_store)):
    repo = store.get_repo(f"{owner}/{name}")
    if repo is None:
        raise HTTPException(404, "Repository not indexed")
    repo.pop("overview", None)
    return repo


@app.post("/repos", status_code=202, dependencies=[Depends(require_writable)])
def start_index(req: IndexRequest, store: Store = Depends(get_store), embedder: Embedder = Depends(get_embedder)):
    try:
        ref = parse_github_url(req.url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    with _indexing_lock:
        if ref.repo_id in _indexing:
            return {"repo_id": ref.repo_id, "status": "already_running"}
        _indexing.add(ref.repo_id)
    store.upsert_repo(ref.repo_id, {"status": "queued", "url": ref.url, "owner": ref.owner, "name": ref.name,
                                    "error": None, "progress": {"done": 0, "total": 0}})

    def run():
        try:
            index_repo(ref, store, embedder, settings)
        except Exception:
            pass  # status "error" was recorded on the repo document
        finally:
            with _indexing_lock:
                _indexing.discard(ref.repo_id)

    threading.Thread(target=run, name=f"index-{ref.repo_id}", daemon=True).start()
    return {"repo_id": ref.repo_id, "status": "queued"}


@app.delete("/repos/{owner}/{name}", dependencies=[Depends(require_writable)])
def delete_repo(owner: str, name: str, store: Store = Depends(get_store)):
    repo_id = f"{owner}/{name}"
    if store.get_repo(repo_id) is None:
        raise HTTPException(404, "Repository not indexed")
    return {"deleted_chunks": store.delete_repo(repo_id)}


@app.get("/repos/{owner}/{name}/files")
def list_files(owner: str, name: str, store: Store = Depends(get_store)):
    return store.list_files(f"{owner}/{name}")


@app.get("/repos/{owner}/{name}/file")
def get_file(owner: str, name: str, path: str = Query(..., max_length=500), store: Store = Depends(get_store)):
    f = store.get_file(f"{owner}/{name}", path)
    if f is None:
        raise HTTPException(404, "File not found in the index")
    symbols = store.chunks_where(f"{owner}/{name}", {"path": path}, 2000)
    f["symbols"] = [
        {"symbol": c["symbol"], "kind": c["kind"], "start_line": c["start_line"], "end_line": c["end_line"]}
        for c in symbols if c["kind"] not in ("imports", "module", "section", "block")
    ]
    return f


@app.get("/repos/{owner}/{name}/overview")
def overview(owner: str, name: str, refresh: bool = False, store: Store = Depends(get_store),
             assistant: Assistant = Depends(get_assistant)):
    repo = ready_repo(f"{owner}/{name}", store)
    if repo.get("overview") and not (refresh and not settings.read_only):
        return {"markdown": repo["overview"], "cached": True}
    try:
        md = assistant.overview(repo)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    store.upsert_repo(repo["repo_id"], {"overview": md})
    return {"markdown": md, "cached": False}


# ---- routes: retrieval ------------------------------------------------------ #
@app.post("/search")
def search(req: SearchRequest, store: Store = Depends(get_store), retriever: HybridRetriever = Depends(get_retriever)):
    repo = ready_repo(req.repo, store)
    results = retriever.search(repo["repo_id"], req.query, req.mode, req.k)
    return {"mode": req.mode, "results": [source_card(r, i, repo) for i, r in enumerate(results, 1)]}


@app.post("/compare")
def compare(req: SearchRequest, store: Store = Depends(get_store), retriever: HybridRetriever = Depends(get_retriever)):
    """Keyword, semantic and fused hybrid results for the same query, side by side."""
    repo = ready_repo(req.repo, store)
    lists = retriever.lists(repo["repo_id"], req.query, "hybrid")
    out = {}
    for name in ("keyword", "semantic"):
        ranked = [{**r, "ranks": {name: i}} for i, r in enumerate(lists[name], 1)]
        out[name] = [source_card(r, i, repo) for i, r in enumerate(dedupe_overlaps(ranked)[:req.k], 1)]
    fused = dedupe_overlaps(rrf(lists, settings.rrf_k))[:req.k]
    out["hybrid"] = [source_card(r, i, repo) for i, r in enumerate(fused, 1)]
    return out


@app.post("/ask")
def ask(req: AskRequest, store: Store = Depends(get_store), assistant: Assistant = Depends(get_assistant)):
    repo = ready_repo(req.repo, store)

    def events():
        try:
            for ev in assistant.ask_stream(repo, req.question, req.mode, [m.model_dump() for m in req.history]):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as e:  # surface LLM/key errors to the UI instead of cutting the stream
            log.exception("ask failed")
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            yield f"data: {json.dumps({'type': 'error', 'message': msg[:400]})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
