"""Storage + the two retrievers.

AtlasStore  -> MongoDB Atlas:  $search (BM25 over code, identifier words and
               symbol names)  and  $vectorSearch (cosine over embeddings).
MemoryStore -> the same interface in-process: a small BM25 implementation and
               numpy cosine. Used by the tests and for offline demos.

Collections:
  repos  : {_id: "owner/name", url, commit, branch, status, progress, stats, overview, ...}
  chunks : {chunk_id, repo_id, path, language, kind, symbol, symbols, start_line,
            end_line, content, signature, doc, ident_words, symbol_text, embedding}
  files  : {repo_id, path, language, lines, content}   (for the code viewer)
"""
from __future__ import annotations

import math
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Protocol

import numpy as np

from .chunker import search_text, split_identifier

PUBLIC = {"embedding": 0, "_id": 0, "ident_words": 0, "symbol_text": 0}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store(Protocol):
    def upsert_repo(self, repo_id: str, fields: dict) -> None: ...
    def get_repo(self, repo_id: str) -> dict | None: ...
    def list_repos(self) -> list[dict]: ...
    def delete_repo(self, repo_id: str) -> int: ...
    def clear_repo_content(self, repo_id: str) -> None: ...
    def existing_chunk_ids(self, repo_id: str) -> set[str]: ...
    def add_files(self, files: list[dict]) -> None: ...
    def add_chunks(self, chunks: list[dict], vectors: np.ndarray) -> None: ...
    def keyword_search(self, repo_id: str, query: str, k: int) -> list[dict]: ...
    def vector_search(self, repo_id: str, vector: np.ndarray, k: int) -> list[dict]: ...
    def list_files(self, repo_id: str) -> list[dict]: ...
    def get_file(self, repo_id: str, path: str) -> dict | None: ...
    def chunks_where(self, repo_id: str, query: dict, limit: int) -> list[dict]: ...


# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"[A-Za-z0-9_$]+")


def tokenize(text: str) -> list[str]:
    """Lower-cased word tokens, keeping whole identifiers *and* their parts,
    so both `GhidraLauncher` and `launcher` match."""
    out: list[str] = []
    for tok in _TOKEN.findall(text):
        low = tok.lower()
        out.append(low)
        parts = split_identifier(tok)
        if len(parts) > 1:
            out.extend(parts)
    return out


class BM25:
    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf: list[Counter] = []
        self.df: Counter = Counter()
        self.lengths: list[int] = []
        self.postings: dict[str, list[int]] = defaultdict(list)

    def add(self, text: str) -> None:
        toks = tokenize(text)
        tf = Counter(toks)
        idx = len(self.tf)
        self.tf.append(tf)
        self.lengths.append(len(toks))
        for t in tf:
            self.df[t] += 1
            self.postings[t].append(idx)

    def scores(self, query: str, allowed: set[int] | None = None) -> dict[int, float]:
        n = len(self.tf)
        if n == 0:
            return {}
        avg = sum(self.lengths) / n
        out: dict[int, float] = defaultdict(float)
        for t in set(tokenize(query)):
            if t not in self.df:
                continue
            idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            for i in self.postings[t]:
                if allowed is not None and i not in allowed:
                    continue
                f = self.tf[i][t]
                denom = f + self.k1 * (1 - self.b + self.b * self.lengths[i] / avg)
                out[i] += idf * f * (self.k1 + 1) / denom
        return out


class MemoryStore:
    """In-process store. Thread-safe enough for the background indexer."""

    SYMBOL_BOOST = 3.0

    def __init__(self):
        self.lock = threading.RLock()
        self.repos: dict[str, dict] = {}
        self.files: dict[tuple[str, str], dict] = {}
        self.chunks: list[dict] = []
        self.vectors: list[np.ndarray] = []
        self.text_index = BM25()
        self.symbol_index = BM25()
        self.by_repo: dict[str, set[int]] = defaultdict(set)

    # repos
    def upsert_repo(self, repo_id, fields):
        with self.lock:
            self.repos.setdefault(repo_id, {"repo_id": repo_id, "created_at": now()}).update(
                {**fields, "updated_at": now()}
            )

    def get_repo(self, repo_id):
        r = self.repos.get(repo_id)
        return dict(r) if r else None

    def list_repos(self):
        return sorted((dict(r) for r in self.repos.values()), key=lambda r: r["repo_id"])

    def clear_repo_content(self, repo_id):
        with self.lock:
            for i in self.by_repo.pop(repo_id, set()):
                self.chunks[i] = {"repo_id": None}  # tombstone keeps indexes aligned
            for key in [k for k in self.files if k[0] == repo_id]:
                del self.files[key]

    def delete_repo(self, repo_id):
        with self.lock:
            n = len(self.by_repo.get(repo_id, ()))
            self.clear_repo_content(repo_id)
            self.repos.pop(repo_id, None)
            return n

    # content
    def existing_chunk_ids(self, repo_id):
        return {self.chunks[i]["chunk_id"] for i in self.by_repo.get(repo_id, set())}

    def add_files(self, files):
        with self.lock:
            for f in files:
                self.files[(f["repo_id"], f["path"])] = dict(f)

    def add_chunks(self, chunks, vectors):
        with self.lock:
            for c, v in zip(chunks, np.asarray(vectors, dtype=np.float32)):
                idx = len(self.chunks)
                self.chunks.append(dict(c))
                self.vectors.append(v)
                self.text_index.add(search_text(c))
                self.symbol_index.add(c.get("symbol_text", ""))
                self.by_repo[c["repo_id"]].add(idx)

    @staticmethod
    def _public(c: dict, score: float) -> dict:
        return {**{k: v for k, v in c.items() if k not in PUBLIC}, "score": float(score)}

    def keyword_search(self, repo_id, query, k):
        allowed = self.by_repo.get(repo_id, set())
        scores = self.text_index.scores(query, allowed)
        for i, s in self.symbol_index.scores(query, allowed).items():
            scores[i] = scores.get(i, 0.0) + self.SYMBOL_BOOST * s
        top = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return [self._public(self.chunks[i], s) for i, s in top]

    def vector_search(self, repo_id, vector, k):
        ids = sorted(self.by_repo.get(repo_id, set()))
        if not ids:
            return []
        mat = np.stack([self.vectors[i] for i in ids])
        sims = mat @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-sims)[:k]
        return [self._public(self.chunks[ids[j]], sims[j]) for j in order]

    def list_files(self, repo_id):
        return sorted(
            ({"path": f["path"], "language": f["language"], "lines": f["lines"]}
             for (r, _), f in self.files.items() if r == repo_id),
            key=lambda f: f["path"],
        )

    def get_file(self, repo_id, path):
        f = self.files.get((repo_id, path))
        return dict(f) if f else None

    def chunks_where(self, repo_id, query, limit):
        out = []
        for i in sorted(self.by_repo.get(repo_id, set())):
            c = self.chunks[i]
            if all(_match(c.get(k), v) for k, v in query.items()):
                out.append(self._public(c, 0.0))
                if len(out) >= limit:
                    break
        return out


def _match(value, cond) -> bool:
    if isinstance(cond, dict) and "$in" in cond:
        return value in cond["$in"]
    if isinstance(cond, dict) and "$regex" in cond:
        return value is not None and re.search(cond["$regex"], str(value)) is not None
    return value == cond


# --------------------------------------------------------------------------- #
class AtlasStore:
    def __init__(self, uri: str, db_name: str, vector_index: str, text_index: str):
        from pymongo import ASCENDING, MongoClient

        if not uri:
            raise RuntimeError("MONGODB_URI is not set. Add it to backend/.env, or set STORE=memory.")
        self.client = MongoClient(uri, retryWrites=True, socketTimeoutMS=120_000, connectTimeoutMS=20_000)
        self.db = self.client[db_name]
        self.repos = self.db["repos"]
        self.chunks = self.db["chunks"]
        self.files = self.db["files"]
        self.vector_index = vector_index
        self.text_index = text_index
        self.chunks.create_index([("chunk_id", ASCENDING)], unique=True)
        self.chunks.create_index([("repo_id", ASCENDING), ("path", ASCENDING), ("start_line", ASCENDING)])
        self.files.create_index([("repo_id", ASCENDING), ("path", ASCENDING)], unique=True)

    # repos
    def upsert_repo(self, repo_id, fields):
        self.repos.update_one(
            {"_id": repo_id},
            {"$set": {**fields, "updated_at": now()}, "$setOnInsert": {"created_at": now()}},
            upsert=True,
        )

    @staticmethod
    def _repo(doc: dict) -> dict:
        doc["repo_id"] = doc.pop("_id")
        return doc

    def get_repo(self, repo_id):
        r = self.repos.find_one({"_id": repo_id})
        return self._repo(r) if r else None

    def list_repos(self):
        return [self._repo(r) for r in self.repos.find({}, {"overview": 0}).sort("_id", 1)]

    def clear_repo_content(self, repo_id):
        self.chunks.delete_many({"repo_id": repo_id})
        self.files.delete_many({"repo_id": repo_id})

    def delete_repo(self, repo_id):
        n = self.chunks.count_documents({"repo_id": repo_id})
        self.clear_repo_content(repo_id)
        self.repos.delete_one({"_id": repo_id})
        return n

    # content
    def existing_chunk_ids(self, repo_id):
        return {d["chunk_id"] for d in self.chunks.find({"repo_id": repo_id}, {"chunk_id": 1, "_id": 0})}

    def add_files(self, files):
        self._insert(self.files, [dict(f) for f in files], batch=50)

    def add_chunks(self, chunks, vectors):
        from bson.binary import Binary, BinaryVectorDtype

        rows = [
            # packed float32 BSON vector: ~1.5 KB per chunk instead of ~3.5 KB as doubles
            {**c, "embedding": Binary.from_vector([float(x) for x in v], BinaryVectorDtype.FLOAT32)}
            for c, v in zip(chunks, vectors)
        ]
        self._insert(self.chunks, rows, batch=64)

    @staticmethod
    def _insert(coll, rows: list[dict], batch: int, attempts: int = 6) -> None:
        """Insert in small batches and survive flaky connections.

        Free-tier clusters on home/college Wi-Fi drop connections now and then
        (WinError 10054). Each batch is retried with backoff; on a retry some
        rows may already be in, so duplicate-key errors (code 11000) count as success.
        """
        import time

        from pymongo.errors import AutoReconnect, BulkWriteError, ConnectionFailure, NetworkTimeout

        for i in range(0, len(rows), batch):
            part = rows[i:i + batch]
            for attempt in range(attempts):
                try:
                    coll.insert_many([dict(r) for r in part], ordered=False)
                    break
                except BulkWriteError as e:
                    errors = e.details.get("writeErrors", [])
                    if errors and all(err.get("code") == 11000 for err in errors):
                        break
                    raise
                except (AutoReconnect, ConnectionFailure, NetworkTimeout):
                    if attempt == attempts - 1:
                        raise
                    wait = min(2 ** (attempt + 1), 30)
                    print(f"\n  connection dropped, retrying in {wait}s…", flush=True)
                    time.sleep(wait)

    def keyword_search(self, repo_id, query, k):
        pipeline = [
            {"$search": {
                "index": self.text_index,
                "compound": {
                    "filter": [{"equals": {"path": "repo_id", "value": repo_id}}],
                    "should": [
                        {"text": {"query": query, "path": ["content", "ident_words", "doc", "path"]}},
                        {"text": {"query": query, "path": "symbol_text", "score": {"boost": {"value": 3}}}},
                    ],
                    "minimumShouldMatch": 1,
                },
            }},
            {"$limit": k},
            {"$set": {"score": {"$meta": "searchScore"}}},
            {"$project": PUBLIC},
        ]
        return list(self.chunks.aggregate(pipeline))

    def vector_search(self, repo_id, vector, k):
        pipeline = [
            {"$vectorSearch": {
                "index": self.vector_index,
                "path": "embedding",
                "queryVector": [float(x) for x in vector],
                "numCandidates": max(k * 10, 150),
                "limit": k,
                "filter": {"repo_id": {"$eq": repo_id}},
            }},
            {"$set": {"score": {"$meta": "vectorSearchScore"}}},
            {"$project": PUBLIC},
        ]
        return list(self.chunks.aggregate(pipeline))

    def list_files(self, repo_id):
        cur = self.files.find({"repo_id": repo_id}, {"_id": 0, "path": 1, "language": 1, "lines": 1})
        return list(cur.sort("path", 1))

    def get_file(self, repo_id, path):
        return self.files.find_one({"repo_id": repo_id, "path": path}, {"_id": 0})

    def chunks_where(self, repo_id, query, limit):
        cur = self.chunks.find({"repo_id": repo_id, **query}, PUBLIC).sort(
            [("path", 1), ("start_line", 1)]
        )
        return list(cur.limit(limit))
