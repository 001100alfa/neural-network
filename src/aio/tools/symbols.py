"""find_symbol tool (#K) — jump to where a function/class/method is defined.

Backed by a lazily-built, cached :class:`aio.codeindex.CodeIndex` so the agent
can navigate the codebase by symbol name instead of grepping. Much faster and
more precise than text search for "where is X defined".
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolContext, ToolError


class FindSymbolTool(Tool):
    name = "find_symbol"
    description = (
        "Find where a function, class or method is DEFINED across the codebase "
        "by name (exact or substring), returning file:line locations. Use this "
        "to navigate to a definition instead of grepping. Supports 'Class.method'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Symbol name to find (e.g. 'build_provider' or 'Agent.run')."},
            "rebuild": {"type": "boolean", "description": "Force a fresh index scan."},
        },
        "required": ["name"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        name = (args.get("name") or "").strip()
        if not name:
            raise ToolError("find_symbol requires a 'name'.")
        if ctx.code_index is None or args.get("rebuild"):
            from ..codeindex import CodeIndex

            ctx.code_index = CodeIndex(ctx.workdir).build()
        hits = ctx.code_index.lookup(name)
        if not hits:
            return f"No symbol matching {name!r} found ({ctx.code_index.n_symbols} symbols indexed)."
        lines = [f"{h['path']}:{h['line']}  {h['kind']} {h['symbol']}" for h in hits]
        return "\n".join(lines)
