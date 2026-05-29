"""Minimal Model Context Protocol (MCP) stdio client.

Connects to MCP servers declared in configuration, lists their tools, and wraps
each remote tool as an :class:`aio.tools.base.Tool` so it can be used by the
agent exactly like a built-in tool. Implemented over JSON-RPC 2.0 on stdio with
no third-party dependencies.
"""

from __future__ import annotations

import json
import subprocess
import threading
from typing import Any

from .tools.base import Tool, ToolContext, ToolError

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """A single MCP server subprocess speaking JSON-RPC over stdio."""

    def __init__(self, name: str, command: str, args: list[str] | None = None,
                 env: dict[str, str] | None = None, timeout: float = 60.0) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        import os

        full_env = dict(os.environ)
        if self.env:
            full_env.update(self.env)
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=full_env,
            bufsize=1,
        )
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aio", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _send(self, payload: dict[str, Any]) -> None:
        assert self._proc and self._proc.stdin
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        with self._lock:
            req_id = self._next_id()
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            # Read until we get the response with the matching id.
            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise ToolError(f"MCP server '{self.name}' closed unexpectedly.")
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise ToolError(f"MCP '{self.name}' error: {msg['error']}")
                    return msg.get("result", {})

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        parts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            else:
                parts.append(json.dumps(block))
        if result.get("isError"):
            raise ToolError("\n".join(parts) or "MCP tool returned an error.")
        return "\n".join(parts)

    def stop(self) -> None:
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:  # pragma: no cover - best effort
                pass
            self._proc = None


class MCPTool(Tool):
    """Adapts a remote MCP tool to the local Tool interface."""

    def __init__(self, server: MCPServer, spec: dict[str, Any]) -> None:
        self._server = server
        self.name = f"{server.name}__{spec['name']}"
        self._remote_name = spec["name"]
        self.description = spec.get("description", "") + f" (via MCP server '{server.name}')"
        self.parameters = spec.get("inputSchema") or {"type": "object", "properties": {}}
        self.needs_approval = True  # remote side effects: confirm by default

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return self._server.call_tool(self._remote_name, args)


def load_mcp_tools(server_configs: list[dict[str, Any]], ui=None) -> tuple[list[MCPTool], list[MCPServer]]:
    """Start every configured MCP server and return their tools.

    Failures are logged (if ``ui`` provided) and skipped so a broken server
    never prevents the agent from starting.
    """

    tools: list[MCPTool] = []
    servers: list[MCPServer] = []
    for cfg in server_configs:
        name = cfg.get("name")
        command = cfg.get("command")
        if not name or not command:
            continue
        server = MCPServer(
            name=name,
            command=command,
            args=cfg.get("args", []),
            env=cfg.get("env"),
        )
        try:
            server.start()
            for spec in server.list_tools():
                tools.append(MCPTool(server, spec))
            servers.append(server)
            if ui is not None:
                ui.info(f"mcp: connected '{name}' ({len(server.list_tools())} tools)")
        except Exception as exc:  # pragma: no cover - depends on external server
            if ui is not None:
                ui.warn(f"mcp: failed to start '{name}': {exc}")
            server.stop()
    return tools, servers
