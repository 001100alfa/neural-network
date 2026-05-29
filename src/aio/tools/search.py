"""Search tools: glob (filename patterns) and grep (content search)."""

from __future__ import annotations

import re
from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath

IGNORE_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".mypy_cache", ".pytest_cache"}
MAX_RESULTS = 200


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern (e.g. '**/*.py'). Returns matching paths."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py' or 'src/*.ts'."},
            "path": {"type": "string", "description": "Base directory to search from (default: working dir)."},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = ctx.safe_path(args.get("path", "."))
        pattern = args["pattern"]
        matches = []
        for m in base.glob(pattern):
            if any(part in IGNORE_DIRS for part in m.parts):
                continue
            matches.append(m)
            if len(matches) >= MAX_RESULTS:
                break
        if not matches:
            return f"No files match {pattern!r}."
        matches.sort(key=lambda p: _relpath(p, ctx))
        return "\n".join(_relpath(p, ctx) for p in matches)


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents for a regular expression. Returns matching lines with file:line prefixes."
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression to search for."},
            "path": {"type": "string", "description": "Base directory to search (default: working dir)."},
            "glob": {"type": "string", "description": "Restrict to files matching this glob (e.g. '*.py')."},
            "ignore_case": {"type": "boolean", "description": "Case-insensitive search (default false)."},
        },
        "required": ["pattern"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        base = ctx.safe_path(args.get("path", "."))
        flags = re.IGNORECASE if args.get("ignore_case") else 0
        try:
            rx = re.compile(args["pattern"], flags)
        except re.error as exc:
            raise ToolError(f"Invalid regex: {exc}")

        file_glob = args.get("glob", "**/*")
        results: list[str] = []
        for f in base.glob(file_glob):
            if not f.is_file() or any(part in IGNORE_DIRS for part in f.parts):
                continue
            try:
                text = f.read_text("utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if rx.search(line):
                    results.append(f"{_relpath(f, ctx)}:{i}: {line.strip()[:200]}")
                    if len(results) >= MAX_RESULTS:
                        results.append(f"... (truncated at {MAX_RESULTS} matches)")
                        return "\n".join(results)
        return "\n".join(results) if results else f"No matches for {args['pattern']!r}."
