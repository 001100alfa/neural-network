"""File-manipulation tools: read, write, edit, list."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath

MAX_READ_BYTES = 400_000


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
            raise ToolError("old_string not found in file. Read the file and try again.")
        if count > 1 and not replace_all:
            raise ToolError(
                f"old_string is not unique ({count} matches). "
                f"Add more context or set replace_all=true."
            )
        new_text = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        if ctx.ui is not None:
            ctx.ui.show_diff(text, new_text, _relpath(p, ctx))
        ctx.snapshot(p, f"edit_file {_relpath(p, ctx)}")
        p.write_text(new_text, encoding="utf-8")
        n = count if replace_all else 1
        return f"Edited {_relpath(p, ctx)} ({n} replacement{'s' if n != 1 else ''})."


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
