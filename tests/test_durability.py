"""Conversation durability: live working state survives a process restart.

Conversations used to live only in memory, so a crash lost any unsaved chat.
These tests simulate "restart" by building a second AgentService against the
same sessions dir and asserting the tabs come back — and that reset/close
purge the persisted copy.
"""

from __future__ import annotations

from pathlib import Path

from aio.providers import AssistantTurn


class _Echo:
    """Minimal provider: replies once with no tool calls."""

    def chat(self, messages, tools=None, system=None):
        return AssistantTurn(content="noted.", tool_calls=[])


def _service(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config
    from aio.service import AgentService

    svc = AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))
    svc.agent.provider = _Echo()   # shared into per-conversation agents
    return svc


def test_conversation_survives_restart(tmp_path, monkeypatch):
    svc1 = _service(tmp_path, monkeypatch)
    svc1.chat("remember 42", conv_id="work")

    # "restart": a brand-new service over the same sessions dir
    svc2 = _service(tmp_path, monkeypatch)
    assert "work" in svc2.conversations
    restored = svc2.conversations["work"]
    assert any("remember 42" in (m.content or "") for m in restored)
    assert any(m.role == "assistant" and "noted" in (m.content or "") for m in restored)


def test_reset_purges_persisted_conversation(tmp_path, monkeypatch):
    svc1 = _service(tmp_path, monkeypatch)
    svc1.chat("temporary", conv_id="scratch")
    svc1.reset("scratch")

    svc2 = _service(tmp_path, monkeypatch)
    # an emptied conversation is not restored as a live tab
    assert "scratch" not in svc2.conversations


def test_close_purges_persisted_conversation(tmp_path, monkeypatch):
    svc1 = _service(tmp_path, monkeypatch)
    svc1.chat("bye", conv_id="temp")
    svc1.close_conversation("temp")

    svc2 = _service(tmp_path, monkeypatch)
    assert "temp" not in svc2.conversations


def test_streaming_turn_is_also_persisted(tmp_path, monkeypatch):
    svc1 = _service(tmp_path, monkeypatch)

    class _Tok:
        def stream_chat(self, messages, tools=None, system=None, on_delta=None):
            if on_delta:
                on_delta("hi")
            return AssistantTurn(content="hi", tool_calls=[])

    svc1.agent.provider = _Tok()
    svc1.chat_stream("stream me", lambda e: None, conv_id="s1")

    svc2 = _service(tmp_path, monkeypatch)
    assert "s1" in svc2.conversations
    assert any("stream me" in (m.content or "") for m in svc2.conversations["s1"])
