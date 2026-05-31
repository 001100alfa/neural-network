"""Tests for MCP resources/prompts (#1), HTTP transport plumbing (#4),
and cache-token accounting (#2)."""

from __future__ import annotations

from aio.agent import Agent
from aio.mcp import MCPServer, load_mcp_tools
from aio.providers import AssistantTurn
from aio.tools import ToolContext, default_registry
from aio.web import EventUI

MCP_FULL = "/tmp/mcp_full.py"


def _have_full_server() -> bool:
    import os
    return os.path.exists(MCP_FULL)


def test_stdio_server_resources_and_prompts():
    if not _have_full_server():
        import pytest
        pytest.skip("mcp_full.py fixture not present")
    s = MCPServer(name="full", command="python3", args=[MCP_FULL])
    s.start()
    try:
        assert [t["name"] for t in s.list_tools()] == ["ping"]
        res = s.list_resources()
        assert res and res[0]["uri"] == "mem://note"
        assert s.read_resource("mem://note") == "RESOURCE BODY"
        prompts = s.list_prompts()
        assert prompts and prompts[0]["name"] == "greet"
        assert s.get_prompt("greet") == "PROMPT BODY"
        assert s.call_tool("ping", {}) == "pong"
    finally:
        s.stop()


def test_load_mcp_tools_with_resources(tmp_path):
    if not _have_full_server():
        import pytest
        pytest.skip("mcp_full.py fixture not present")
    ui = EventUI()
    tools, servers = load_mcp_tools(
        [{"name": "full", "command": "python3", "args": [MCP_FULL]}], ui=ui
    )
    try:
        assert any(t.name == "full__ping" for t in tools)
        # the connect log mentions resources + prompts
        info = " ".join(e.get("text", "") for e in ui.events)
        assert "resources" in info and "prompts" in info
    finally:
        for s in servers:
            s.stop()


def test_http_transport_selected():
    s = MCPServer(name="remote", url="https://example.com/mcp")
    assert s.transport == "http" and s.command is None


def test_cache_tokens_tracked_in_run_usage(tmp_path):
    class P:
        def chat(self, messages, tools=None, system=None):
            return AssistantTurn(content="ok", usage={
                "input_tokens": 10, "output_tokens": 5,
                "cache_read_input_tokens": 100, "cache_creation_input_tokens": 40,
            })

    ui = EventUI()
    ag = Agent(P(), default_registry(), ToolContext(workdir=tmp_path, ui=ui), ui, "sys")
    ag.run("hi")
    assert ag.run_usage["cache_read"] == 100
    assert ag.run_usage["cache_write"] == 40
