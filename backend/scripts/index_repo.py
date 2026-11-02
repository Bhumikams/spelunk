"""Index a repository from the command line (recommended for big repos like REA).

    python -m scripts.index_repo                         # DEFAULT_REPO (morluto/rea)
    python -m scripts.index_repo https://github.com/pallets/flask
    python -m scripts.index_repo morluto/rea --local ../rea   # use an existing checkout

Indexing runs on your machine, writes to Atlas, and the deployed read-only app
serves the result.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import settings
from app.embeddings import FastEmbedEmbedder
from app.indexer import index_repo
from app.repo import parse_github_url
from app.store import AtlasStore, MemoryStore


class ProgressStore:
    """Wraps a store and prints indexing progress on one line."""

    def __init__(self, inner):
        self.inner = inner
        self.t0 = time.time()

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def upsert_repo(self, repo_id, fields):
        self.inner.upsert_repo(repo_id, fields)
        state, prog = fields.get("status"), fields.get("progress") or {}
        if prog.get("total"):
            pct = 100 * prog["done"] / prog["total"]
            print(f"\r{state:<10} {prog['done']:>6}/{prog['total']:<6} {pct:5.1f}%  {time.time() - self.t0:6.1f}s",
                  end="", flush=True)
        elif state:
            print(f"\n{state}…", end="", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=settings.default_repo)
    ap.add_argument("--local", type=Path, help="index this local checkout instead of cloning")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)

    ref = parse_github_url(args.repo)
    store = MemoryStore() if settings.store == "memory" else AtlasStore(
        settings.mongodb_uri, settings.mongodb_db, settings.vector_index_name, settings.text_index_name)
    print(f"Loading embedding model {settings.embed_model}…")
    embedder = FastEmbedEmbedder(settings.embed_model, settings.embed_cache_dir)
    commit = None
    if args.local:
        import subprocess
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=args.local, capture_output=True, text=True).stdout.strip() or None
    try:
        stats = index_repo(ref, ProgressStore(store), embedder, settings, local_path=args.local, commit=commit)
    except Exception as e:
        sys.exit(f"\nFailed: {e}")
    print(f"\n\nIndexed {ref.repo_id}: {stats['files']} files, {stats['chunks']} chunks, "
          f"{stats['lines']:,} lines in {stats['seconds']}s")
    print("Languages:", ", ".join(f"{k} {v}" for k, v in list(stats["languages"].items())[:6]))
    print("Skipped:", stats["skipped"])


if __name__ == "__main__":
    main()
