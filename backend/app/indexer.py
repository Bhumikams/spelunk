"""Index a GitHub repo: clone -> walk -> chunk -> embed -> store.

Progress is written to the repo document so the UI (or the CLI) can show it.
Re-indexing replaces the repo's previous chunks and files.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from .chunker import CodeChunker, embed_text
from .config import Settings
from .embeddings import Embedder
from .repo import RepoRef, build_excludes, clone, preset_for, walk
from .store import Store

log = logging.getLogger("spelunk.indexer")


def index_repo(
    ref: RepoRef,
    store: Store,
    embedder: Embedder,
    s: Settings,
    *,
    local_path: Path | None = None,  # index an existing checkout (tests, offline)
    commit: str | None = None,
) -> dict:
    repo_id = ref.repo_id
    started = time.time()
    preset = preset_for(repo_id)
    previous = store.get_repo(repo_id)  # read before the status update overwrites it

    def status(state: str, **extra):
        store.upsert_repo(repo_id, {"status": state, **extra})
        log.info("%s: %s %s", repo_id, state, extra.get("progress", ""))

    status("cloning", url=ref.url, owner=ref.owner, name=ref.name,
           description=preset.get("description", ""), error=None,
           progress={"done": 0, "total": 0})
    workdir = Path(tempfile.mkdtemp(prefix="spelunk-"))
    try:
        if local_path is not None:
            root, sha, branch = local_path, commit or "local", "local"
        else:
            root = workdir / "src"
            unfinished = previous and previous.get("status") not in (None, "ready") and previous.get("commit")
            sha, branch = clone(ref, root, commit=previous["commit"] if unfinished else None)

        status("scanning", commit=sha, branch=branch)
        excludes = build_excludes(repo_id, index_tests=s.index_tests, extra=s.extra_exclude)
        files, skipped = walk(root, excludes, s.max_file_kb, s.max_files)
        if not files:
            raise RuntimeError("No indexable source files found (check the exclude rules).")

        status("chunking", progress={"done": 0, "total": len(files)})
        chunker = CodeChunker(repo_id, s.chunk_max_lines, s.chunk_min_lines)
        chunks: list[dict] = []
        failed: list[str] = []
        for f in files:
            try:
                chunks.extend(c.to_dict() for c in chunker.chunk(f.path, f.text, f.language))
            except Exception:  # one odd file should not sink the whole index
                log.exception("chunking failed for %s", f.path)
                failed.append(f.path)

        # Resume an interrupted run of the same commit: keep what is already stored.
        # A different commit (or a finished index) starts clean.
        resumable = previous and previous.get("commit") == sha and previous.get("status") != "ready"
        done_ids = store.existing_chunk_ids(repo_id) if resumable else set()
        if not done_ids:
            store.clear_repo_content(repo_id)
            store.add_files([
                {"repo_id": repo_id, "path": f.path, "language": f.language, "lines": f.lines, "content": f.text}
                for f in files
            ])
        else:
            log.info("resuming %s: %d chunks already stored", repo_id, len(done_ids))

        total = len(chunks)
        todo = [c for c in chunks if c["chunk_id"] not in done_ids]
        done = total - len(todo)
        status("embedding", progress={"done": done, "total": total})
        batch = max(16, s.embed_batch)
        for i in range(0, len(todo), batch):
            part = todo[i:i + batch]
            vecs = embedder.embed([embed_text_from_dict(c) for c in part])
            store.add_chunks(part, vecs)
            done += len(part)
            status("embedding", progress={"done": done, "total": total})

        stats = {
            "files": len(files),
            "chunks": total,
            "lines": sum(f.lines for f in files),
            "languages": dict(Counter(f.language for f in files).most_common()),
            "kinds": dict(Counter(c["kind"] for c in chunks).most_common()),
            "skipped": skipped,
            "failed_files": failed[:50],
            "seconds": round(time.time() - started, 1),
        }
        status("ready", stats=stats, progress={"done": total, "total": total},
               indexed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), overview=None)
        return stats
    except Exception as e:
        log.exception("indexing %s failed", repo_id)
        status("error", error=f"{type(e).__name__}: {e}"[:400])
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def embed_text_from_dict(d: dict) -> str:
    from .chunker import Chunk

    c = Chunk(d["repo_id"], d["path"], d["language"], d["kind"], d["symbol"],
              d["start_line"], d["end_line"], d["content"], d.get("signature", ""),
              d.get("doc", ""), d.get("symbols", []))
    return embed_text(c)
