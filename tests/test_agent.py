"""Tests for compaction, plan mode and project memory."""

from __future__ import annotations

from aio.agent import READONLY_TOOLS, Agent
from aio.config import load_config, load_project_memory
from aio.providers import AssistantTurn, ToolCall
from aio.tools import ToolContext, default_registry
from aio.web import EventUI


class _NoToolProvider:
    """Replies without tool calls; answers the summariser prompt distinctly."""

    def chat(self, messages, tools=None, system=None):
        if messages and (messages[0].content or "").startswith("Summarise"):
            return AssistantTurn(content="SUMMARY OF EARLIER")
        return AssistantTurn(content="ok")


def _agent(tmp_path, **kw) -> Agent:
    ui = EventUI()
    return Agent(_NoToolProvider(), default_registry(),
                 ToolContext(workdir=tmp_path, ui=ui), ui, "sys", **kw)


def test_compaction_trims_and_summarises(tmp_path):
    ag = _agent(tmp_path, auto_compact=True, context_limit=1)  # always over threshold
    for i in range(6):
        ag.run(f"message number {i}")
    assert "SUMMARY OF EARLIER" in ag.summary           # older turns summarised
    assert len(ag.messages) <= ag.compact_keep + 2      # history trimmed
    assert ag.messages[0].role == "user"                # tail stays valid
    assert "Summary of earlier conversation" in ag._effective_system()


def test_no_compaction_when_disabled(tmp_path):
    ag = _agent(tmp_path, auto_compact=False, context_limit=1)
    for i in range(6):
        ag.run(f"m{i}")
    assert ag.summary == ""
    assert len(ag.messages) == 12  # 6 user + 6 assistant, untouched


def test_plan_mode_is_readonly(tmp_path):
    ag = _agent(tmp_path, plan_mode=True)
    names = {s["name"] for s in ag._effective_specs()}
    assert names <= READONLY_TOOLS and "write_file" not in names and "run_shell" not in names
    assert "PLAN MODE" in ag._effective_system()
    # a mutating tool call is refused and nothing is written
    res = ag._execute(ToolCall(id="1", name="write_file", arguments={"path": "x.txt", "content": "y"}))
    assert "read-only" in res.lower()
    assert not (tmp_path / "x.txt").exists()


def test_project_memory_loaded(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Always use 4-space indents.")
    (tmp_path / "AGENTS.md").write_text("Run tests before committing.")
    mem = load_project_memory(tmp_path)
    assert "Always use 4-space indents." in mem and "Run tests before committing." in mem
    cfg = load_config(workdir=tmp_path)
    assert "CLAUDE.md" in cfg.project_memory and "AGENTS.md" in cfg.project_memory


def test_project_map_loaded_into_config(tmp_path):
    (tmp_path / "main.py").write_text("print(1)\n")
    cfg = load_config(workdir=tmp_path)
    assert "main.py" in cfg.project_map and "Python" in cfg.project_map
