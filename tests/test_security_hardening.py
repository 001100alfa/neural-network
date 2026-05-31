"""Tests for cephe-4 hardening: secret redaction, command guardrail, agent
budget/backpressure, and the tool audit log."""

from __future__ import annotations

import json

from aio.agent import Agent
from aio.guard import dangerous_command
from aio.obs import AuditLog
from aio.providers import AssistantTurn, ToolCall
from aio.redact import redact, redact_obj
from aio.service import EventUI
from aio.tools import ToolContext, default_registry


# -- secret redaction -------------------------------------------------------

def test_redact_masks_provider_keys_and_assignments():
    assert "sk-" in redact("sk-ABCDEFGH12345678") and "ABCDEFGH12345678" not in redact("sk-ABCDEFGH12345678")
    assert "gsk_secretvalue123456" not in redact("token gsk_secretvalue123456")
    out = redact('ANTHROPIC_API_KEY=sk-supersecretvalue123')
    assert "sk-supersecretvalue123" not in out and "supersecret" not in out
    out2 = redact('Authorization: Bearer abcdef123456ghijkl')
    assert "abcdef123456ghijkl" not in out2


def test_redact_leaves_ordinary_text_alone():
    assert redact("just a normal sentence with words") == "just a normal sentence with words"
    assert redact("") == ""


def test_redact_obj_recurses():
    obj = {"key": "sk-ABCDEFGH12345678", "nested": ["plain", "password=hunter2longvalue"]}
    out = redact_obj(obj)
    assert "ABCDEFGH12345678" not in json.dumps(out)
    assert "hunter2longvalue" not in json.dumps(out)
    assert out["nested"][0] == "plain"


# -- command guardrail ------------------------------------------------------

def test_dangerous_command_blocks_catastrophes():
    assert dangerous_command("rm -rf /")
    assert dangerous_command("sudo rm -rf  --no-preserve-root /")
    assert dangerous_command(":(){ :|:& };:")
    assert dangerous_command("mkfs.ext4 /dev/sda1")
    assert dangerous_command("dd if=/dev/zero of=/dev/sda")
    assert dangerous_command("shutdown -h now")


def test_dangerous_command_allows_normal():
    assert dangerous_command("rm -rf build/") is None
    assert dangerous_command("python -m pytest -q") is None
    assert dangerous_command("git commit -m 'rm the root cause'") is None
    assert dangerous_command("") is None


def test_guardrail_blocks_in_agent_even_with_auto_approve(tmp_path):
    class P:
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[ToolCall(
                    "1", "run_shell", {"command": "rm -rf /"})])
            return AssistantTurn(content="stopped", tool_calls=[])

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(P(), default_registry(), ctx, ui, "sys", max_steps=4)
    agent.run("delete everything")
    tool_results = [m.content for m in agent.messages if m.role == "tool"]
    assert tool_results and "refused" in tool_results[0] and "guardrail" in tool_results[0]


# -- agent budget / backpressure --------------------------------------------

class _LoopForever:
    """Always asks to run a harmless command — never stops on its own."""

    def chat(self, messages, tools=None, system=None):
        return AssistantTurn(content="again", tool_calls=[ToolCall(
            "x", "run_shell", {"command": "echo hi"})])


def test_max_tool_calls_budget_stops_a_runaway(tmp_path):
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(_LoopForever(), default_registry(), ctx, ui, "sys",
                  max_steps=100, max_tool_calls=3)
    agent.run("loop")
    executed = [m for m in agent.messages if m.role == "tool"]
    assert len(executed) == 3            # stopped at the budget, not max_steps
    assert any("budget" in e.get("text", "") for e in ui.events if e["type"] == "warn")


def test_deadline_budget_stops_a_runaway(tmp_path):
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(_LoopForever(), default_registry(), ctx, ui, "sys",
                  max_steps=100, deadline_s=0.0001)
    agent.run("loop")
    # the deadline trips almost immediately -> far fewer than 100 iterations
    assert len([m for m in agent.messages if m.role == "tool"]) < 100


# -- audit log --------------------------------------------------------------

def test_audit_log_records_redacted_tool_runs(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")

    class P:
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[ToolCall(
                    "1", "write_file", {"path": "f.txt", "content": "key sk-ABCDEFGH12345678"})])
            return AssistantTurn(content="done", tool_calls=[])

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True, audit=audit.record)
    Agent(P(), default_registry(), ctx, ui, "sys", max_steps=4).run("write")

    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    entries = [json.loads(ln) for ln in lines]
    wf = [e for e in entries if e["tool"] == "write_file"]
    assert wf and wf[0]["status"] == "ok"
    # the secret in the args must be redacted in the audit trail
    assert "ABCDEFGH12345678" not in (tmp_path / "audit.jsonl").read_text()
