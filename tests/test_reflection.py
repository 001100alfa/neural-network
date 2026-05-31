"""Tests for the agent's self-verify / reflect-and-continue loop."""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn, ToolCall
from aio.service import EventUI
from aio.tools import ToolContext, default_registry


def _agent(tmp_path, provider, max_reflections):
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
    return Agent(provider, default_registry(), ctx, ui, "sys",
                 max_steps=8, max_reflections=max_reflections)


class _Scripted:
    """Distinguishes work turns from 'Self-review' verification probes."""

    def __init__(self, probe_verdicts, work_turns):
        self.probe_verdicts = list(probe_verdicts)
        self.work_turns = list(work_turns)
        self.probes = 0
        self.work = 0

    def chat(self, messages, tools=None, system=None):
        if "Self-review" in (messages[-1].content or ""):
            v = self.probe_verdicts[min(self.probes, len(self.probe_verdicts) - 1)]
            self.probes += 1
            return AssistantTurn(content=v)
        t = self.work_turns[min(self.work, len(self.work_turns) - 1)]
        self.work += 1
        return t


def test_no_reflection_when_disabled(tmp_path):
    p = _Scripted(["INCOMPLETE: x"], [AssistantTurn(content="answer")])
    agent = _agent(tmp_path, p, max_reflections=0)
    out = agent.run("do it")
    assert out == "answer"
    assert p.probes == 0 and p.work == 1          # never self-reviews


def test_reflection_continues_until_complete(tmp_path):
    p = _Scripted(
        probe_verdicts=["INCOMPLETE: file not written yet", "TASK_COMPLETE"],
        work_turns=[
            AssistantTurn(content="I'll start."),                       # initial -> incomplete
            AssistantTurn(content="", tool_calls=[ToolCall(            # continue: write the file
                "1", "write_file", {"path": "out.txt", "content": "hi"})]),
            AssistantTurn(content="done"),
        ],
    )
    agent = _agent(tmp_path, p, max_reflections=3)
    out = agent.run("create out.txt")
    assert out == "done"
    assert (tmp_path / "out.txt").read_text() == "hi"   # the continue turn did real work
    assert p.probes == 2                                # incomplete, then complete


def test_immediate_complete_skips_extra_work(tmp_path):
    p = _Scripted(["TASK_COMPLETE"], [AssistantTurn(content="all good")])
    agent = _agent(tmp_path, p, max_reflections=2)
    out = agent.run("trivial")
    assert out == "all good"
    assert p.probes == 1 and p.work == 1          # one verify, no extra work turn


def test_reflection_is_bounded(tmp_path):
    # verifier is never satisfied -> stops after max_reflections continues
    p = _Scripted(["INCOMPLETE: still more"], [AssistantTurn(content="partial")])
    agent = _agent(tmp_path, p, max_reflections=2)
    agent.run("hard task")
    assert p.probes == 2 and p.work == 3          # initial + exactly 2 continues


def test_reflection_usage_is_accumulated(tmp_path):
    p = _Scripted(
        ["INCOMPLETE: more", "TASK_COMPLETE"],
        [AssistantTurn(content="a", usage={"input_tokens": 10, "output_tokens": 2}),
         AssistantTurn(content="b", usage={"input_tokens": 5, "output_tokens": 1})],
    )
    # give probes some usage too
    p_chat = p.chat
    def chat(messages, tools=None, system=None):
        t = p_chat(messages, tools, system)
        if t.usage is None:
            t.usage = {"input_tokens": 3, "output_tokens": 1}
        return t
    p.chat = chat
    agent = _agent(tmp_path, p, max_reflections=2)
    agent.run("task")
    # usage spans both work turns and the probes (requests counted)
    assert agent.run_usage["requests"] >= 3
    assert agent.run_usage["input_tokens"] > 0
