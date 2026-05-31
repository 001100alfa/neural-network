"""File-manipulation tools: read, write, edit, list."""

from __future__ import annotations

import difflib
from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath


def _edit_not_found_hint(text: str, old_string: str) -> str:
    """Explain WHY old_string didn't match, so the model can self-correct.

    Distinguishes the common failure modes Claude-Code-style edits hit:
    whitespace/indentation differences, CRLF vs LF, and "almost there" matches —
    rather than a bare "not found".
    """
    base = "old_string not found in file."
    # whitespace-only difference: same text once leading/trailing spaces are normalised
    norm_old = "\n".join(line.strip() for line in old_string.splitlines())
    norm_text = "\n".join(line.strip() for line in text.splitlines())
    if norm_old and norm_old in norm_text:
        return (base + " A whitespace/indentation difference is preventing the match — "
                "the same text exists but with different leading spaces or tabs. "
                "Read the file and copy the exact indentation.")
    # closest line, to point the model at the right place
    first = old_string.splitlines()[0].strip() if old_string.splitlines() else old_string.strip()
    if first:
        candidates = [ln for ln in text.splitlines() if ln.strip()]
        match = difflib.get_close_matches(first, candidates, n=1, cutoff=0.6)
        if match:
            return (base + f" The closest line in the file is:\n    {match[0]!r}\n"
                    "Read the file and copy the exact text (including whitespace).")
    return base + " Read the file with read_file and copy the exact text to match."

MAX_READ_BYTES = 400_000


def _symbol_span(text: str, symbol: str) -> tuple[int, int] | None:
    """Return (start_line, end_line) (1-based, inclusive) for a symbol definition.

    Uses Python ``ast`` (supports ``Class.method``); otherwise scans for a line
    defining the name and takes an indentation/brace-bounded block.
    """
    import ast

    name = symbol.split(".")[-1]
    try:
        tree = ast.parse(text)

        def find(nodes):
            for node in nodes:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if node.name == name or node.name == symbol:
                        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                        return start, getattr(node, "end_lineno", node.lineno)
                    if isinstance(node, ast.ClassDef):
                        hit = find(node.body)
                        if hit:
                            return hit
            return None

        span = find(tree.body)
        if span:
            return span
    except SyntaxError:
        pass

    # generic fallback: find a definition line, then take an indent/brace block
    lines = text.splitlines()
    import re

    pat = re.compile(rf"\b{re.escape(name)}\b")
    for i, ln in enumerate(lines):
        if pat.search(ln) and re.search(r"\b(def|function|func|fn|class|struct|type|interface)\b", ln):
            start = i + 1
            base_indent = len(ln) - len(ln.lstrip())
            j = i + 1
            depth = ln.count("{") - ln.count("}")
            braces = "{" in ln
            while j < len(lines):
                cur = lines[j]
                if braces:
                    depth += cur.count("{") - cur.count("}")
                    if depth <= 0:
                        j += 1
                        break
                else:
                    if cur.strip() and (len(cur) - len(cur.lstrip())) <= base_indent:
                        break
                j += 1
            return start, min(j, len(lines))
    return None


def _apply_selected_hunks(old: str, new: str, ctx: ToolContext, path: str) -> tuple[str, int, int]:
    """Ask the UI per hunk; rebuild text keeping only accepted hunks.

    Returns (result_text, kept, total). Falls back to ``new`` if the UI can't
    prompt per hunk.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines)
    groups = [g for g in sm.get_grouped_opcodes(1)]
    if not groups or not hasattr(ctx.ui, "confirm_hunk"):
        return new, 0, 0
    # decide each changed group
    keep = {}
    for i, group in enumerate(groups, start=1):
        preview = []
        for tag, a1, a2, b1, b2 in group:
            if tag in ("delete", "replace"):
                preview += ["-" + ln.rstrip("\n") for ln in old_lines[a1:a2]]
            if tag in ("insert", "replace"):
                preview += ["+" + ln.rstrip("\n") for ln in new_lines[b1:b2]]
            if tag == "equal":
                preview += [" " + ln.rstrip("\n") for ln in old_lines[a1:a2]]
        keep[i] = ctx.ui.confirm_hunk(path, i, len(groups), preview)
    # rebuild: walk full opcodes, applying changed spans only when accepted
    result, gi = [], 0
    changed_spans = []
    for group in groups:
        gi += 1
        a_start = group[0][1]
        a_end = group[-1][2]
        changed_spans.append((a_start, a_end, gi, group))
    cursor = 0
    for a_start, a_end, gi, group in changed_spans:
        result.extend(old_lines[cursor:a_start])  # unchanged context before
        if keep.get(gi):
            for tag, a1, a2, b1, b2 in group:
                if tag == "equal":
                    result.extend(old_lines[a1:a2])
                else:
                    result.extend(new_lines[b1:b2])
        else:
            result.extend(old_lines[a_start:a_end])  # reject -> keep original
        cursor = a_end
    result.extend(old_lines[cursor:])
    kept = sum(1 for v in keep.values() if v)
    return "".join(result), kept, len(groups)


class ReadFileTool(Tool):
    name = "read_file"
    description = (
        "Read a UTF-8 text file and return its contents with line numbers. "
        "Use this before editing a file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file, relative to the working directory."},
            "offset": {"type": "integer", "description": "1-based line to start from (optional)."},
            "limit": {"type": "integer", "description": "Maximum number of lines to read (optional)."},
            "symbol": {"type": "string", "description": "Read just this function/class/method's "
                       "definition from the file (Python; falls back to a line scan otherwise)."},
        },
        "required": ["path"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        if not p.exists():
            raise ToolError(f"File not found: {_relpath(p, ctx)}")
        if p.is_dir():
            raise ToolError(f"{_relpath(p, ctx)} is a directory, not a file.")
        data = p.read_bytes()[:MAX_READ_BYTES]
        text = data.decode("utf-8", "replace")
        lines = text.splitlines()
        ctx.touch_file(_relpath(p, ctx))

        symbol = (args.get("symbol") or "").strip()
        if symbol:
            span = _symbol_span(text, symbol)
            if span is None:
                raise ToolError(f"symbol {symbol!r} not found in {_relpath(p, ctx)}.")
            offset, end = span
            chunk = lines[offset - 1:end]
            numbered = "\n".join(f"{offset + i:>6}\t{ln}" for i, ln in enumerate(chunk))
            return numbered or "(empty)"

        offset = max(1, int(args.get("offset", 1)))
        limit = int(args.get("limit", len(lines)))
        chunk = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(f"{offset + i:>6}\t{ln}" for i, ln in enumerate(chunk))
        return numbered or "(empty file)"


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. "
        "Parent directories are created automatically."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to write, relative to the working directory."},
            "content": {"type": "string", "description": "Full file contents."},
        },
        "required": ["path", "content"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        content = args.get("content", "")
        existed = p.exists()
        old = p.read_text("utf-8", "replace") if existed and p.is_file() else ""
        if ctx.ui is not None:
            ctx.ui.show_diff(old, content, _relpath(p, ctx))
        ctx.snapshot(p, f"write_file {_relpath(p, ctx)}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        ctx.touch_file(_relpath(p, ctx))
        verb = "Overwrote" if existed else "Created"
        return f"{verb} {_relpath(p, ctx)} ({len(content)} bytes)."


class EditFileTool(Tool):
    name = "edit_file"
    description = (
        "Replace an exact string in a file with a new string. The old_string "
        "must match exactly (including whitespace) and be unique unless "
        "replace_all is true."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "old_string": {"type": "string", "description": "Exact text to find."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        if not p.is_file():
            raise ToolError(f"File not found: {_relpath(p, ctx)}")
        old_string = args["old_string"]
        new_string = args["new_string"]
        replace_all = bool(args.get("replace_all", False))

        text = p.read_text("utf-8", "replace")
        count = text.count(old_string)
        if count == 0:
            raise ToolError(_edit_not_found_hint(text, old_string))
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string is not unique ({count} matches). "
                f"Add more context or set replace_all=true."
            )
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        note = ""
        if ctx.per_hunk:
            new_text, kept, total = _apply_selected_hunks(text, new_text, ctx, _relpath(p, ctx))
            if total:
                note = f" ({kept}/{total} hunks applied)"
            if new_text == text:
                return f"No hunks applied to {_relpath(p, ctx)}."
        if ctx.ui is not None:
            ctx.ui.show_diff(text, new_text, _relpath(p, ctx))
        ctx.snapshot(p, f"edit_file {_relpath(p, ctx)}")
        p.write_text(new_text, encoding="utf-8")
        ctx.touch_file(_relpath(p, ctx))
        n = count if replace_all else 1
        return f"Edited {_relpath(p, ctx)} ({n} replacement{'s' if n != 1 else ''}){note}."


class ListDirTool(Tool):
    name = "list_dir"
    description = "List the entries of a directory (non-recursive)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to list (default: working directory)."},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args.get("path", "."))
        if not p.is_dir():
            raise ToolError(f"Not a directory: {_relpath(p, ctx)}")
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        if not entries:
            return "(empty directory)"
        lines = []
        for e in entries:
            suffix = "/" if e.is_dir() else ""
            size = "" if e.is_dir() else f"  {e.stat().st_size}b"
            lines.append(f"{e.name}{suffix}{size}")
        return "\n".join(lines)
