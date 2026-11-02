"""Hybrid retrieval: run keyword and semantic search, fuse with Reciprocal Rank Fusion.

Why RRF: BM25 scores (unbounded, ~0-40) and cosine scores (0-1) are not on the
same scale, so adding them is meaningless. RRF ignores raw scores and only uses
each result's *rank* in each list:

    score(d) = sum over retrievers r of  w_r / (k + rank_r(d))

A chunk ranked #1 by keyword and #8 by semantic beats one that only one
retriever found. k=60 is the constant from the original RRF paper
(Cormack et al., 2009); it stops the top rank from dominating.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from .embeddings import Embedder
from .store import Store

Mode = Literal["hybrid", "keyword", "semantic"]

# Looks like code: camelCase, PascalCase with 2+ humps, snake_case, dotted.path, call(), `ticks`
_CODEY = re.compile(
    r"`[^`]+`|\b[a-z]+[A-Z]\w*|\b[A-Z][a-z]+[A-Z]\w*|\b\w+_\w+\b|\b\w+\.\w+\(?|\b\w+\(\)"
)


def looks_like_code(query: str) -> bool:
    return bool(_CODEY.search(query))


def rrf(rankings: dict[str, list[dict]], k: int = 60, weights: dict[str, float] | None = None) -> list[dict]:
    weights = weights or {}
    fused: dict[str, dict] = {}
    for name, results in rankings.items():
        w = weights.get(name, 1.0)
        for rank, r in enumerate(results, start=1):
            cid = r["chunk_id"]
            entry = fused.setdefault(cid, {**r, "score": 0.0, "ranks": {}})
            entry["score"] += w / (k + rank)
            entry["ranks"][name] = rank
    return sorted(fused.values(), key=lambda r: -r["score"])


class HybridRetriever:
    def __init__(self, store: Store, embedder: Embedder, *, candidates: int = 40, rrf_k: int = 60,
                 identifier_boost: float = 1.0):
        self.store = store
        self.embedder = embedder
        self.candidates = candidates
        self.rrf_k = rrf_k
        # >1.0 gives keyword search more say when the query contains identifiers.
        # Off (1.0) by default; scripts/eval.py measures whether it helps.
        self.identifier_boost = identifier_boost
        self._pool = ThreadPoolExecutor(max_workers=4)

    def _keyword(self, repo_id: str, query: str) -> list[dict]:
        return self.store.keyword_search(repo_id, query, self.candidates)

    def _semantic(self, repo_id: str, query: str) -> list[dict]:
        vec = self.embedder.embed([query])[0]
        return self.store.vector_search(repo_id, vec, self.candidates)

    def lists(self, repo_id: str, query: str, mode: Mode = "hybrid") -> dict[str, list[dict]]:
        """Raw ranked lists from each retriever that the mode needs (run in parallel)."""
        jobs = {}
        if mode in ("hybrid", "keyword"):
            jobs["keyword"] = self._pool.submit(self._keyword, repo_id, query)
        if mode in ("hybrid", "semantic"):
            jobs["semantic"] = self._pool.submit(self._semantic, repo_id, query)
        return {name: f.result() for name, f in jobs.items()}

    def fuse(self, lists: dict[str, list[dict]], query: str) -> list[dict]:
        weights = {}
        if self.identifier_boost != 1.0 and looks_like_code(query):
            weights["keyword"] = self.identifier_boost
        return rrf(lists, self.rrf_k, weights)

    def search(self, repo_id: str, query: str, mode: Mode = "hybrid", k: int = 6) -> list[dict]:
        lists = self.lists(repo_id, query, mode)
        if mode == "hybrid":
            results = self.fuse(lists, query)
        else:
            name = mode
            results = [{**r, "ranks": {name: i}} for i, r in enumerate(lists[name], start=1)]
        return dedupe_overlaps(results)[:k]


def dedupe_overlaps(results: list[dict]) -> list[dict]:
    """Drop a result whose line range sits inside a better-ranked one from the
    same file (a class outline already covers its own methods' range, and
    long functions are split into overlapping windows)."""
    kept: list[dict] = []
    for r in results:
        dup = False
        for k in kept:
            if k["path"] != r["path"]:
                continue
            if k.get("kind") == "outline" or r.get("kind") == "outline":
                continue  # outline + a method from it are both useful
            overlap = min(k["end_line"], r["end_line"]) - max(k["start_line"], r["start_line"]) + 1
            if overlap > 0.6 * (r["end_line"] - r["start_line"] + 1):
                dup = True
                break
        if not dup:
            kept.append(r)
    return kept
