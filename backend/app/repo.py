"""Fetch a repository and decide which files are worth indexing.

`clone()` does a shallow `git clone` and records the commit SHA, so every
citation can link to the exact line on GitHub even after the repo moves on.
`walk()` applies include/exclude rules and skips binaries, vendored code,
lockfiles and (by default) tests.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Extensions we know how to read. Anything else is skipped.
CODE_EXTS = {
    ".ts", ".mts", ".cts", ".tsx", ".js", ".mjs", ".cjs", ".jsx",
    ".py", ".java", ".kt", ".go", ".rs", ".rb", ".php", ".cs", ".swift",
    ".c", ".h", ".cc", ".cpp", ".hpp", ".sh", ".sql",
}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".mermaid"}
CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml"}

ALWAYS_EXCLUDE = [
    ".git/**", "**/node_modules/**", "**/dist/**", "**/build/**", "**/vendor/**",
    "**/.venv/**", "**/__pycache__/**", "**/coverage/**",
    "**/*.min.js", "**/*.map", "**/package-lock.json", "**/yarn.lock",
    "**/pnpm-lock.yaml", "**/poetry.lock", "**/*.lock",
]
TEST_EXCLUDE = [
    "**/*.test.*", "**/*.spec.*", "**/*.fixture.*", "**/test/**", "**/tests/**",
    "**/__tests__/**", "**/test_*.py", "**/*_test.py", "**/*_test.go",
]

# Per-repo presets. Keys are "owner/name" (lower-case).
PRESETS: dict[str, dict] = {
    "morluto/rea": {
        "description": "REA: Reverse Engineer Anything (MCP server for reverse engineering)",
        # Translations of the README, the marketing site, release scripts and
        # vendored licences add noise without explaining how REA works.
        "exclude": [
            "docs/readme/**", "docs/verification/**", "docs/releases/**",
            "website/**", "scripts/**", "licenses/**",
            "third_party/**", "patches/**", "**/*.po", "**/*.json",
            "**/*.yml", "**/*.yaml", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
        ],
    },
}


@dataclass
class RepoRef:
    owner: str
    name: str
    url: str

    @property
    def repo_id(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class SourceFile:
    path: str
    text: str
    language: str
    lines: int = field(init=False)

    def __post_init__(self):
        self.lines = self.text.count("\n") + 1


_GH = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?/?(?:[#?].*)?$")


def parse_github_url(url: str) -> RepoRef:
    url = url.strip()
    m = _GH.match(url)
    if not m:
        short = re.fullmatch(r"([\w.-]+)/([\w.-]+)", url)
        if not short:
            raise ValueError("Use a GitHub URL like https://github.com/owner/repo")
        m = short
    owner, name = m.group(1), m.group(2)
    return RepoRef(owner, name, f"https://github.com/{owner}/{name}")


def preset_for(repo_id: str) -> dict:
    return PRESETS.get(repo_id.lower(), {})


def clone(ref: RepoRef, dest: Path, timeout: int = 600, commit: str | None = None) -> tuple[str, str]:
    """Shallow-clone into dest, optionally at a specific commit. Returns (commit_sha, branch)."""
    if dest.exists():
        shutil.rmtree(dest)
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed on the server.")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    res = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch", "-q", ref.url, str(dest)],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    if res.returncode != 0:
        msg = (res.stderr or res.stdout).strip().splitlines()[-1:] or ["unknown error"]
        raise RuntimeError(f"git clone failed: {msg[0]}")
    branch = _git(dest, "rev-parse", "--abbrev-ref", "HEAD")
    if commit and _git(dest, "rev-parse", "HEAD") != commit:
        # Pin to the commit an unfinished run started on, so it can resume
        fetched = subprocess.run(["git", "fetch", "--depth", "1", "-q", "origin", commit],
                                 cwd=dest, capture_output=True, text=True, timeout=timeout, env=env)
        if fetched.returncode == 0:
            subprocess.run(["git", "checkout", "-q", commit], cwd=dest, capture_output=True, check=True)
    sha = _git(dest, "rev-parse", "HEAD")
    return sha, branch


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


# --------------------------------------------------------------------------- #
# Glob matching with ** support (fnmatch alone treats / like any character)
_glob_cache: dict[str, re.Pattern] = {}


def _glob_to_re(pattern: str) -> re.Pattern:
    if pattern in _glob_cache:
        return _glob_cache[pattern]
    i, out = 0, ""
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    rx = re.compile(out + r"\Z")
    _glob_cache[pattern] = rx
    return rx


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(_glob_to_re(p).match(path) for p in patterns)


def language_for(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in (".ts", ".mts", ".cts"):
        return "typescript"
    if ext == ".tsx":
        return "tsx"
    if ext in (".js", ".mjs", ".cjs", ".jsx"):
        return "javascript"
    if ext == ".py":
        return "python"
    if ext == ".java":
        return "java"
    if ext in (".md", ".mdx"):
        return "markdown"
    if ext in CODE_EXTS:
        return ext.lstrip(".")
    if ext in DOC_EXTS:
        return "text" if ext != ".mermaid" else "mermaid"
    if ext in CONFIG_EXTS:
        return ext.lstrip(".")
    return None


def build_excludes(repo_id: str, *, index_tests: bool, extra: list[str] | None = None) -> list[str]:
    ex = list(ALWAYS_EXCLUDE)
    if not index_tests:
        ex += TEST_EXCLUDE
    ex += preset_for(repo_id).get("exclude", [])
    ex += extra or []
    return ex


def walk(root: Path, excludes: list[str], max_file_kb: int, max_files: int) -> tuple[list[SourceFile], dict]:
    """Return indexable files plus a small report of what was skipped and why."""
    files: list[SourceFile] = []
    skipped = {"excluded": 0, "unsupported": 0, "too_large": 0, "binary": 0, "over_limit": 0}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        # prune whole excluded directories early
        dirnames[:] = sorted(
            d for d in dirnames if not matches_any(f"{rel_dir}{d}/", excludes)
        )
        for fn in sorted(filenames):
            rel = rel_dir + fn
            if matches_any(rel, excludes):
                skipped["excluded"] += 1
                continue
            lang = language_for(rel)
            if lang is None:
                skipped["unsupported"] += 1
                continue
            full = Path(dirpath) / fn
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > max_file_kb * 1024:
                skipped["too_large"] += 1
                continue
            raw = full.read_bytes()
            if b"\x00" in raw[:4096]:
                skipped["binary"] += 1
                continue
            if len(files) >= max_files:
                skipped["over_limit"] += 1
                continue
            text = raw.decode("utf-8", errors="replace")
            if text.strip():
                files.append(SourceFile(rel, text, lang))
    return files, skipped
