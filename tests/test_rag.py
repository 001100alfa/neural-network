"""Retrieval-augmented context: relevant code is auto-injected each turn.

search_code already let the model *choose* to search; this wires retrieval into
the loop so the model starts each turn already grounded in the most relevant
code — the Cursor-like behaviour. Retrieval is BM25 (lexical), injected into the
system prompt transiently (not persisted into the message history).
"""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn
from aio.service import EventUI
from aio.tools import ToolContext, default_registry


class _Capture:
    """Records the system prompt it receives; never calls a tool."""

    def __init__(self):
        self.systems = []

    def chat(self, messages, tools=None, system=None):
        self.systems.append(system or "")
        return AssistantTurn(content="ok", tool_calls=[])


def _repo(tmp_path):
    (tmp_path / "retry.py").write_text(
        "def with_backoff(fn):\n"
        "    # retry with exponential backoff on rate limit errors\n"
        "    for i in range(5):\n        sleep(2 ** i)\n")
    (tmp_path / "ui.py").write_text(
        "def render_button(label):\n    return f'<button>{label}</button>'\n")
    return tmp_path


def _agent(tmp_path, **kw):
    return Agent(_Capture(), default_registry(), ToolContext(workdir=tmp_path),
                 EventUI(), "SYS", **kw)


def test_auto_context_injects_relevant_code(tmp_path):
    _repo(tmp_path)
    agent = _agent(tmp_path, auto_context=True, auto_context_k=3)
    agent.run("exponential backoff on rate limit")
    sys = agent.provider.systems[0]
    assert "Retrieved code context" in sys
    assert "retry.py" in sys and "backoff" in sys
    assert "ui.py" not in sys                       # the irrelevant file isn't pulled in


def test_auto_context_off_does_not_inject(tmp_path):
    _repo(tmp_path)
    agent = _agent(tmp_path)                         # auto_context defaults False
    agent.run("exponential backoff on rate limit")
    assert "Retrieved code context" not in agent.provider.systems[0]


def test_auto_context_not_persisted_to_messages(tmp_path):
    _repo(tmp_path)
    agent = _agent(tmp_path, auto_context=True)
    agent.run("backoff")
    # the injected context lives only in the system prompt, not the transcript
    assert all("Retrieved code context" not in (m.content or "") for m in agent.messages)


def test_auto_context_no_match_injects_nothing(tmp_path):
    _repo(tmp_path)
    agent = _agent(tmp_path, auto_context=True)
    agent.run("zzzcompletelyunrelatedterm")
    assert "Retrieved code context" not in agent.provider.systems[0]


def test_auto_context_reflects_latest_query_and_caches_index(tmp_path):
    _repo(tmp_path)
    agent = _agent(tmp_path, auto_context=True, auto_context_k=2)
    agent.run("backoff retry")
    searcher = agent.ctx.code_searcher
    assert searcher is not None                      # built and cached on the context
    agent.run("render button label")
    assert agent.ctx.code_searcher is searcher       # reused, not rebuilt
    # the second turn's context reflects the second query
    assert "ui.py" in agent.provider.systems[1]


def test_config_default_on_and_env_opt_out(tmp_path, monkeypatch):
    from aio.config import load_config

    monkeypatch.delenv("AIO_NO_AUTO_CONTEXT", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic"})
    assert cfg.auto_context is True

    monkeypatch.setenv("AIO_NO_AUTO_CONTEXT", "1")
    cfg2 = load_config(workdir=tmp_path, overrides={"provider": "anthropic"})
    assert cfg2.auto_context is False
