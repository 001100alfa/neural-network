"""Sub-agent / Task tool (#5).

Delegates a self-contained piece of work to a fresh agent that has the same
tools and provider but its own clean conversation. Returns only the sub-agent's
final summary to the parent, keeping the parent's context small.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError


class TaskTool(Tool):
    name = "task"
    description = (
        "Delegate a self-contained sub-task to a fresh sub-agent with its own "
        "clean context (same tools). Use for focused work like 'explore the repo "
        "and report how X works' or a well-scoped change, when you want to keep "
        "your own context small. Returns the sub-agent's final summary only."
    )
    parameters = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "Short task title (a few words)."},
            "prompt": {"type": "string", "description": "The full instructions for the sub-agent."},
        },
        "required": ["prompt"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if ctx.spawn_subagent is None:
            raise ToolError("sub-agents are not available in this context.")
        if ctx.depth >= 2:
            raise ToolError("sub-agent nesting limit reached (max depth 2).")
        prompt = (args.get("prompt") or "").strip()
        if not prompt:
            raise ToolError("task requires a 'prompt'.")
        return ctx.spawn_subagent(prompt)
