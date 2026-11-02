"""Tests run offline: in-memory store, hash embedder, fake LLM, a tiny local repo.

    cd backend && pytest -q
"""
import json
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.chunker import CodeChunker, split_identifier
from app.config import Settings
from app.embeddings import HashEmbedder
from app.hybrid import HybridRetriever, dedupe_overlaps, looks_like_code, rrf
from app.indexer import index_repo
from app.rag import cited_numbers
from app.repo import build_excludes, matches_any, parse_github_url, walk
from app.store import MemoryStore, tokenize

TS = textwrap.dedent('''
    import { spawn } from "node:child_process";
    import type { Config } from "./config.js";

    /** Launches the headless analyzer for one binary. */
    export class HeadlessLauncher {
      private readonly options: Config;

      constructor(options: Config) {
        this.options = options;
      }

      /** Start the process and wait for the bridge socket. */
      async launch(target: string): Promise<number> {
        const child = spawn("analyzeHeadless", [target, `--x=${"{"}`]);
        await waitForSocket(this.options.socketPath);
        return child.pid ?? -1;
      }

      stop(): void {
        this.options.onStop?.();
      }
    }

    export type LaunchId = string;
    export const MAX_RETRIES = 3;

    // Picks the provider with the highest priority that supports the target.
    export const selectProvider = (providers: Provider[], target: string) => {
      const ok = providers.filter((p) => p.supports(target));
      ok.sort((a, b) => b.priority - a.priority);
      return ok[0];
    };
''')

PY = textwrap.dedent('''
    import socket

    def handle_request(payload):
        """Dispatch one JSON request from the TypeScript client."""
        kind = payload["kind"]
        if kind == "decompile":
            return decompile(payload["address"])
        return {"error": "unknown"}


    class BridgeServer:
        """Unix socket server that runs inside the disassembler."""

        def serve(self, path):
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(path)
            return sock
''')

MD = textwrap.dedent('''
    # Demo tool

    Demo explains how binaries are analysed.

    ## Choosing a provider

    The registry sorts providers by priority and never falls back silently.

    ## Shutdown

    SIGTERM triggers a graceful shutdown of every owned process.
''')


# --------------------------------------------------------------------------- #
def test_split_identifier():
    assert split_identifier("GhidraLaunchFailure") == ["ghidra", "launch", "failure"]
    assert split_identifier("parse_http_endpoint") == ["parse", "http", "endpoint"]
    assert split_identifier("HTTPServer") == ["http", "server"]
    assert "launcher" in tokenize("new HeadlessLauncher()")


def test_typescript_chunks_by_definition():
    chunker = CodeChunker("o/r", max_lines=12, min_lines=4)  # small max_lines forces a class split
    chunks = chunker.chunk("src/launcher.ts", TS, "typescript")
    by_symbol = {c.symbol: c for c in chunks}
    # class is longer than max_lines -> outline + one chunk per method
    assert by_symbol["HeadlessLauncher"].kind == "outline"
    launch = by_symbol["HeadlessLauncher.launch"]
    assert launch.kind == "method"
    assert "Start the process" in launch.doc            # JSDoc attached
    assert "`--x=${\"{\"}`" in launch.content          # template literal with a brace survives
    sel = by_symbol["selectProvider"]
    assert sel.kind == "function"                      # arrow-function const
    assert "highest priority" in sel.doc               # line comment attached
    imports = [c for c in chunks if c.kind == "imports"]
    assert imports and imports[0].start_line == 2
    # every chunk has a sane 1-based line range
    for c in chunks:
        assert 1 <= c.start_line <= c.end_line <= TS.count("\n") + 1


def test_python_and_markdown_chunks():
    chunker = CodeChunker("o/r", max_lines=40, min_lines=3)
    py = {c.symbol: c for c in chunker.chunk("bridge/server.py", PY, "python")}
    assert py["handle_request"].doc.startswith("Dispatch one JSON request")
    assert py["BridgeServer"].kind == "class"
    md = chunker.chunk("docs/demo.md", MD, "markdown")
    assert [c.symbol for c in md] == ["Demo tool", "Demo tool > Choosing a provider", "Demo tool > Shutdown"]


def test_long_function_split_into_windows():
    body = "\n".join(f"  const v{i} = {i};" for i in range(300))
    src = f"export function huge() {{\n{body}\n}}\n"
    chunks = CodeChunker("o/r", max_lines=90).chunk("a.ts", src, "typescript")
    assert len(chunks) >= 3
    assert chunks[0].symbol == "huge [part 1]"
    assert chunks[1].content.startswith("// export function huge()")   # signature kept in later parts


def test_globs_and_excludes():
    ex = build_excludes("morluto/rea", index_tests=False)
    assert matches_any("src/a/b.test.ts", ex)
    assert matches_any("node_modules/x/index.js", ex)
    assert matches_any("docs/readme/README_ja.md", ex)
    assert not matches_any("src/ghidra/GhidraLauncher.ts", ex)
    assert not matches_any("docs/tool-design.md", ex)


def test_parse_github_url():
    assert parse_github_url("https://github.com/morluto/rea").repo_id == "morluto/rea"
    assert parse_github_url("github.com/morluto/rea.git").repo_id == "morluto/rea"
    assert parse_github_url("morluto/rea").url == "https://github.com/morluto/rea"
    with pytest.raises(ValueError):
        parse_github_url("not a repo url at all")


def test_rrf_rewards_agreement():
    a = {"chunk_id": "a", "path": "x", "start_line": 1, "end_line": 2}
    b = {"chunk_id": "b", "path": "y", "start_line": 1, "end_line": 2}
    c = {"chunk_id": "c", "path": "z", "start_line": 1, "end_line": 2}
    fused = rrf({"keyword": [a, b], "semantic": [c, b]}, k=60)
    assert fused[0]["chunk_id"] == "b"                 # found by both beats #1 in one list
    assert fused[0]["ranks"] == {"keyword": 2, "semantic": 2}
    assert looks_like_code("where is parseMcpHttpEndpoint used")
    assert looks_like_code("what does session_document() do")
    assert not looks_like_code("how does shutdown work")


def test_dedupe_overlapping_windows():
    r1 = {"chunk_id": "1", "path": "a.ts", "kind": "function", "start_line": 1, "end_line": 90}
    r2 = {"chunk_id": "2", "path": "a.ts", "kind": "function", "start_line": 81, "end_line": 170}
    r3 = {"chunk_id": "3", "path": "a.ts", "kind": "function", "start_line": 10, "end_line": 40}
    assert [r["chunk_id"] for r in dedupe_overlaps([r1, r2, r3])] == ["1", "2"]


def test_cited_numbers():
    assert cited_numbers("A [1], B [3][1] and [9]", 4) == [1, 3]


# --------------------------------------------------------------------------- #
@pytest.fixture
def demo_repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "bridge").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "launcher.ts").write_text(TS)
    (tmp_path / "src" / "launcher.test.ts").write_text("test('x', () => {})\n")
    (tmp_path / "bridge" / "server.py").write_text(PY)
    (tmp_path / "docs" / "guide.md").write_text(MD)
    (tmp_path / "README.md").write_text("# Demo\n\nDemo is a toy reverse-engineering tool used in tests.\n")
    return tmp_path


def test_walk_skips_tests(demo_repo):
    files, skipped = walk(demo_repo, build_excludes("demo/tool", index_tests=False), 320, 100)
    paths = {f.path for f in files}
    assert "src/launcher.ts" in paths and "src/launcher.test.ts" not in paths
    assert skipped["excluded"] == 1


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.last_user = ""

    def stream(self, system, user):
        self.last_user = user
        yield "The launcher spawns `analyzeHeadless` "
        yield "and waits for the socket [1]."

    def complete(self, system, user):
        self.last_user = user
        return "## What it is\nA demo [overview]."


@pytest.fixture
def client(demo_repo):
    store, emb, llm = MemoryStore(), HashEmbedder(), FakeLLM()
    index_repo(parse_github_url("demo/tool"), store, emb, Settings(), local_path=demo_repo, commit="abc123")
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_embedder] = lambda: emb
    main.app.dependency_overrides[main.get_llm] = lambda: llm
    with TestClient(main.app) as c:
        c.llm = llm
        yield c
    main.app.dependency_overrides.clear()


def test_repo_listing_and_stats(client):
    repos = client.get("/repos").json()
    assert repos[0]["repo_id"] == "demo/tool" and repos[0]["status"] == "ready"
    stats = repos[0]["stats"]
    assert stats["files"] == 4 and stats["chunks"] > 5
    files = client.get("/repos/demo/tool/files").json()
    assert {f["path"] for f in files} >= {"src/launcher.ts", "bridge/server.py"}
    f = client.get("/repos/demo/tool/file", params={"path": "src/launcher.ts"}).json()
    assert "HeadlessLauncher" in f["content"]
    assert any(s["symbol"] == "HeadlessLauncher" for s in f["symbols"])
    assert any(s["symbol"] == "selectProvider" and s["kind"] == "function" for s in f["symbols"])


def test_keyword_finds_identifier(client):
    res = client.post("/search", json={"repo": "demo/tool", "query": "selectProvider", "mode": "keyword"}).json()
    top = res["results"][0]
    assert top["symbol"] == "selectProvider"
    assert top["github_url"] == "https://github.com/demo/tool/blob/abc123/src/launcher.ts#L" + \
        f"{top['start_line']}-L{top['end_line']}"


def test_camelcase_matches_plain_words(client):
    res = client.post("/search", json={"repo": "demo/tool", "query": "headless launcher", "mode": "keyword"}).json()
    assert res["results"][0]["path"] == "src/launcher.ts"


def test_compare_returns_three_lists(client):
    res = client.post("/compare", json={"repo": "demo/tool", "query": "unix socket bridge server", "k": 3}).json()
    assert set(res) == {"keyword", "semantic", "hybrid"}
    assert res["hybrid"] and "ranks" in res["hybrid"][0]


def test_ask_streams_sources_then_tokens(client):
    with client.stream("POST", "/ask", json={"repo": "demo/tool", "question": "How is the analyzer launched?"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert events[0]["type"] == "sources" and events[0]["sources"]
    assert "".join(e["text"] for e in events if e["type"] == "token").startswith("The launcher")
    assert events[-1] == {"type": "done", "cited": [1]}
    assert "[1] " in client.llm.last_user and "```typescript" in client.llm.last_user


def test_overview_is_cached(client):
    first = client.get("/repos/demo/tool/overview").json()
    assert first["cached"] is False and "What it is" in first["markdown"]
    assert "README (excerpt)" in client.llm.last_user
    assert client.get("/repos/demo/tool/overview").json()["cached"] is True


def test_unknown_repo_and_read_only(client, monkeypatch):
    assert client.post("/search", json={"repo": "nope/nope", "query": "x y"}).status_code == 404
    monkeypatch.setattr(main.settings.__class__, "read_only", True, raising=False)
    object.__setattr__(main.settings, "read_only", True)
    try:
        assert client.post("/repos", json={"url": "https://github.com/a/b"}).status_code == 403
        assert client.delete("/repos/demo/tool").status_code == 403
    finally:
        object.__setattr__(main.settings, "read_only", False)


def test_hybrid_retriever_modes(demo_repo):
    store, emb = MemoryStore(), HashEmbedder()
    index_repo(parse_github_url("demo/tool"), store, emb, Settings(), local_path=demo_repo)
    r = HybridRetriever(store, emb, candidates=10)
    hybrid = r.search("demo/tool", "graceful shutdown SIGTERM", "hybrid", 3)
    assert hybrid[0]["path"] == "docs/guide.md"
    assert set(hybrid[0]["ranks"]) == {"keyword", "semantic"}


def test_interrupted_index_resumes(demo_repo):
    """A run that dies mid-embedding (e.g. Atlas drops the connection) resumes on the
    next run of the same commit instead of starting from zero."""

    class FlakyEmbedder(HashEmbedder):
        def __init__(self, fail_after):
            super().__init__()
            self.calls, self.fail_after, self.texts = 0, fail_after, 0

        def embed(self, texts):
            self.calls += 1
            if self.calls > self.fail_after:
                raise ConnectionResetError("WinError 10054")
            self.texts += len(texts)
            return super().embed(texts)

    (demo_repo / "src" / "many.ts").write_text("\n\n".join(
        f"export function handler{i}(x: number) {{\n  return x + {i};\n}}" for i in range(40)
    ))
    s = Settings()
    object.__setattr__(s, "embed_batch", 16)
    store = MemoryStore()
    ref = parse_github_url("demo/tool")
    flaky = FlakyEmbedder(fail_after=1)
    with pytest.raises(ConnectionResetError):
        index_repo(ref, store, flaky, s, local_path=demo_repo, commit="abc")
    assert store.get_repo("demo/tool")["status"] == "error"
    first = len(store.existing_chunk_ids("demo/tool"))
    assert first == 16

    second = FlakyEmbedder(fail_after=99)
    stats = index_repo(ref, store, second, s, local_path=demo_repo, commit="abc")
    assert store.get_repo("demo/tool")["status"] == "ready"
    assert second.texts == stats["chunks"] - first          # only the missing chunks were embedded
    assert len(store.existing_chunk_ids("demo/tool")) == stats["chunks"]
    assert len(store.list_files("demo/tool")) == 5          # files were not duplicated


def test_fullwidth_citations_are_counted():
    assert cited_numbers("Starts here 【1】 and here 【3†source】, not 【9】", 4) == [1, 3]
