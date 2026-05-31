"""Project-wide refactor tools.

A bare model renaming a symbol does it by eyeballing files and reliably misses
occurrences. ``rename_symbol`` does it properly: it scans the whole working tree
(gitignore- and ignore-dir-aware), replaces only **whole-word** matches, applies
every edit atomically with per-file snapshots (so the whole refactor is a single
rewindable operation), and reports exactly what changed. Supports a dry run.
"""

from __future__ import annotations

import re
from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath

MAX_FILES = 5000
MAX_FILE_BYTES = 2_000_000
# Text-ish files worth rewriting; binary/vendored stuff is skipped.
_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar",
             ".whl", ".pyc", ".so", ".dylib", ".dll", ".ico", ".woff", ".woff2"}


class RenameSymbolTool(Tool):
    name = "rename_symbol"
    description = (
        "Rename an identifier across the WHOLE codebase, replacing only "
        "whole-word matches (not substrings), atomically and rewindably. Use "
        "this for safe project-wide renames instead of many edit_file calls. "
        "Set dry_run=true to preview the affected files first."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "old_name": {"type": "string", "description": "Existing identifier to rename."},
            "new_name": {"type": "string", "description": "Replacement identifier."},
            "dry_run": {"type": "boolean", "description": "Preview matches without writing (default false)."},
            "path": {"type": "string", "description": "Restrict to this subdirectory (default: whole workdir)."},
        },
        "required": ["old_name", "new_name"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        old = (args.get("old_name") or "").strip()
        new = (args.get("new_name") or "").strip()
        dry = bool(args.get("dry_run", False))
        if not old or not new:
            raise ToolError("rename_symbol requires non-empty old_name and new_name.")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", old) or \
           not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", new):
            raise ToolError("old_name/new_name must be valid identifiers.")
        if old == new:
            raise ToolError("old_name and new_name are identical.")

        from ..projectmap import IGNORE_DIRS, _gitignore_patterns, _ignored

        base = ctx.safe_path(args.get("path", "."))
        if not base.is_dir():
            raise ToolError(f"Not a directory: {_relpath(base, ctx)}")
        patterns = _gitignore_patterns(ctx.workdir)
        pat = re.compile(rf"\b{re.escape(old)}\b")

        hits: list[tuple[Any, int]] = []   # (path, count)
        count = 0
        scanned = 0
        for f in sorted(base.rglob("*")):
            if scanned >= MAX_FILES:
                break
            if not f.is_file() or f.suffix.lower() in _SKIP_EXT:
                continue
            rel_parts = f.relative_to(ctx.workdir).parts
            if any(p in IGNORE_DIRS for p in rel_parts):
                continue
            rel = str(f.relative_to(ctx.workdir))
            if _ignored(rel, f.name, patterns):
                continue
            try:
                text = f.read_bytes()[:MAX_FILE_BYTES].decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            n = len(pat.findall(text))
            if n:
                hits.append((f, n))
                count += n

        if not hits:
            return f"No whole-word occurrences of {old!r} found ({scanned} files scanned)."

        files_list = "\n".join(f"  {_relpath(f, ctx)} ({n})" for f, n in hits)
        if dry:
            return (f"DRY RUN: would rename {old!r} -> {new!r} in {count} place(s) "
                    f"across {len(hits)} file(s):\n{files_list}")

        # Apply atomically: snapshot every file first (one rewindable operation),
        # then write. If a write fails the snapshots already allow a full rewind.
        for f, _n in hits:
            ctx.snapshot(f, f"rename_symbol {old}->{new} in {_relpath(f, ctx)}")
        for f, _n in hits:
            text = f.read_text("utf-8")
            f.write_text(pat.sub(new, text), encoding="utf-8")
        return (f"Renamed {old!r} -> {new!r} in {count} place(s) across "
                f"{len(hits)} file(s):\n{files_list}")
