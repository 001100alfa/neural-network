"""Tests for the project-wide rename_symbol refactor tool."""

from __future__ import annotations

import pytest

from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.refactor import RenameSymbolTool


def _repo(tmp_path):
    (tmp_path / "a.py").write_text("def compute(x):\n    return compute(x) + computed\n")
    (tmp_path / "b.py").write_text("from a import compute\nprint(compute(1))\n")
    (tmp_path / "notes.md").write_text("call compute() to compute things\n")
    return tmp_path


def test_rename_is_registered():
    assert default_registry().get("rename_symbol") is not None


def test_rename_across_files_whole_word_only(tmp_path):
    _repo(tmp_path)
    ctx = ToolContext(workdir=tmp_path)
    out = RenameSymbolTool().run({"old_name": "compute", "new_name": "evaluate"}, ctx)
    assert "evaluate" in out and "a.py" in out and "b.py" in out

    a = (tmp_path / "a.py").read_text()
    assert "def evaluate(x):" in a and "return evaluate(x)" in a
    assert "computed" in a                      # substring NOT renamed (whole-word only)
    assert "evaluated" not in a
    assert "from a import evaluate" in (tmp_path / "b.py").read_text()
    assert "evaluate() to evaluate things" in (tmp_path / "notes.md").read_text()


def test_dry_run_changes_nothing(tmp_path):
    _repo(tmp_path)
    ctx = ToolContext(workdir=tmp_path)
    out = RenameSymbolTool().run(
        {"old_name": "compute", "new_name": "evaluate", "dry_run": True}, ctx)
    assert "DRY RUN" in out and "a.py" in out
    assert "def compute(x):" in (tmp_path / "a.py").read_text()   # untouched


def test_rename_is_atomic_and_rewindable(tmp_path):
    _repo(tmp_path)
    ctx = ToolContext(workdir=tmp_path)
    RenameSymbolTool().run({"old_name": "compute", "new_name": "evaluate"}, ctx)
    # one snapshot per touched file (a.py + b.py + notes.md) -> rewindable
    touched = {cp["path"] for cp in ctx.checkpoints}
    assert len(touched) == 3


def test_no_match_reports_cleanly(tmp_path):
    _repo(tmp_path)
    ctx = ToolContext(workdir=tmp_path)
    out = RenameSymbolTool().run({"old_name": "nonexistent", "new_name": "x"}, ctx)
    assert "No whole-word occurrences" in out


def test_respects_ignored_dirs(tmp_path):
    _repo(tmp_path)
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "v.js").write_text("function compute(){}\n")
    ctx = ToolContext(workdir=tmp_path)
    RenameSymbolTool().run({"old_name": "compute", "new_name": "evaluate"}, ctx)
    assert "compute" in (vendor / "v.js").read_text()   # ignored dir untouched


def test_validates_identifiers(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    with pytest.raises(ToolError):
        RenameSymbolTool().run({"old_name": "a b", "new_name": "c"}, ctx)
    with pytest.raises(ToolError):
        RenameSymbolTool().run({"old_name": "x", "new_name": "x"}, ctx)
