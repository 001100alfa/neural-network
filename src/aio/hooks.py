"""Lightweight tool hooks (PreToolUse / PostToolUse).

A hook is a shell command run around a tool call. Configure them in
``.aio.toml`` or via the key store:

    [[hooks]]
    event = "PreToolUse"        # or "PostToolUse"
    matcher = "write_file|edit_file"   # regex over the tool name (optional)
    command = "ruff check ."     # shell command to run

The tool call's JSON ({"name","arguments","result"?}) is piped to the command on
stdin and also exposed as $AIO_TOOL / $AIO_TOOL_ARGS. A PreToolUse hook that
exits non-zero **blocks** the tool call (its stderr/stdout becomes the reason).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass


@dataclass
class Hook:
    event: str
    command: str
    matcher: str = ""  # regex over tool name; empty = match all

    def matches(self, event: str, tool_name: str) -> bool:
        if self.event != event:
            return False
        if not self.matcher:
            return True
        try:
            return re.search(self.matcher, tool_name) is not None
        except re.error:
            return self.matcher in tool_name


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""
    outputs: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.outputs is None:
            self.outputs = []


class HookRunner:
    def __init__(self, hooks: list[dict] | None, workdir, timeout: float = 60.0):
        self.hooks = [
            Hook(event=h.get("event", ""), command=h.get("command", ""),
                 matcher=h.get("matcher", ""))
            for h in (hooks or []) if h.get("command")
        ]
        self.workdir = str(workdir)
        self.timeout = timeout

    def has(self, event: str) -> bool:
        return any(h.event == event for h in self.hooks)

    def run(self, event: str, tool_name: str, arguments: dict, result: str | None = None) -> HookResult:
        out = HookResult()
        payload = json.dumps({"name": tool_name, "arguments": arguments, "result": result})
        import os

        for h in self.hooks:
            if not h.matches(event, tool_name):
                continue
            env = dict(os.environ)
            env["AIO_TOOL"] = tool_name
            env["AIO_TOOL_ARGS"] = json.dumps(arguments)
            env["AIO_HOOK_EVENT"] = event
            try:
                proc = subprocess.run(
                    h.command, shell=True, cwd=self.workdir, input=payload,
                    capture_output=True, text=True, timeout=self.timeout, env=env,
                )
            except subprocess.TimeoutExpired:
                out.outputs.append(f"hook '{h.command}' timed out")
                continue
            text = ((proc.stdout or "") + (proc.stderr or "")).strip()
            if text:
                out.outputs.append(f"[{event}] {h.command}: {text[:500]}")
            if event == "PreToolUse" and proc.returncode != 0:
                out.blocked = True
                out.reason = text or f"hook '{h.command}' exited {proc.returncode}"
                break
        return out
