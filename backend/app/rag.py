"""Answering: grounded Q&A with citations, and a repo-level architecture overview."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterator

from .hybrid import HybridRetriever, Mode
from .llm import LLM
from .store import Store

ASK_SYSTEM = """You are Spelunk, an assistant that helps engineers reverse-engineer \
an unfamiliar codebase: {repo}{desc}.

Answer ONLY from the numbered sources (code chunks and docs from the repo).
- Cite every claim with the source number in plain ASCII square brackets, e.g. [2] or [1][4]
  (not 【】).
- Name the exact files, functions, classes and types involved, in `backticks`.
- When the question is about a flow ("how does X work", "what happens when"),
  walk through it step by step in call order, one cited step per line.
- Quote short code snippets only when they make the point clearer.
- If the sources do not contain the answer, say so plainly, say what they *do*
  show, and suggest an identifier or file to search for next. Never invent
  file names, functions or behaviour.
Use Markdown. Be concise: lead with the direct answer."""

OVERVIEW_SYSTEM = """You are Spelunk. Write an architecture map of the repository {repo} \
for an engineer who has never seen it, using ONLY the material provided \
(README excerpt, architecture notes, directory statistics and key symbols).

Structure, in Markdown:
## What it is            (2-3 sentences)
## How a request flows   (numbered steps from entry point to result, naming real files)
## Main modules          (a table: directory | responsibility | key files/symbols)
## Where to start reading  (5 files, one line each on why)
## Good questions to ask   (5 concrete questions someone could ask Spelunk next)

Only name files, directories and symbols that appear in the material."""


def format_sources(results: list[dict], max_chars: int) -> str:
    blocks = []
    for i, r in enumerate(results, start=1):
        code = r["content"]
        if len(code) > max_chars:
            code = code[:max_chars] + "\n... (truncated)"
        lang = r.get("language", "")
        head = f"[{i}] {r['path']}:{r['start_line']}-{r['end_line']}  ({r['kind']} {r['symbol']})"
        if r.get("doc"):
            head += f"\nDoc: {r['doc'][:300]}"
        blocks.append(f"{head}\n```{lang}\n{code}\n```")
    return "\n\n".join(blocks)


def cited_numbers(text: str, n: int) -> list[int]:
    # [1] and also 【1】 / 【1†source】, which gpt-oss models tend to emit
    nums = {int(x) for x in re.findall(r"[\[【]\s*(\d{1,2})(?:[†:][^\]】]*)?\s*[\]】]", text)}
    return sorted(x for x in nums if 1 <= x <= n)


def source_card(r: dict, i: int, repo: dict) -> dict:
    commit = repo.get("commit") or repo.get("branch") or "HEAD"
    url = repo.get("url", "")
    link = f"{url}/blob/{commit}/{r['path']}#L{r['start_line']}-L{r['end_line']}" if url and commit != "local" else None
    return {
        "n": i,
        "chunk_id": r["chunk_id"],
        "path": r["path"],
        "language": r.get("language"),
        "kind": r["kind"],
        "symbol": r["symbol"],
        "start_line": r["start_line"],
        "end_line": r["end_line"],
        "signature": r.get("signature", ""),
        "doc": r.get("doc", ""),
        "snippet": r["content"][:1200],
        "score": round(float(r.get("score", 0.0)), 5),
        "ranks": r.get("ranks", {}),
        "github_url": link,
    }


class Assistant:
    def __init__(self, store: Store, retriever: HybridRetriever, llm: LLM | None, *, top_k: int = 6,
                 context_chars: int = 1800):
        self.store = store
        self.retriever = retriever
        self.llm = llm
        self.top_k = top_k
        self.context_chars = context_chars

    def _llm(self) -> LLM:
        if self.llm is None:
            raise RuntimeError("No LLM configured. Set GROQ_API_KEY in backend/.env.")
        return self.llm

    # ---- ask --------------------------------------------------------------
    def ask_stream(self, repo: dict, question: str, mode: Mode = "hybrid",
                   history: list[dict] | None = None) -> Iterator[dict]:
        """Yields events: sources -> token* -> done."""
        query = question
        if history:  # follow-ups like "and where is that called?" need the previous turn
            last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
            if last_user and len(question.split()) < 12:
                query = f"{last_user} {question}"
        results = self.retriever.search(repo["repo_id"], query, mode, self.top_k)
        cards = [source_card(r, i, repo) for i, r in enumerate(results, start=1)]
        yield {"type": "sources", "sources": cards, "mode": mode}
        if not results:
            yield {"type": "token", "text": "I couldn't find anything in this repository matching that. "
                                            "Try an identifier, a file name, or different wording."}
            yield {"type": "done", "cited": []}
            return

        convo = ""
        if history:
            turns = history[-4:]
            convo = "Earlier in this conversation:\n" + "\n".join(
                f"{m['role'].upper()}: {m['content'][:600]}" for m in turns
            ) + "\n\n"
        user = (
            f"{convo}Question: {question}\n\nSources:\n"
            f"{format_sources(results, self.context_chars)}"
        )
        desc = f" ({repo['description']})" if repo.get("description") else ""
        system = ASK_SYSTEM.format(repo=repo["repo_id"], desc=desc)
        answer = []
        for tok in self._llm().stream(system, user):
            answer.append(tok)
            yield {"type": "token", "text": tok}
        yield {"type": "done", "cited": cited_numbers("".join(answer), len(results))}

    # ---- overview -----------------------------------------------------------
    def overview_material(self, repo: dict) -> str:
        repo_id = repo["repo_id"]
        files = self.store.list_files(repo_id)
        parts: list[str] = []

        readme = next((f for f in files if f["path"].lower() in ("readme.md", "readme.rst", "readme")), None)
        if readme:
            text = self.store.get_file(repo_id, readme["path"])["content"]
            text = re.sub(r"<[^>]+>", "", text)          # drop HTML badges/markup
            text = re.sub(r"\n{3,}", "\n\n", text)
            parts.append("README (excerpt):\n" + text[:4000])

        for f in files:
            name = f["path"].lower()
            if re.search(r"(^|/)(architecture[^/]*|agents\.md|contributing\.md)$", name):
                text = self.store.get_file(repo_id, f["path"])["content"]
                parts.append(f"{f['path']} (excerpt):\n{text[:3000]}")

        # directory statistics two levels deep
        dirs: dict[str, Counter] = defaultdict(Counter)
        for f in files:
            segs = f["path"].split("/")
            key = "/".join(segs[:2]) if len(segs) > 2 else (segs[0] if len(segs) > 1 else "(root)")
            dirs[key]["files"] += 1
            dirs[key]["lines"] += f["lines"]
        top = sorted(dirs.items(), key=lambda kv: -kv[1]["lines"])[:40]
        parts.append("Directories by size (files, lines):\n" + "\n".join(
            f"- {d}/: {c['files']} files, {c['lines']} lines" for d, c in top
        ))

        # key symbols: class outlines and big classes give the shape of the code
        outlines = self.store.chunks_where(repo_id, {"kind": {"$in": ["outline", "class"]}}, 4000)
        outlines.sort(key=lambda c: -(c["end_line"] - c["start_line"]))
        sym_lines = [f"- {c['symbol']} ({c['path']}:{c['start_line']}) {c.get('doc', '')[:120]}"
                     for c in outlines[:60]]
        parts.append("Largest classes:\n" + "\n".join(sym_lines))

        entry = [f["path"] for f in files if re.search(r"(^|/)(main|index|cli|server|app)\.(ts|js|py|go)$", f["path"])]
        if entry:
            parts.append("Likely entry points:\n" + "\n".join(f"- {p}" for p in entry[:15]))
        return "\n\n".join(parts)

    def overview(self, repo: dict) -> str:
        # ~4k tokens of material keeps a single call inside Groq's free-tier per-minute budget
        material = self.overview_material(repo)
        return self._llm().complete(OVERVIEW_SYSTEM.format(repo=repo["repo_id"]), material[:16000])
