"""Shell execution tool."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Tool, ToolContext, ToolError

MAX_OUTPUT = 30_000


class RunShellTool(Tool):
    name = "run_shell"
    description = (
        "Run a shell command in the working directory and return combined "
        "stdout/stderr. Use for builds, tests, installs, and inspection. "
        "Avoid long-running or interactive commands."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to execute."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)."},
        },
        "required": ["command"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        command = args["command"]
        timeout = int(args.get("timeout", 120))
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(ctx.workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Command timed out after {timeout}s: {command}")

        out = (proc.stdout or "") + (proc.stderr or "")
        if len(out) > MAX_OUTPUT:
            out = out[:MAX_OUTPUT] + f"\n... (output truncated at {MAX_OUTPUT} chars)"
        status = f"[exit code: {proc.returncode}]"
        return f"{status}\n{out}".strip()
