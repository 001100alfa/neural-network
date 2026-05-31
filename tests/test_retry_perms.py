"""Tests for request retry/backoff (#8) and granular permissions (#7)."""

from __future__ import annotations

import urllib.error

import pytest

from aio.agent import Agent
from aio.providers import AssistantTurn, ProviderError, ToolCall
from aio.providers.openai_compat import OpenAICompatProvider
from aio.tools import ToolContext, default_registry
from aio.web import EventUI


def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def test_send_retries_then_succeeds(monkeypatch):
    p = OpenAICompatProvider(model="m", api_key="k", max_retries=3)
    calls = {"n": 0}
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise _http_error(503)  # always transient

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError):
        p._send(urllib.request.Request("http://x"), "http://x")
    # 1 initial + 3 retries = 4 attempts, with 3 backoff sleeps (2,4,8)
    assert calls["n"] == 4
    assert sleeps == [1, 2, 4]  # exponential backoff 2**0, 2**1, 2**2


def test_no_retry_on_400(monkeypatch):
    p = OpenAICompatProvider(model="m", api_key="k", max_retries=3)
    calls = {"n": 0}
    monkeypatch.setattr("time.sleep", lambda s: None)

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise _http_error(400)

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ProviderError):
        p._send(urllib.request.Request("http://x"), "http://x")
    assert calls["n"] == 1  # 400 is not retried


def test_permission_deny_blocks_tool(tmp_path):
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True,
                      permissions={"git_commit": "deny"})

    class P:
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[
                    ToolCall(id="1", name="git_commit", arguments={"message": "x"})])
            return AssistantTurn(content="done")

    ag = Agent(P(), default_registry(), ctx, ui, "sys", max_steps=4)
    ag.run("commit it")
    tool_msg = next(m for m in ag.messages if m.role == "tool")
    assert "denied" in tool_msg.content and "git_commit" in tool_msg.content


def test_permission_allow_skips_prompt(tmp_path):
    """allow rule runs a needs_approval tool without calling ui.confirm."""
    ui = EventUI()
    confirmed = {"n": 0}
    ui.confirm = lambda *a, **k: (confirmed.__setitem__("n", confirmed["n"] + 1) or "yes")  # type: ignore
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=False,
                      permissions={"write_file": "allow"})

    class P:
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[
                    ToolCall(id="1", name="write_file",
                             arguments={"path": "a.txt", "content": "hi"})])
            return AssistantTurn(content="done")

    ag = Agent(P(), default_registry(), ctx, ui, "sys", max_steps=4)
    ag.run("write it")
    assert (tmp_path / "a.txt").read_text() == "hi"
    assert confirmed["n"] == 0  # allow rule skipped the approval prompt
