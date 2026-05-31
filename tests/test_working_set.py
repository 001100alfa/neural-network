"""Tests for multi-file context: the agent tracks the files it reads/edits and
keeps that working set in front of the model each turn."""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.service import EventUI
from aio.tools import ToolContext, default_registry


def test_touch_file_dedupes_and_orders():
    ctx = ToolContext(workdir=".")
    for f in ["a.py", "b.py", "a.py", "c.py"]:
        ctx.touch_file(f)
    assert ctx.working_set == ["b.py", "a.py", "c.py"]   # deduped, most-recent last


def test_touch_file_caps_at_twelve():
    ctx = ToolContext(workdir=".")
    for i in range(20):
        ctx.touch_file(f"f{i}.py")
    assert len(ctx.working_set) == 12 and ctx.working_set[-1] == "f19.py"


def test_read_and_edit_populate_working_set(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 2\n")
    from aio.tools.files import EditFileTool, ReadFileTool, WriteFileTool
    ctx = ToolContext(workdir=tmp_path)
    ReadFileTool().run({"path": "a.py"}, ctx)
    EditFileTool().run({"path": "b.py", "old_string": "B = 2", "new_string": "B = 3"}, ctx)
    WriteFileTool().run({"path": "c.py", "content": "C = 4\n"}, ctx)
    assert ctx.working_set == ["a.py", "b.py", "c.py"]


def test_agent_injects_working_set_into_system_prompt(tmp_path):
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 2\n")

    class P:
        def __init__(self): self.n = 0; self.systems = []
        def chat(self, msgs, tools=None, system=None):
            self.systems.append(system or ""); self.n += 1
            if self.n == 1:
                return AssistantTurn(content="read a", tool_calls=[
                    ToolCall("1", "read_file", {"path": "a.py"})])
            if self.n == 2:
                return AssistantTurn(content="edit b", tool_calls=[
                    ToolCall("2", "edit_file", {"path": "b.py", "old_string": "B = 2", "new_string": "B = 3"})])
            return AssistantTurn(content="done")

    prov = P()
    ctx = ToolContext(workdir=tmp_path, ui=EventUI(), auto_approve=True)
    agent = Agent(prov, default_registry(), ctx, EventUI(), "sys", max_steps=5, auto_context=False)
    agent.run("touch both files")

    final_sys = prov.systems[-1]
    assert "Files in play" in final_sys
    assert "a.py" in final_sys and "b.py" in final_sys


def test_no_working_set_section_when_empty(tmp_path):
    class P:
        def __init__(self): self.systems = []
        def chat(self, msgs, tools=None, system=None):
            self.systems.append(system or "")
            return AssistantTurn(content="nothing to do")

    prov = P()
    agent = Agent(prov, default_registry(), ToolContext(workdir=tmp_path),
                  EventUI(), "sys", max_steps=3, auto_context=False)
    agent.run("just answer")
    assert "Files in play" not in prov.systems[0]
