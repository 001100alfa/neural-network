"""End-to-end proof of autonomous coding: the agent drives the real tool loop
(find_symbol -> read_file(symbol) -> edit_file -> run_shell tests) on real files
and makes a broken test suite pass. Provider is scripted (no network) but every
TOOL is the real one operating on the filesystem.
"""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.web import EventUI

CALC = "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n"
TEST = (
    "from calc import add, subtract, multiply\n\n"
    "def test_add(): assert add(2, 3) == 5\n"
    "def test_sub(): assert subtract(5, 2) == 3\n"
    "def test_mul(): assert multiply(4, 3) == 12\n"
)


class _AutoProvider:
    """Scripted plan: locate add, read it, add multiply, run tests, summarise."""

    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None, system=None):
        self.step += 1
        if self.step == 1:
            return AssistantTurn(content="Locating add().",
                                 tool_calls=[ToolCall("1", "find_symbol", {"name": "add"})])
        if self.step == 2:
            return AssistantTurn(content="Reading add() for style.",
                                 tool_calls=[ToolCall("2", "read_file",
                                                      {"path": "calc.py", "symbol": "add"})])
        if self.step == 3:
            return AssistantTurn(content="Adding multiply().",
                                 tool_calls=[ToolCall("3", "edit_file", {
                                     "path": "calc.py",
                                     "old_string": "def subtract(a, b):\n    return a - b",
                                     "new_string": "def subtract(a, b):\n    return a - b\n\n"
                                                   "def multiply(a, b):\n    return a * b"})])
        if self.step == 4:
            return AssistantTurn(content="Verifying with the tests.",
                                 tool_calls=[ToolCall("4", "run_shell",
                                                      {"command": "python3 -m pytest -q"})])
        return AssistantTurn(content="Done: added multiply(); all tests pass.", tool_calls=[])


def test_agent_fixes_failing_suite_end_to_end(tmp_path):
    (tmp_path / "calc.py").write_text(CALC)
    (tmp_path / "test_calc.py").write_text(TEST)

    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(_AutoProvider(), default_registry(), ctx, ui, "sys", max_steps=10)
    final = agent.run("multiply() is missing; add it and make the tests pass.")

    # the agent actually used the real navigation + edit + verify tools, in order
    used = [tc.name for m in agent.messages if m.role == "assistant" for tc in m.tool_calls]
    assert used == ["find_symbol", "read_file", "edit_file", "run_shell"]

    # the file was really modified on disk
    assert "def multiply(a, b):" in (tmp_path / "calc.py").read_text()

    # the run_shell verification step saw all tests pass
    shell_results = [m.content for m in agent.messages
                     if m.role == "tool" and m.name == "run_shell"]
    assert shell_results and "[exit code: 0]" in shell_results[0]
    assert "3 passed" in shell_results[0]

    # and the change is rewindable (a checkpoint was recorded)
    assert len(ctx.checkpoints) == 1
    assert "Done" in final
