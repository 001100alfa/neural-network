"""Task-list tool (TodoWrite-style) for tracking multi-step work."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError

VALID_STATUS = {"pending", "in_progress", "completed"}


class WriteTodosTool(Tool):
    name = "write_todos"
    description = (
        "Record or update the task list for the current multi-step work. Pass the "
        "FULL list each time. Each item: {content, status}, status one of "
        "pending|in_progress|completed. Keep exactly one task in_progress. Use this "
        "to plan and track progress on non-trivial tasks."
    )
    parameters = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "The complete current task list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "What the task is."},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        raw = args.get("todos")
        if not isinstance(raw, list):
            raise ToolError("todos must be a list of {content, status}.")
        todos = []
        for item in raw:
            if not isinstance(item, dict) or "content" not in item:
                raise ToolError("each todo needs at least a 'content' field.")
            status = item.get("status", "pending")
            if status not in VALID_STATUS:
                raise ToolError(f"invalid status '{status}'; use pending|in_progress|completed.")
            todos.append({"content": str(item["content"]), "status": status})
        ctx.todos = todos
        if ctx.ui is not None and hasattr(ctx.ui, "todos_update"):
            ctx.ui.todos_update(todos)
        done = sum(1 for t in todos if t["status"] == "completed")
        return f"Updated task list ({done}/{len(todos)} done)."
