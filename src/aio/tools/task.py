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


class ParallelTasksTool(Tool):
    name = "parallel_tasks"
    description = (
        "Run several INDEPENDENT sub-tasks concurrently, each in its own fresh "
        "sub-agent (same tools). Use when tasks don't depend on each other (e.g. "
        "investigate three modules at once). Returns all results, labelled. Do "
        "NOT use for steps that must run in order."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "Independent prompts to run in parallel.",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Short label."},
                        "prompt": {"type": "string", "description": "Instructions for this sub-agent."},
                    },
                    "required": ["prompt"],
                },
            }
        },
        "required": ["tasks"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        import concurrent.futures

        if ctx.spawn_subagent is None:
            raise ToolError("sub-agents are not available in this context.")
        if ctx.depth >= 2:
            raise ToolError("sub-agent nesting limit reached (max depth 2).")
        tasks = args.get("tasks") or []
        prompts = [(t.get("description") or f"task {i+1}", (t.get("prompt") or "").strip())
                   for i, t in enumerate(tasks) if isinstance(t, dict) and t.get("prompt")]
        if not prompts:
            raise ToolError("parallel_tasks requires a non-empty 'tasks' list with prompts.")
        # cap fan-out to keep resource use sane
        prompts = prompts[:6]
        results: list[tuple[int, str, str]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(prompts))) as ex:
            futs = {ex.submit(ctx.spawn_subagent, p): (i, label)
                    for i, (label, p) in enumerate(prompts)}
            for fut in concurrent.futures.as_completed(futs):
                i, label = futs[fut]
                try:
                    results.append((i, label, fut.result()))
                except Exception as exc:  # pragma: no cover - defensive
                    results.append((i, label, f"(failed: {type(exc).__name__}: {exc})"))
        results.sort(key=lambda r: r[0])
        return "\n\n".join(f"## {label}\n{out}" for _, label, out in results)
