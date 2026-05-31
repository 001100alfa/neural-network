"""Atomic multi-edit tool (#J) — several find/replace edits to one file in order.

All edits are validated and applied in memory first; the file is only written if
every edit succeeds (atomic), so a partial failure never leaves a half-edited
file. Mirrors Claude Code's MultiEdit.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError, _relpath


class MultiEditTool(Tool):
    name = "multi_edit"
    description = (
        "Apply several exact find/replace edits to a single file, in order, "
        "atomically (all succeed or none are written). Each edit: "
        "{old_string, new_string, replace_all?}. Prefer this over multiple "
        "edit_file calls when changing one file in several places."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File to edit."},
            "edits": {
                "type": "array",
                "description": "Edits applied in sequence to the file's text.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {"type": "string"},
                        "new_string": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["path", "edits"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        p = ctx.safe_path(args["path"])
        if not p.is_file():
            raise ToolError(f"File not found: {_relpath(p, ctx)}")
        edits = args.get("edits") or []
        if not edits:
            raise ToolError("multi_edit requires a non-empty 'edits' list.")

        original = p.read_text("utf-8", "replace")
        text = original
        applied = 0
        for i, e in enumerate(edits, start=1):
            old = e.get("old_string", "")
            new = e.get("new_string", "")
            replace_all = bool(e.get("replace_all", False))
            if old == "":
                raise ToolError(f"edit {i}: old_string must not be empty.")
            count = text.count(old)
            if count == 0:
                raise ToolError(f"edit {i}: old_string not found (no changes written).")
            if count > 1 and not replace_all:
                raise ToolError(
                    f"edit {i}: old_string is not unique ({count} matches); "
                    f"add context or set replace_all."
                )
            text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
            applied += 1

        if text == original:
            return f"No changes for {_relpath(p, ctx)}."
        if ctx.ui is not None:
            ctx.ui.show_diff(original, text, _relpath(p, ctx))
        ctx.snapshot(p, f"multi_edit {_relpath(p, ctx)}")
        p.write_text(text, encoding="utf-8")
        return f"Applied {applied} edits to {_relpath(p, ctx)}."
