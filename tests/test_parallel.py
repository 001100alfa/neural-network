"""Tests for the parallel sub-agents tool (#2)."""

from __future__ import annotations

import time

from aio.agent import Agent
from aio.providers import AssistantTurn
from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.task import ParallelTasksTool
from aio.web import EventUI


def test_parallel_tasks_registered():
    assert "parallel_tasks" in default_registry().names()


def test_parallel_tasks_runs_concurrently_and_labels(tmp_path):
    """Three sub-agents each sleep ~0.3s; concurrently they finish well under 0.9s,
    and all labelled results come back."""
    ui = EventUI()
    ctx = ToolContext(workdir=tmp_path, ui=ui)

    def fake_spawn(prompt):
        time.sleep(0.3)
        return f"done: {prompt}"

    ctx.spawn_subagent = fake_spawn
    t0 = time.time()
    out = ParallelTasksTool().run(
        {"tasks": [{"description": "A", "prompt": "a"},
                   {"description": "B", "prompt": "b"},
                   {"description": "C", "prompt": "c"}]},
        ctx,
    )
    elapsed = time.time() - t0
    assert elapsed < 0.8                     # ran in parallel, not 3*0.3s
    assert "## A" in out and "## B" in out and "## C" in out
    assert "done: a" in out


def test_parallel_tasks_validates(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    ctx.spawn_subagent = lambda p: p
    import pytest
    with pytest.raises(ToolError):
        ParallelTasksTool().run({"tasks": []}, ctx)


def test_parallel_usage_rollup_threadsafe(tmp_path):
    """The real Agent._spawn_subagent rolls usage up under a lock when run in
    parallel — totals must equal the sum of children."""
    ui = EventUI()

    class P:
        def chat(self, messages, tools=None, system=None):
            # each child does exactly one request reporting 10/5 tokens
            return AssistantTurn(content="ok", usage={"input_tokens": 10, "output_tokens": 5})

    parent = Agent(P(), default_registry(), ToolContext(workdir=tmp_path, ui=ui), ui, "sys")
    # drive parallel_tasks through the real spawn
    out = ParallelTasksTool().run(
        {"tasks": [{"prompt": "x"}, {"prompt": "y"}, {"prompt": "z"}]},
        parent.ctx,
    )
    assert out.count("## ") == 3
    assert parent.run_usage["requests"] == 3
    assert parent.run_usage["input_tokens"] == 30
