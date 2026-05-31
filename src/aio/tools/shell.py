"""Shell execution tool."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Tool, ToolContext, ToolError

MAX_OUTPUT = 30_000


def smart_truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    """Keep the head and tail of long output, dropping the middle (#6).

    Tool output is most informative at the start (what ran) and end (result /
    error), so when over ``limit`` we keep ~60% head + ~40% tail and note how
    many characters were elided.
    """
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    tail = limit - head
    elided = len(text) - head - tail
    return (
        text[:head]
        + f"\n... [{elided} chars elided] ...\n"
        + text[-tail:]
    )


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
        # Stream output through a bounded head+tail buffer so a chatty command
        # (e.g. a verbose build) can't blow up memory (#3). We keep at most
        # ~2x MAX_OUTPUT in memory and smart-truncate the final result (#6).
        import collections

        cap = MAX_OUTPUT * 2
        head: list[str] = []
        head_len = 0
        tail = collections.deque()  # (chunk, len) for the most recent bytes
        tail_len = 0

        def absorb(chunk: str) -> None:
            nonlocal head_len, tail_len
            if head_len < cap:
                take = chunk[: cap - head_len]
                head.append(take)
                head_len += len(take)
                chunk = chunk[len(take):]
            if not chunk:
                return
            tail.append(chunk)
            tail_len += len(chunk)
            while tail_len > cap and len(tail) > 1:
                old = tail.popleft()
                tail_len -= len(old)

        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=str(ctx.workdir),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
            )
            try:
                assert proc.stdout is not None
                import time as _time
                start = _time.time()
                for line in proc.stdout:
                    absorb(line)
                    if _time.time() - start > timeout:
                        proc.kill()
                        raise ToolError(f"Command timed out after {timeout}s: {command}")
                proc.wait(timeout=5)
            finally:
                if proc.stdout:
                    proc.stdout.close()
        except FileNotFoundError as exc:
            raise ToolError(f"command not found: {exc}")

        out = "".join(head) + ("".join(tail) if tail_len else "")
        out = smart_truncate(out, MAX_OUTPUT)
        status = f"[exit code: {proc.returncode}]"
        return f"{status}\n{out}".strip()
