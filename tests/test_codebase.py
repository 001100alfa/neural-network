"""Tests for project map (#J), gitignore awareness, and multi_edit."""

from __future__ import annotations

import pytest

from aio.projectmap import build_project_map
from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.multiedit import MultiEditTool


def test_project_map_summarizes_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    (tmp_path / "src" / "util.py").write_text("x=1\n")
    (tmp_path / "README.md").write_text("# Demo")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'")
    # ignored dir should not be counted
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("//x")

    m = build_project_map(tmp_path)
    assert "Python" in m
    assert "README.md" in m and "pyproject.toml" in m
    assert "app.py" in m
    assert "node_modules" not in m  # ignored


def test_project_map_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("secret.txt\nbuildout/\n")
    (tmp_path / "keep.py").write_text("x=1")
    (tmp_path / "secret.txt").write_text("nope")
    (tmp_path / "buildout").mkdir()
    (tmp_path / "buildout" / "a.py").write_text("y=2")
    m = build_project_map(tmp_path)
    assert "keep.py" in m
    assert "secret.txt" not in m
    assert "buildout" not in m


def test_project_map_empty_dir(tmp_path):
    assert build_project_map(tmp_path) == ""


def test_multi_edit_registered():
    assert "multi_edit" in default_registry().names()


def test_multi_edit_atomic_success(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a = 1\nb = 2\nc = 3\n")
    ctx = ToolContext(workdir=tmp_path, auto_approve=True)
    out = MultiEditTool().run(
        {"path": "a.py", "edits": [
            {"old_string": "a = 1", "new_string": "a = 10"},
            {"old_string": "c = 3", "new_string": "c = 30"},
        ]}, ctx,
    )
    assert "Applied 2 edits" in out
    assert f.read_text() == "a = 10\nb = 2\nc = 30\n"
    # a checkpoint was recorded for rewind
    assert len(ctx.checkpoints) == 1


def test_multi_edit_atomic_failure_writes_nothing(tmp_path):
    f = tmp_path / "a.py"
    original = "a = 1\nb = 2\n"
    f.write_text(original)
    ctx = ToolContext(workdir=tmp_path, auto_approve=True)
    with pytest.raises(ToolError):
        MultiEditTool().run(
            {"path": "a.py", "edits": [
                {"old_string": "a = 1", "new_string": "a = 10"},
                {"old_string": "NOPE", "new_string": "x"},   # fails -> abort all
            ]}, ctx,
        )
    assert f.read_text() == original  # unchanged (atomic)


def test_multi_edit_requires_unique(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x\nx\n")
    ctx = ToolContext(workdir=tmp_path, auto_approve=True)
    with pytest.raises(ToolError):
        MultiEditTool().run({"path": "a.py", "edits": [{"old_string": "x", "new_string": "y"}]}, ctx)
    # replace_all resolves it
    out = MultiEditTool().run(
        {"path": "a.py", "edits": [{"old_string": "x", "new_string": "y", "replace_all": True}]}, ctx
    )
    assert "Applied 1 edits" in out and f.read_text() == "y\ny\n"
