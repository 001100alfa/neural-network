"""Background command tools (#4).

``run_background`` launches a long-running shell command without blocking the
agent (output is captured to a temp file); ``check_background`` reports its
captured output and whether it is still running. Lets the agent start a dev
server / watcher / long build and keep working.
"""

from __future__ import annotations

import subprocess
import tempfile
from typing import Any

from .base import Tool, ToolContext, ToolError

MAX_OUTPUT = 20_000


class RunBackgroundTool(Tool):
    name = "run_background"
    description = (
        "Start a long-running shell command in the background (e.g. a dev server "
        "or watcher) without blocking. Returns a job id; use check_background to "
        "read its output or stop it."
    )
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run in the background."},
        },
        "required": ["command"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        command = (args.get("command") or "").strip()
        if not command:
            raise ToolError("command is required.")
        log = tempfile.NamedTemporaryFile(prefix="aio-bg-", suffix=".log", delete=False)
        proc = subprocess.Popen(
            command, shell=True, cwd=str(ctx.workdir),
            stdout=log, stderr=subprocess.STDOUT, text=True,
        )
        jid = f"bg{len(ctx.background) + 1}"
        ctx.background[jid] = {"proc": proc, "command": command, "log": log.name}
        return f"started {jid}: {command} (use check_background id={jid})"


class CheckBackgroundTool(Tool):
    name = "check_background"
    description = (
        "Check a background job started with run_background: returns its recent "
        "output and whether it's still running. Optionally stop it."
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "The job id (e.g. bg1). Omit to list all."},
            "stop": {"type": "boolean", "description": "Terminate the job."},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        jid = args.get("id")
        if not jid:
            if not ctx.background:
                return "no background jobs."
            return "background jobs: " + ", ".join(
                f"{k} ({'running' if j['proc'].poll() is None else 'exited'})"
                for k, j in ctx.background.items()
            )
        job = ctx.background.get(jid)
        if job is None:
            raise ToolError(f"unknown background job '{jid}'.")
        proc = job["proc"]
        if args.get("stop") and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
        running = proc.poll() is None
        try:
            with open(job["log"], "r", encoding="utf-8", errors="replace") as fh:
                out = fh.read()
        except OSError:
            out = ""
        if len(out) > MAX_OUTPUT:
            out = "... " + out[-MAX_OUTPUT:]
        status = "running" if running else f"exited (code {proc.returncode})"
        return f"[{jid}: {status}]\n{out}".strip()
