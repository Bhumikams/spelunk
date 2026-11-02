"""Create the two Atlas search indexes on the `chunks` collection (run once).

    python -m scripts.create_indexes

  vector_index : Atlas Vector Search, 384-dim cosine, pre-filter on repo_id
  text_index   : Atlas Search (Lucene BM25) over code, identifier words, doc
                 comments, file path and symbol names

Both fit in the free M0 tier (it allows up to 3 search indexes).
"""
import sys
import time

from pymongo import MongoClient
from pymongo.operations import SearchIndexModel

from app.config import settings

VECTOR = {
    "fields": [
        {"type": "vector", "path": "embedding", "numDimensions": settings.embed_dim, "similarity": "cosine"},
        {"type": "filter", "path": "repo_id"},
    ]
}

TEXT = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "repo_id": {"type": "token"},
            "kind": {"type": "token"},
            "content": {"type": "string", "analyzer": "lucene.standard"},
            "ident_words": {"type": "string", "analyzer": "lucene.standard"},
            "doc": {"type": "string", "analyzer": "lucene.english"},
            "symbol_text": {"type": "string", "analyzer": "lucene.standard"},
            # split "src/ghidra/GhidraLauncher.ts" on / . - so path segments match
            "path": {"type": "string", "analyzer": "lucene.simple"},
        },
    }
}


def ensure(coll, name: str, definition: dict, kind: str) -> None:
    existing = {ix["name"] for ix in coll.list_search_indexes()}
    if name in existing:
        print(f"'{name}' already exists.")
        return
    coll.create_search_index(SearchIndexModel(definition=definition, name=name, type=kind))
    print(f"Requested '{name}' ({kind}).")


def wait(coll, names: list[str]) -> None:
    print("Waiting for indexes to become queryable", end="", flush=True)
    for _ in range(90):
        status = {ix["name"]: ix.get("queryable") for ix in coll.list_search_indexes()}
        if all(status.get(n) for n in names):
            print("\nReady.")
            return
        print(".", end="", flush=True)
        time.sleep(5)
    print("\nStill building; check Atlas > your cluster > Search & Vector Search.")


def main() -> None:
    if not settings.mongodb_uri:
        sys.exit("Set MONGODB_URI in backend/.env first.")
    db = MongoClient(settings.mongodb_uri)[settings.mongodb_db]
    if "chunks" not in db.list_collection_names():
        db.create_collection("chunks")  # search indexes need an existing collection
    coll = db["chunks"]
    ensure(coll, settings.vector_index_name, VECTOR, "vectorSearch")
    ensure(coll, settings.text_index_name, TEXT, "search")
    wait(coll, [settings.vector_index_name, settings.text_index_name])


if __name__ == "__main__":
    main()
