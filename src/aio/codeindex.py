"""Lightweight symbol index for codebase navigation (#K).

Builds a map of top-level/def/class symbols across the repo so the agent can
jump to "where is X defined" instead of grepping blindly. Pure stdlib: Python
files are parsed with ``ast``; other common languages use cheap regex patterns.
Dependency-free; bounded for large repos.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from .projectmap import IGNORE_DIRS, _gitignore_patterns, _ignored

MAX_FILES = 2000

# (regex, kind) per language extension — captures the symbol name in group 1.
_REGEX_LANG: dict[str, list[tuple[str, str]]] = {
    ".js": [(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", "function"),
            (r"^\s*(?:export\s+)?class\s+(\w+)", "class"),
            (r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", "function")],
    ".go": [(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", "func"),
            (r"^\s*type\s+(\w+)\s+struct", "type")],
    ".rs": [(r"^\s*(?:pub\s+)?fn\s+(\w+)", "fn"),
            (r"^\s*(?:pub\s+)?struct\s+(\w+)", "struct"),
            (r"^\s*(?:pub\s+)?enum\s+(\w+)", "enum")],
    ".rb": [(r"^\s*def\s+(\w+)", "def"), (r"^\s*class\s+(\w+)", "class")],
    ".java": [(r"\b(?:class|interface)\s+(\w+)", "class"),
              (r"\b(?:public|private|protected)\s+[\w<>\[\]]+\s+(\w+)\s*\(", "method")],
}
_REGEX_LANG[".ts"] = _REGEX_LANG[".tsx"] = _REGEX_LANG[".jsx"] = _REGEX_LANG[".mjs"] = _REGEX_LANG[".js"]


def _python_symbols(text: str) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, "function", node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, "class", node.lineno))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{sub.name}", "method", sub.lineno))
    return out


def _regex_symbols(text: str, patterns: list[tuple[str, str]]) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    compiled = [(re.compile(p), kind) for p, kind in patterns]
    for i, line in enumerate(text.splitlines(), start=1):
        for rx, kind in compiled:
            m = rx.search(line)
            if m:
                out.append((m.group(1), kind, i))
                break
    return out


class CodeIndex:
    """Symbol -> [(relpath, kind, line)] across the working directory."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)
        self.symbols: dict[str, list[tuple[str, str, int]]] = {}
        self.n_files = 0
        self.n_symbols = 0

    def build(self) -> "CodeIndex":
        patterns = _gitignore_patterns(self.workdir)
        for f in self._iter_files(patterns):
            ext = f.suffix.lower()
            handler = ext == ".py" or ext in _REGEX_LANG
            if not handler:
                continue
            try:
                text = f.read_text("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            rel = str(f.relative_to(self.workdir))
            syms = _python_symbols(text) if ext == ".py" else _regex_symbols(text, _REGEX_LANG[ext])
            for name, kind, line in syms:
                self.symbols.setdefault(name, []).append((rel, kind, line))
                self.n_symbols += 1
            self.n_files += 1
        return self

    def _iter_files(self, patterns):
        count = 0
        for f in self.workdir.rglob("*"):
            if count >= MAX_FILES:
                break
            if not f.is_file():
                continue
            rel_parts = f.relative_to(self.workdir).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            rel = str(f.relative_to(self.workdir))
            if _ignored(rel, f.name, patterns):
                continue
            count += 1
            yield f

    def lookup(self, query: str, limit: int = 50) -> list[dict]:
        """Find symbols by exact, then case-insensitive substring match."""
        q = query.strip()
        results: list[dict] = []
        seen = set()

        def add(name: str):
            for rel, kind, line in self.symbols.get(name, []):
                key = (rel, line, name)
                if key not in seen:
                    seen.add(key)
                    results.append({"symbol": name, "kind": kind, "path": rel, "line": line})

        if q in self.symbols:           # exact
            add(q)
        ql = q.lower()
        for name in self.symbols:        # substring (incl. Class.method tail)
            tail = name.split(".")[-1]
            if name != q and (ql in name.lower() or ql == tail.lower()):
                add(name)
                if len(results) >= limit:
                    break
        return results[:limit]
