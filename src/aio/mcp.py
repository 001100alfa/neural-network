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
import urllib.error
import urllib.request
from typing import Any

from .tools.base import Tool, ToolContext, ToolError

PROTOCOL_VERSION = "2024-11-05"


def _result_text(result: dict[str, Any]) -> str:
    parts = []
    for block in result.get("content", []):
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
        else:
            parts.append(json.dumps(block))
    return "\n".join(parts)


class MCPServer:
    """A single MCP server speaking JSON-RPC over stdio or HTTP.

    stdio: give ``command``/``args`` (a subprocess). HTTP (#4): give ``url`` —
    each request is an HTTP POST (the modern "streamable HTTP" transport).
    """

    def __init__(self, name: str, command: str | None = None, args: list[str] | None = None,
                 env: dict[str, str] | None = None, timeout: float = 60.0,
                 url: str | None = None, headers: dict[str, str] | None = None) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.env = env
        self.timeout = timeout
        self.url = url
        self.headers = headers or {}
        self.transport = "http" if url else "stdio"
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self.transport == "stdio":
            import os

            full_env = dict(os.environ)
            if self.env:
                full_env.update(self.env)
            assert self.command is not None  # stdio transport always has a command
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, env=full_env, bufsize=1,
            )
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aio", "version": "0.1.3"},
            },
        )
        self._notify("notifications/initialized", {})

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # -- transport --------------------------------------------------------
    def _notify(self, method: str, params: dict[str, Any]) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if self.transport == "stdio":
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        # HTTP notifications are best-effort fire-and-forget
        else:  # pragma: no cover - network
            try:
                self._http_post(payload)
            except Exception:
                pass

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            req_id = self._next_id()
            payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            if self.transport == "http":  # pragma: no cover - network
                msg = self._http_post(payload)
                if msg is None:
                    raise ToolError(f"MCP '{self.name}': empty HTTP response.")
                if "error" in msg:
                    raise ToolError(f"MCP '{self.name}' error: {msg['error']}")
                return msg.get("result", {})
            # stdio
            assert self._proc and self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
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

    def _http_post(self, payload: dict[str, Any]):  # pragma: no cover - network
        headers = {"content-type": "application/json",
                   "accept": "application/json, text/event-stream"}
        headers.update(self.headers)
        assert self.url is not None  # only called on the http transport
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", "replace").strip()
        except urllib.error.URLError as exc:
            raise ToolError(f"MCP '{self.name}' HTTP error: {exc}") from exc
        if not body:
            return None
        # response may be plain JSON or an SSE 'data:' frame
        if body.startswith("data:"):
            for ln in body.splitlines():
                if ln.startswith("data:"):
                    body = ln[5:].strip()
                    break
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    # -- MCP methods ------------------------------------------------------
    def list_tools(self) -> list[dict[str, Any]]:
        return self._request("tools/list", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        text = _result_text(result)
        if result.get("isError"):
            raise ToolError(text or "MCP tool returned an error.")
        return text

    def list_resources(self) -> list[dict[str, Any]]:
        try:
            return self._request("resources/list", {}).get("resources", [])
        except ToolError:
            return []

    def read_resource(self, uri: str) -> str:
        result = self._request("resources/read", {"uri": uri})
        parts = []
        for c in result.get("contents", []):
            parts.append(c.get("text", "") or c.get("blob", ""))
        return "\n".join(p for p in parts if p)

    def list_prompts(self) -> list[dict[str, Any]]:
        try:
            return self._request("prompts/list", {}).get("prompts", [])
        except ToolError:
            return []

    def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = self._request("prompts/get", {"name": name, "arguments": arguments or {}})
        parts = []
        for m in result.get("messages", []):
            c = m.get("content", {})
            parts.append(c.get("text", "") if isinstance(c, dict) else str(c))
        return "\n".join(p for p in parts if p)

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
        url = cfg.get("url")
        if not name or (not command and not url):
            continue
        server = MCPServer(
            name=name,
            command=command,
            args=cfg.get("args", []),
            env=cfg.get("env"),
            url=url,
            headers=cfg.get("headers"),
        )
        try:
            server.start()
            specs = server.list_tools()
            for spec in specs:
                tools.append(MCPTool(server, spec))
            servers.append(server)
            if ui is not None:
                extra = []
                nres, npr = len(server.list_resources()), len(server.list_prompts())
                if nres:
                    extra.append(f"{nres} resources")
                if npr:
                    extra.append(f"{npr} prompts")
                suffix = (", " + ", ".join(extra)) if extra else ""
                ui.info(f"mcp: connected '{name}' ({server.transport}, {len(specs)} tools{suffix})")
        except Exception as exc:  # pragma: no cover - depends on external server
            if ui is not None:
                ui.warn(f"mcp: failed to start '{name}': {exc}")
            server.stop()
    return tools, servers
