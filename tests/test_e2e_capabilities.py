"""End-to-end capability proof: one autonomous run that exercises the full
Claude-Code-level toolset on real files — scaffold a package, manage files,
write code, hit a real edit error and recover, run the project's tests, archive.

The provider is scripted (deterministic, no network), but every TOOL is the
real one operating on the filesystem, so this proves the loop + tools end to end.
"""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.web import EventUI


class _Model:
    def __init__(self):
        self.n = 0

    def chat(self, msgs, tools=None, system=None):
        self.n += 1
        last = next((m.content for m in reversed(msgs) if m.role == "tool"), "")
        if self.n == 1:
            return AssistantTurn(content="scaffold", tool_calls=[
                ToolCall("1", "make_dir", {"path": "mypkg"})])
        if self.n == 2:
            return AssistantTurn(content="module", tool_calls=[ToolCall("2", "write_file", {
                "path": "mypkg/calc.py", "content": "def add(a, b):\n    return a + b\n"})])
        if self.n == 3:
            return AssistantTurn(content="tests", tool_calls=[ToolCall("3", "write_file", {
                "path": "test_calc.py",
                "content": "from mypkg.calc import add, mul\n\n"
                           "def test_add(): assert add(2, 3) == 5\n"
                           "def test_mul(): assert mul(4, 3) == 12\n"})])
        if self.n == 4:
            return AssistantTurn(content="init", tool_calls=[ToolCall("4", "write_file", {
                "path": "mypkg/__init__.py", "content": ""})])
        if self.n == 5:   # deliberately wrong indentation -> edit fails with a hint
            return AssistantTurn(content="bad edit", tool_calls=[ToolCall("5", "edit_file", {
                "path": "mypkg/calc.py",
                "old_string": "def add(a, b):\n  return a + b", "new_string": "x"})])
        if self.n == 6:   # recover from the hint
            assert "whitespace" in last.lower() or "indentation" in last.lower(), last
            return AssistantTurn(content="recover", tool_calls=[ToolCall("6", "edit_file", {
                "path": "mypkg/calc.py",
                "old_string": "def add(a, b):\n    return a + b",
                "new_string": "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b"})])
        if self.n == 7:
            return AssistantTurn(content="verify", tool_calls=[ToolCall("7", "run_shell", {
                "command": "python3 -m pytest -q"})])
        if self.n == 8:
            return AssistantTurn(content="backup", tool_calls=[ToolCall("8", "archive", {
                "action": "zip", "source": "mypkg", "dest": "mypkg-backup.zip"})])
        return AssistantTurn(content="Done: package built, mul added, tests pass, backup made.")


def test_full_capability_run(tmp_path):
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    agent = Agent(_Model(), default_registry(), ctx, ui, "sys", max_steps=12)
    final = agent.run("scaffold a package, implement the missing function, pass tests, back up")

    used = [tc.name for m in agent.messages if m.role == "assistant" for tc in m.tool_calls]
    assert used == ["make_dir", "write_file", "write_file", "write_file",
                    "edit_file", "edit_file", "run_shell", "archive"]

    # project scaffolded with file-management tools
    assert (tmp_path / "mypkg").is_dir()
    assert (tmp_path / "mypkg/calc.py").exists() and (tmp_path / "mypkg/__init__.py").exists()
    # a real edit error happened and the agent recovered
    edits = [m.content for m in agent.messages if m.role == "tool" and m.name == "edit_file"]
    assert any("not found" in e.lower() for e in edits) and any("Edited" in e for e in edits)
    assert "def mul(a, b):" in (tmp_path / "mypkg/calc.py").read_text()
    # the project's own tests pass
    shell = [m.content for m in agent.messages if m.role == "tool" and m.name == "run_shell"]
    assert shell and "2 passed" in shell[0] and "[exit code: 0]" in shell[0]
    # archived, and multi-file working set tracked
    assert (tmp_path / "mypkg-backup.zip").is_file()
    assert "mypkg/calc.py" in ctx.working_set
    assert "Done" in final
