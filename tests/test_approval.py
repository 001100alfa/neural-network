"""Tool-approval gate: side-effecting tools require explicit approval in
gated web mode, instead of being auto-run.

Covers the ApprovalBroker in isolation, EventUI.confirm's gated flow, and the
real end-to-end path where a streaming turn blocks until the user approves or
denies a write_file over a second thread.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from aio.providers import AssistantTurn, ToolCall
from aio.security import ApprovalBroker
from aio.service import AgentService, EventUI


# -- ApprovalBroker ---------------------------------------------------------

def test_broker_resolve_unblocks_with_decision():
    b = ApprovalBroker()
    rid = b.open()
    out = {}
    t = threading.Thread(target=lambda: out.update(d=b.wait(rid)))
    t.start()
    time.sleep(0.05)               # ensure the waiter is parked
    assert b.resolve(rid, "always") is True
    t.join(2)
    assert out["d"] == "always"


def test_broker_times_out_to_deny():
    b = ApprovalBroker(timeout=0.1)
    rid = b.open()
    assert b.wait(rid) == "no"     # nobody answered -> fail closed


def test_broker_unknown_and_invalid():
    b = ApprovalBroker()
    assert b.resolve("nope", "yes") is False         # unknown request
    rid = b.open()
    assert b.resolve(rid, "garbage") is True
    assert b.wait(rid) == "no"                        # invalid decision -> deny


# -- EventUI.confirm --------------------------------------------------------

def test_eventui_confirm_auto_approves_without_broker_or_sink():
    assert EventUI().confirm("write_file", {}) == "yes"          # no broker
    ui = EventUI(ApprovalBroker())
    assert ui.confirm("write_file", {}) == "yes"                 # broker but no live sink


def test_eventui_confirm_gates_over_a_live_sink():
    broker = ApprovalBroker()
    ui = EventUI(broker)
    events = []
    ui.sink = events.append
    out = {}
    t = threading.Thread(target=lambda: out.update(d=ui.confirm("write_file", {"path": "x"})))
    t.start()
    rid = _await(lambda: next((e["id"] for e in events if e["type"] == "tool_approval"), None))
    broker.resolve(rid, "yes")
    t.join(2)
    assert out["d"] == "yes"
    assert any(e["type"] == "tool_approval_resolved" and e["decision"] == "yes" for e in events)


# -- service end-to-end -----------------------------------------------------

def _await(get, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = get()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("condition not met in time")


def _gated_service(tmp_path: Path, monkeypatch) -> AgentService:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config

    return AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))  # gated by default


class _ToolThenDone:
    """Streaming provider: request a write_file once, then finish."""

    def __init__(self, call):
        self.call = call
        self.n = 0

    def stream_chat(self, messages, tools=None, system=None, on_delta=None):
        self.n += 1
        if self.n == 1:
            return AssistantTurn(content="", tool_calls=[self.call])
        return AssistantTurn(content="done", tool_calls=[])


def _run_turn(svc, events):
    done = threading.Event()
    threading.Thread(
        target=lambda: (svc.chat_stream("write a file", events.append), done.set()),
        daemon=True,
    ).start()
    return done


def test_gated_default_is_reported(tmp_path, monkeypatch):
    svc = _gated_service(tmp_path, monkeypatch)
    assert svc.gated is True
    assert svc.info()["tool_approval"] == "gated"


def test_approval_yes_runs_the_tool(tmp_path, monkeypatch):
    svc = _gated_service(tmp_path, monkeypatch)
    svc.agent.provider = _ToolThenDone(
        ToolCall(id="1", name="write_file", arguments={"path": "out.txt", "content": "hi"}))
    events: list = []
    done = _run_turn(svc, events)
    # the turn must block on approval, not run write_file yet
    rid = _await(lambda: next((e["id"] for e in events if e["type"] == "tool_approval"), None))
    assert not (tmp_path / "out.txt").exists()      # still gated, nothing written
    svc.resolve_approval(rid, "yes")                # approve from this (other) thread
    assert done.wait(5)
    assert (tmp_path / "out.txt").read_text() == "hi"
    assert any(e["type"] == "tool_result" and not e.get("error") for e in events)


def test_approval_deny_blocks_the_tool(tmp_path, monkeypatch):
    svc = _gated_service(tmp_path, monkeypatch)
    svc.agent.provider = _ToolThenDone(
        ToolCall(id="1", name="write_file", arguments={"path": "no.txt", "content": "x"}))
    events: list = []
    done = _run_turn(svc, events)
    rid = _await(lambda: next((e["id"] for e in events if e["type"] == "tool_approval"), None))
    svc.resolve_approval(rid, "no")
    assert done.wait(5)
    assert not (tmp_path / "no.txt").exists()       # denied -> never written


def test_readonly_tool_is_not_gated(tmp_path, monkeypatch):
    (tmp_path / "r.txt").write_text("readable")
    svc = _gated_service(tmp_path, monkeypatch)
    svc.agent.provider = _ToolThenDone(
        ToolCall(id="1", name="read_file", arguments={"path": "r.txt"}))
    events: list = []
    done = _run_turn(svc, events)
    # read_file has needs_approval=False -> no approval event, runs immediately
    assert done.wait(5)
    assert not any(e["type"] == "tool_approval" for e in events)
    assert any(e["type"] == "tool_result" and "readable" in e.get("text", "") for e in events)
