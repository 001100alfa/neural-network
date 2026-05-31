"""Tests for hooks (#6) and the sub-agent/task tool (#5)."""

from __future__ import annotations

from aio.agent import Agent
from aio.hooks import HookRunner
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.tools.task import TaskTool
from aio.web import EventUI


def test_pretooluse_hook_blocks_on_nonzero(tmp_path):
    hooks = HookRunner([{"event": "PreToolUse", "command": "exit 1"}], tmp_path)
    res = hooks.run("PreToolUse", "write_file", {"path": "a"})
    assert res.blocked is True


def test_pretooluse_matcher_and_pass(tmp_path):
    # matcher only fires for edit_file; write_file passes
    hooks = HookRunner([{"event": "PreToolUse", "matcher": "edit_file", "command": "exit 1"}], tmp_path)
    assert hooks.run("PreToolUse", "write_file", {}).blocked is False
    assert hooks.run("PreToolUse", "edit_file", {}).blocked is True


def test_posttooluse_hook_runs_and_cannot_block(tmp_path):
    hooks = HookRunner([{"event": "PostToolUse", "command": "echo done"}], tmp_path)
    res = hooks.run("PostToolUse", "write_file", {}, result="ok")
    assert res.blocked is False and any("done" in o for o in res.outputs)


def test_agent_blocks_tool_when_pretooluse_fails(tmp_path):
    """A PreToolUse hook that exits non-zero prevents the write from happening."""
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    hooks = HookRunner([{"event": "PreToolUse", "matcher": "write_file", "command": "exit 3"}], tmp_path)

    class P:
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[
                    ToolCall(id="1", name="write_file",
                             arguments={"path": "x.txt", "content": "hi"})])
            return AssistantTurn(content="stopped")

    ag = Agent(P(), default_registry(), ctx, ui, "sys", max_steps=5, hooks=hooks)
    ag.run("write x")
    assert not (tmp_path / "x.txt").exists()      # hook blocked the write
    assert any(e["type"] == "tool_result" and e.get("error") for e in ui.events)


def test_task_tool_registered_and_spawns_subagent(tmp_path):
    assert "task" in default_registry().names()

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)

    class P:
        """Parent calls task(); sub-agent just answers; parent then finishes."""
        def __init__(self): self.n = 0
        def chat(self, messages, tools=None, system=None):
            self.n += 1
            # the sub-agent's first turn: it gets the prompt as the only user msg
            if any("explore" in (m.content or "") for m in messages if m.role == "user") \
               and not any(m.role == "assistant" for m in messages):
                return AssistantTurn(content="sub-agent report: all good")
            if self.n == 1:
                return AssistantTurn(content="", tool_calls=[
                    ToolCall(id="1", name="task",
                             arguments={"prompt": "explore the repo"})])
            return AssistantTurn(content="parent done")

    ag = Agent(P(), default_registry(), ctx, ui, "sys", max_steps=5)
    final = ag.run("delegate it")
    # the task tool's result (the sub-agent's summary) reached the parent
    tool_msgs = [m for m in ag.messages if m.role == "tool" and m.name == "task"]
    assert tool_msgs and "sub-agent report" in tool_msgs[0].content
    assert final == "parent done"


def test_task_tool_requires_spawn(tmp_path):
    ctx = ToolContext(workdir=tmp_path)  # no spawn_subagent wired
    import pytest as _pt
    with _pt.raises(Exception):
        TaskTool().run({"prompt": "do it"}, ctx)
