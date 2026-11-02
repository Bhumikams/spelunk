"""Retrieval evaluation: does the right file show up in the top k?

    python -m scripts.eval                          # eval/rea_questions.json, k=5
    python -m scripts.eval --k 10 --boost 1.5       # also try identifier-weighted RRF

Reports hit@k and MRR for keyword-only, semantic-only and hybrid (RRF),
overall and split by query type (identifier vs natural language).
Run it against your real index (Atlas + MiniLM) and quote *those* numbers.
"""
import argparse
import json
from pathlib import Path

from app.config import settings
from app.embeddings import FastEmbedEmbedder
from app.hybrid import HybridRetriever
from app.store import AtlasStore


def first_hit(results: list[dict], expect: list[str]) -> int | None:
    for rank, r in enumerate(results, start=1):
        if any(r["path"] == e or (e.endswith("/") and r["path"].startswith(e)) for e in expect):
            return rank
    return None


def evaluate(retriever: HybridRetriever, repo: str, questions: list[dict], k: int, modes: list[str]) -> dict:
    table: dict[str, dict] = {}
    per_question = []
    for q in questions:
        row = {"q": q["q"], "type": q["type"]}
        for mode in modes:
            name = mode
            results = retriever.search(repo, q["q"], "hybrid" if mode.startswith("hybrid") else mode, k)
            rank = first_hit(results, q["expect"])
            row[name] = rank
            for bucket in ("all", q["type"]):
                t = table.setdefault(name, {}).setdefault(bucket, {"n": 0, "hits": 0, "rr": 0.0})
                t["n"] += 1
                t["hits"] += rank is not None
                t["rr"] += 1 / rank if rank else 0.0
        per_question.append(row)
    return {"table": table, "rows": per_question}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).resolve().parents[1] / "eval" / "rea_questions.json"))
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--boost", type=float, default=0.0, help="also evaluate hybrid with keyword weight W for code-like queries")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    spec = json.loads(Path(args.file).read_text())
    store = AtlasStore(settings.mongodb_uri, settings.mongodb_db, settings.vector_index_name, settings.text_index_name)
    embedder = FastEmbedEmbedder(settings.embed_model, settings.embed_cache_dir)
    base = HybridRetriever(store, embedder, candidates=settings.candidates, rrf_k=settings.rrf_k)

    res = evaluate(base, spec["repo"], spec["questions"], args.k, ["keyword", "semantic", "hybrid"])
    if args.boost:
        boosted = HybridRetriever(store, embedder, candidates=settings.candidates, rrf_k=settings.rrf_k,
                                  identifier_boost=args.boost)
        extra = evaluate(boosted, spec["repo"], spec["questions"], args.k, ["hybrid"])
        res["table"][f"hybrid+boost{args.boost}"] = extra["table"]["hybrid"]
        for row, xrow in zip(res["rows"], extra["rows"]):
            row[f"hybrid+boost{args.boost}"] = xrow["hybrid"]

    print(f"\n{spec['repo']}: {len(spec['questions'])} questions, k={args.k}\n")
    print(f"{'mode':<20}{'bucket':<12}{'hit@k':>8}{'MRR':>8}")
    for mode, buckets in res["table"].items():
        for bucket in ("all", "identifier", "natural"):
            if bucket in buckets:
                t = buckets[bucket]
                print(f"{mode:<20}{bucket:<12}{t['hits'] / t['n']:>8.2f}{t['rr'] / t['n']:>8.2f}")
    if args.verbose:
        print("\nPer question (rank of first correct file, - = miss):")
        for row in res["rows"]:
            ranks = "  ".join(f"{m}={row[m] or '-'}" for m in res["table"])
            print(f"  [{row['type'][:5]}] {row['q'][:70]:<70} {ranks}")


if __name__ == "__main__":
    main()
