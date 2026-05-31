"""Tests for headless JSON output (#5) and per-hunk diff approval (#9)."""

from __future__ import annotations

import json

from aio.agent import Agent
from aio.providers import AssistantTurn
from aio.tools import ToolContext, default_registry
from aio.tools.files import EditFileTool, _apply_selected_hunks
from aio.web import EventUI


def test_headless_json_output(tmp_path, capsys):
    from aio.cli import _run_headless_json

    class P:
        def chat(self, messages, tools=None, system=None):
            return AssistantTurn(content="all done", usage={"input_tokens": 5, "output_tokens": 2})

    ui = EventUI()
    ag = Agent(P(), default_registry(), ToolContext(workdir=tmp_path, ui=ui), ui, "sys")
    import os
    os.environ["AIO_SESSIONS_DIR"] = str(tmp_path / "s")
    rc = _run_headless_json(ag, "do it")
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["result"] == "all done"
    assert out["usage"]["input_tokens"] == 5


class _PickUI(EventUI):
    """Accept only odd-numbered hunks."""
    def confirm_hunk(self, path, index, total, lines):
        return index % 2 == 1


def test_apply_selected_hunks_partial(tmp_path):
    # two changes separated by enough context to be distinct hunks
    old = "a\n" + "x\n" * 6 + "d\n"
    new = "A\n" + "x\n" * 6 + "D\n"   # line1 a->A and last d->D
    ctx = ToolContext(workdir=tmp_path, ui=_PickUI())
    merged, kept, total = _apply_selected_hunks(old, new, ctx, "f.txt")
    assert total == 2 and kept == 1
    # first hunk accepted (A), second rejected (d stays, no D)
    assert merged.startswith("A\n") and merged.endswith("d\n") and "D" not in merged


def test_edit_per_hunk_applies_subset(tmp_path):
    f = tmp_path / "x.txt"
    old = "a\n" + "x\n" * 6 + "d\n"
    f.write_text(old)
    ctx = ToolContext(workdir=tmp_path, ui=_PickUI(), auto_approve=True, per_hunk=True)
    out = EditFileTool().run(
        {"path": "x.txt", "old_string": old, "new_string": "A\n" + "x\n" * 6 + "D\n"},
        ctx,
    )
    assert "1/2 hunks applied" in out
    assert f.read_text().startswith("A\n") and f.read_text().endswith("d\n")
