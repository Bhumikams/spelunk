"""Code-aware chunking.

The unit of retrieval is a *definition*: a function, method, class, interface,
type or enum, with the comment block above it attached. That is what people
ask about ("where is X decided?"), and it gives every citation an exact line
range.

* TypeScript / JavaScript / Python / Java are parsed with tree-sitter.
  Regex chunking breaks on template literals, nested braces in strings and
  decorators; a real parser does not.
* Large classes become one "outline" chunk (signature + member list) plus one
  chunk per method, so a 2,000-line class is still searchable method by method.
* Runs of small top-level definitions (one-line types, constants) are merged
  so the index is not flooded with 3-line chunks.
* Markdown is split by heading; anything else falls back to line windows.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache

# --------------------------------------------------------------------------- #
@dataclass
class Chunk:
    repo_id: str
    path: str
    language: str
    kind: str          # function | method | class | interface | type | enum | outline | module | section | block
    symbol: str        # qualified name, e.g. "GhidraLauncher.launch"
    start_line: int    # 1-based, inclusive
    end_line: int
    content: str
    signature: str = ""
    doc: str = ""
    symbols: list[str] = field(default_factory=list)  # every name defined in the chunk

    @property
    def chunk_id(self) -> str:
        key = f"{self.repo_id}:{self.path}:{self.start_line}:{self.end_line}:{self.symbol}"
        return hashlib.sha1(key.encode()).hexdigest()[:20]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["chunk_id"] = self.chunk_id
        # Extra words for the keyword index (the code itself is indexed from `content`)
        d["ident_words"] = ident_words(self)
        d["symbol_text"] = " ".join(
            dict.fromkeys(
                [*self.symbols, *(w for s in self.symbols for w in split_identifier(s))]
            )
        )
        return d


# --------------------------------------------------------------------------- #
# Identifier handling: "GhidraLaunchFailure" should match "ghidra launch failure"
_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]{2,}")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(name: str) -> list[str]:
    parts: list[str] = []
    for piece in re.split(r"[_$.\-/]+", name):
        parts.extend(p.lower() for p in _CAMEL.findall(piece))
    return [p for p in parts if len(p) > 1]


def ident_words(c: Chunk) -> str:
    """Path words plus the split form of every identifier in the chunk, so
    "launch failure" finds `GhidraLaunchFailure` and `launch_failure`."""
    path_words = split_identifier(c.path)
    idents = dict.fromkeys(_IDENT.findall(c.content))
    split_words = (w for i in idents for w in split_identifier(i))
    return " ".join(dict.fromkeys([*path_words, *split_words]))


def search_text(d: dict) -> str:
    """Everything the keyword index sees for one stored chunk (used by MemoryStore;
    Atlas indexes the same fields separately)."""
    return "\n".join(
        [d["path"], d.get("symbol_text", ""), d.get("doc", ""), d["content"], d.get("ident_words", "")]
    )


def embed_text(c: Chunk, max_chars: int = 1600) -> str:
    """What the embedding model sees. MiniLM reads ~256 tokens, so the most
    meaningful parts go first: where it lives, what it is, its doc comment."""
    head = f"{c.path}\n{c.kind} {c.symbol} ({' '.join(split_identifier(c.symbol))})\n"
    if c.doc:
        head += c.doc.strip() + "\n"
    return (head + c.content)[:max_chars]


# --------------------------------------------------------------------------- #
# tree-sitter setup
@lru_cache(maxsize=None)
def _parser(language: str):
    from tree_sitter import Language, Parser

    if language in ("typescript", "javascript"):
        import tree_sitter_typescript as m
        lang = Language(m.language_typescript() if language == "typescript" else m.language_tsx())
    elif language == "tsx":
        import tree_sitter_typescript as m
        lang = Language(m.language_tsx())
    elif language == "python":
        import tree_sitter_python as m
        lang = Language(m.language())
    elif language == "java":
        import tree_sitter_java as m
        lang = Language(m.language())
    else:
        return None
    return Parser(lang)


TS_DEFS = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "lexical_declaration": "const",
    "variable_declaration": "const",
    "module": "namespace",
    "internal_module": "namespace",
}
TS_MEMBERS = {
    "method_definition": "method",
    "abstract_method_signature": "method",
    "public_field_definition": "field",
    "method_signature": "method",
    "property_signature": "field",
}
PY_DEFS = {"function_definition": "function", "class_definition": "class"}
JAVA_DEFS = {
    "class_declaration": "class",
    "interface_declaration": "interface",
    "enum_declaration": "enum",
    "record_declaration": "class",
    "method_declaration": "method",
    "constructor_declaration": "method",
}
CLASSLIKE = {"class", "interface", "namespace", "enum"}
# Functions and classes always keep their own chunk (and their own name in the
# UI), however short. Only small types/constants/fields get grouped.
GROUP_NEVER = {"class", "function", "method"}
COMMENT_TYPES = {"comment", "line_comment", "block_comment"}


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _name(node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    if n is not None:
        return _text(src, n)
    # const foo = ...  /  let a = 1, b = 2
    names = []
    for ch in node.named_children:
        if ch.type == "variable_declarator":
            nn = ch.child_by_field_name("name")
            if nn is not None:
                names.append(_text(src, nn))
    return ", ".join(names) or node.type


def _signature(text: str) -> str:
    """First meaningful line(s) of a definition, up to the opening brace/colon."""
    sig = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("//", "/*", "*", "#", "@")) and not sig:
            continue
        sig.append(s)
        if s.endswith(("{", ":", ";", "=>")) or len(" ".join(sig)) > 200:
            break
    return " ".join(sig)[:240]


def _clean_comment(text: str) -> str:
    lines = []
    for line in text.splitlines():
        s = line.strip()
        s = re.sub(r"^(/\*\*?|\*/|\*|//+|#)\s?", "", s)
        s = s.removesuffix("*/").strip()
        if s:
            lines.append(s)
    return " ".join(lines)[:500]


@dataclass
class _Def:
    kind: str
    name: str
    node: object      # the declaration node (possibly the export wrapper)
    inner: object     # the declaration itself
    start: int        # 0-based line, including attached comments
    end: int
    doc: str


class CodeChunker:
    def __init__(self, repo_id: str, max_lines: int = 90, min_lines: int = 8):
        self.repo_id = repo_id
        self.max_lines = max_lines
        self.min_lines = min_lines

    # ---- public -------------------------------------------------------------
    def chunk(self, path: str, text: str, language: str) -> list[Chunk]:
        if language == "markdown":
            return self._markdown(path, text)
        parser = _parser(language)
        if parser is None:
            return self._windows(path, text, language, kind="block", symbol=path.rsplit("/", 1)[-1])
        src = text.encode("utf-8")
        tree = parser.parse(src)
        lines = text.splitlines()
        defs = self._collect(tree.root_node, src, language)
        return self._assemble(path, language, src, lines, defs)

    # ---- definition discovery ----------------------------------------------
    def _defs_table(self, language: str) -> dict:
        if language == "python":
            return PY_DEFS
        if language == "java":
            return JAVA_DEFS
        return TS_DEFS

    def _unwrap(self, node, language):
        """Return (inner_declaration, kind) or (None, None)."""
        table = self._defs_table(language)
        if node.type in ("export_statement",):
            decl = node.child_by_field_name("declaration")
            if decl is None:
                for ch in node.named_children:
                    if ch.type in table:
                        decl = ch
                        break
            if decl is not None and decl.type in table:
                return decl, table[decl.type]
            return None, None
        if node.type == "decorated_definition":  # python
            d = node.child_by_field_name("definition")
            if d is not None and d.type in table:
                return d, table[d.type]
            return None, None
        if node.type == "expression_statement":  # TS: namespace Foo {}
            for ch in node.named_children:
                if ch.type in ("internal_module", "module"):
                    return ch, "namespace"
        if node.type in table:
            return node, table[node.type]
        return None, None

    def _collect(self, parent, src: bytes, language: str) -> list[_Def]:
        out: list[_Def] = []
        pending_comments: list = []
        for node in parent.named_children:
            if node.type in COMMENT_TYPES:
                pending_comments.append(node)
                continue
            inner, kind = self._unwrap(node, language)
            if inner is None:
                pending_comments = []
                continue
            if kind == "const":
                if self._is_function_const(inner):
                    kind = "function"
                elif not self._is_interesting_const(inner, src):
                    kind = "const-small"
            start = node.start_point[0]
            doc = ""
            # attach comments that end on the line directly above
            while pending_comments and pending_comments[-1].end_point[0] >= start - 1:
                c = pending_comments.pop()
                doc = _clean_comment(_text(src, c)) + (" " + doc if doc else "")
                start = c.start_point[0]
            pending_comments = []
            if language == "python" and not doc:
                doc = self._py_docstring(inner, src)
            out.append(_Def(kind, _name(inner, src), node, inner, start, node.end_point[0], doc))
        return out

    @staticmethod
    def _is_function_const(node) -> bool:
        for ch in node.named_children:
            if ch.type == "variable_declarator":
                v = ch.child_by_field_name("value")
                if v is not None and v.type in ("arrow_function", "function_expression", "function", "generator_function"):
                    return True
        return False

    @staticmethod
    def _is_interesting_const(node, src: bytes) -> bool:
        """`const x = 3` is noise; `const handler = async () => {...}` or a big
        object/table is worth its own chunk."""
        return (node.end_point[0] - node.start_point[0]) >= 3

    @staticmethod
    def _py_docstring(node, src: bytes) -> str:
        body = node.child_by_field_name("body")
        if body is None or not body.named_children:
            return ""
        first = body.named_children[0]
        if first.type == "expression_statement" and first.named_children and first.named_children[0].type == "string":
            return _clean_comment(_text(src, first.named_children[0]).strip("\"' \n"))
        return ""

    def _members(self, inner, src: bytes, language: str) -> list[_Def]:
        body = inner.child_by_field_name("body")
        if body is None:
            return []
        if language == "python":
            return [d for d in self._collect(body, src, language) if d.kind in ("function", "class")]
        if language == "java":
            return [d for d in self._collect(body, src, language)]
        # TS class / interface body
        out: list[_Def] = []
        pending: list = []
        for node in body.named_children:
            if node.type in COMMENT_TYPES:
                pending.append(node)
                continue
            kind = TS_MEMBERS.get(node.type)
            if kind is None:
                pending = []
                continue
            start, doc = node.start_point[0], ""
            while pending and pending[-1].end_point[0] >= start - 1:
                c = pending.pop()
                doc = _clean_comment(_text(src, c)) + (" " + doc if doc else "")
                start = c.start_point[0]
            pending = []
            out.append(_Def(kind, _name(node, src), node, node, start, node.end_point[0], doc))
        return out

    # ---- assembly -----------------------------------------------------------
    def _assemble(self, path, language, src, lines, defs: list[_Def]) -> list[Chunk]:
        chunks: list[Chunk] = []
        covered = [False] * (len(lines) + 1)
        small: list[_Def] = []

        def flush_small():
            if not small:
                return
            # group consecutive small definitions into blocks of <= max_lines
            group: list[_Def] = []
            for d in small + [None]:
                if d is not None and (not group or d.end - group[0].start + 1 <= self.max_lines):
                    group.append(d)
                    continue
                if group:
                    s, e = group[0].start, group[-1].end
                    names = [g.name for g in group]
                    kinds = {g.kind.replace("-small", "") for g in group}
                    chunks.append(self._mk(
                        path, language, lines, s, e,
                        kind=kinds.pop() if len(kinds) == 1 else "definitions",
                        symbol=names[0] if len(names) == 1 else f"{names[0]} (+{len(names) - 1})",
                        doc=" ".join(g.doc for g in group if g.doc)[:500],
                        symbols=names,
                    ))
                group = [d] if d is not None else []
            small.clear()

        for d in defs:
            for i in range(d.start, d.end + 1):
                if i < len(covered):
                    covered[i] = True
            n_lines = d.end - d.start + 1
            if d.kind in CLASSLIKE and n_lines > self.max_lines:
                flush_small()
                chunks.extend(self._big_class(path, language, src, lines, d))
            elif n_lines < self.min_lines and d.kind not in GROUP_NEVER:
                small.append(d)
            else:
                flush_small()
                kind = d.kind.replace("-small", "")
                if kind == "function" and language == "java":
                    kind = "method"
                chunks.extend(self._fit(path, language, lines, d.start, d.end, kind, d.name, d.doc, [d.name]))
        flush_small()

        # leftover top-level code (imports, statements, re-exports)
        run_start = None
        for i in range(len(lines) + 1):
            free = i < len(lines) and not covered[i] and lines[i].strip() != ""
            if free and run_start is None:
                run_start = i
            elif not free and run_start is not None:
                # tolerate blank lines inside a run
                if i < len(lines) and lines[i].strip() == "" and not covered[i]:
                    continue
                self._emit_module(chunks, path, language, lines, run_start, i - 1)
                run_start = None

        chunks.sort(key=lambda c: c.start_line)
        return chunks

    def _emit_module(self, chunks, path, language, lines, s, e):
        while e > s and not lines[e].strip():
            e -= 1
        body = "\n".join(lines[s:e + 1])
        if len(body.strip()) < 40:
            return
        imports_only = all(
            (not ln.strip()) or ln.lstrip().startswith(("import ", "from ", "export {", "export *", "package ", "}", "//"))
            for ln in lines[s:e + 1]
        )
        stem = path.rsplit("/", 1)[-1]
        chunks.extend(self._fit(
            path, language, lines, s, e,
            kind="imports" if imports_only else "module",
            symbol=stem, doc="", symbols=[stem],
        ))

    def _big_class(self, path, language, src, lines, d: _Def) -> list[Chunk]:
        members = self._members(d.inner, src, language)
        out: list[Chunk] = []
        # outline: header + member signatures, cites the whole class range
        header_end = d.inner.child_by_field_name("body")
        header_line = header_end.start_point[0] if header_end is not None else d.start
        header = "\n".join(lines[d.start:header_line + 1])
        member_sigs = [f"  {_signature(_text(src, m.node))}" for m in members if m.kind != "field" or True]
        outline = header + "\n" + "\n".join(member_sigs[:80]) + ("\n  ..." if len(member_sigs) > 80 else "") + "\n}"
        out.append(Chunk(
            self.repo_id, path, language, "outline", d.name, d.start + 1, d.end + 1,
            outline, signature=_signature(header), doc=d.doc,
            symbols=[d.name, *[f"{d.name}.{m.name}" for m in members]],
        ))
        # one chunk per method; fields and tiny members are grouped
        small: list[_Def] = []

        def flush():
            if not small:
                return
            s, e = small[0].start, small[-1].end
            names = [f"{d.name}.{m.name}" for m in small]
            out.extend(self._fit(path, language, lines, s, e, "members",
                                 f"{d.name}.{small[0].name}" + (f" (+{len(small) - 1})" if len(small) > 1 else ""),
                                 " ".join(m.doc for m in small if m.doc)[:500], names))
            small.clear()

        for m in members:
            if m.kind in CLASSLIKE and m.end - m.start + 1 > self.max_lines:  # nested class
                flush()
                out.extend(self._big_class(path, language, src, lines, _Def(
                    m.kind, f"{d.name}.{m.name}", m.node, m.inner, m.start, m.end, m.doc)))
                continue
            short = m.end - m.start + 1 < self.min_lines and m.kind not in GROUP_NEVER
            if short and (not small or m.end - small[0].start + 1 <= self.max_lines):
                small.append(m)
                continue
            flush()
            if short:
                small.append(m)
                continue
            kind = "method" if m.kind in ("function", "method") else m.kind
            out.extend(self._fit(path, language, lines, m.start, m.end, kind,
                                 f"{d.name}.{m.name}", m.doc, [f"{d.name}.{m.name}"]))
        flush()
        return out

    def _fit(self, path, language, lines, s, e, kind, symbol, doc, symbols) -> list[Chunk]:
        """Emit one chunk, or overlapping windows if the range is too long."""
        n = e - s + 1
        if n <= self.max_lines * 1.5:
            return [self._mk(path, language, lines, s, e, kind, symbol, doc, symbols)]
        out, step, part = [], self.max_lines - 10, 1
        sig = _signature("\n".join(lines[s:e + 1]))
        for ws in range(s, e + 1, step):
            we = min(ws + self.max_lines - 1, e)
            c = self._mk(path, language, lines, ws, we, kind, f"{symbol} [part {part}]", doc, symbols)
            if part > 1:  # keep the signature visible in later windows
                c.content = f"// {sig}  (continued, lines {ws + 1}-{we + 1})\n" + c.content
                c.signature = sig
            out.append(c)
            part += 1
            if we == e:
                break
        return out

    def _mk(self, path, language, lines, s, e, kind, symbol, doc="", symbols=None) -> Chunk:
        content = "\n".join(lines[s:e + 1])
        return Chunk(
            self.repo_id, path, language, kind, symbol, s + 1, e + 1, content,
            signature=_signature(content), doc=doc, symbols=symbols or [symbol],
        )

    # ---- non-code -----------------------------------------------------------
    def _markdown(self, path: str, text: str) -> list[Chunk]:
        lines = text.splitlines()
        heads: list[tuple[int, int, str]] = []  # (line, level, title)
        in_fence = False
        for i, ln in enumerate(lines):
            if ln.lstrip().startswith("```"):
                in_fence = not in_fence
            m = None if in_fence else re.match(r"^(#{1,4})\s+(.+?)\s*#*\s*$", ln)
            if m:
                heads.append((i, len(m.group(1)), m.group(2)))
        if not heads or heads[0][0] > 0:
            heads.insert(0, (0, 0, path.rsplit("/", 1)[-1]))
        out: list[Chunk] = []
        trail: list[tuple[int, str]] = []
        for idx, (start, level, title) in enumerate(heads):
            end = (heads[idx + 1][0] - 1) if idx + 1 < len(heads) else len(lines) - 1
            trail = [t for t in trail if t[0] < level] + [(level, title)]
            body = "\n".join(lines[start:end + 1]).strip()
            if len(body) < 30:
                continue
            crumb = " > ".join(t for lvl, t in trail if t and (lvl > 0 or level == 0))
            out.extend(self._fit(path, "markdown", lines, start, end, "section", crumb, "", [title]))
        return out

    def _windows(self, path, text, language, kind, symbol) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []
        return self._fit(path, language, lines, 0, len(lines) - 1, kind, symbol, "", [symbol])
