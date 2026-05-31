"""End-to-end autonomy proof WITH error recovery — closer to real agent runs.

The scripted "model" makes a realistic mistake (an edit with wrong indentation),
reads the tool's hint, recovers, then implements the feature and verifies with
the project's own tests. Every tool is the real one operating on real files.
"""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.web import EventUI

SRC = "class Calc:\n    def add(self, a, b):\n        return a + b\n"
TEST = ("from calc import Calc\n\n"
        "def test_add(): assert Calc().add(2, 3) == 5\n"
        "def test_sub(): assert Calc().sub(5, 2) == 3\n")


class _RecoveringModel:
    """Locates the class, makes a bad edit, recovers from the hint, verifies."""

    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None, system=None):
        self.step += 1
        last_tool = next((m.content for m in reversed(messages) if m.role == "tool"), "")
        if self.step == 1:
            return AssistantTurn(content="Find the class.", tool_calls=[
                ToolCall("1", "find_symbol", {"name": "Calc"})])
        if self.step == 2:
            # deliberately wrong indentation -> edit_file will fail with a hint
            return AssistantTurn(content="Add sub().", tool_calls=[ToolCall("2", "edit_file", {
                "path": "calc.py",
                "old_string": "def add(self, a, b):\n    return a + b",     # wrong indent
                "new_string": "x"})])
        if self.step == 3:
            # the hint said it's a whitespace/indentation problem -> fix it
            assert "whitespace" in last_tool.lower() or "indentation" in last_tool.lower(), last_tool
            return AssistantTurn(content="Recovering with correct indentation.", tool_calls=[
                ToolCall("3", "edit_file", {
                    "path": "calc.py",
                    "old_string": "    def add(self, a, b):\n        return a + b",
                    "new_string": "    def add(self, a, b):\n        return a + b\n\n"
                                  "    def sub(self, a, b):\n        return a - b"})])
        if self.step == 4:
            return AssistantTurn(content="Verify.", tool_calls=[
                ToolCall("4", "run_shell", {"command": "python3 -m pytest -q"})])
        return AssistantTurn(content="Done: added sub(); tests pass.")


def test_agent_recovers_from_a_bad_edit_then_finishes(tmp_path):
    (tmp_path / "calc.py").write_text(SRC)
    (tmp_path / "test_calc.py").write_text(TEST)

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(_RecoveringModel(), default_registry(), ctx, ui, "sys", max_steps=10)
    final = agent.run("Calc is missing sub(); add it and make the tests pass.")

    # the model hit a real error and recovered (two edit attempts, one failed)
    edit_results = [m.content for m in agent.messages if m.role == "tool" and m.name == "edit_file"]
    assert any("not found" in r.lower() for r in edit_results)      # first edit failed
    assert any("Edited" in r for r in edit_results)                 # second succeeded

    # the feature really landed and the project's own tests pass
    assert "def sub(self, a, b):" in (tmp_path / "calc.py").read_text()
    shell = [m.content for m in agent.messages if m.role == "tool" and m.name == "run_shell"]
    assert shell and "2 passed" in shell[0] and "[exit code: 0]" in shell[0]
    assert "Done" in final
    assert len(ctx.checkpoints) == 1   # only the successful edit snapshotted
