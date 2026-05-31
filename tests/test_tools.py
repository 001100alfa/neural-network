"""Tests for the built-in tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from aio.tools.search import GlobTool, GrepTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workdir=tmp_path, auto_approve=True)


def test_write_then_read(ctx: ToolContext):
    WriteFileTool().run({"path": "hello.txt", "content": "hi\nthere"}, ctx)
    out = ReadFileTool().run({"path": "hello.txt"}, ctx)
    assert "hi" in out and "there" in out
    # line numbers are present
    assert "1\t" in out


def test_edit_unique_required(ctx: ToolContext):
    WriteFileTool().run({"path": "a.py", "content": "x = 1\nx = 1\n"}, ctx)
    with pytest.raises(ToolError):
        EditFileTool().run({"path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx)
    # replace_all works
    res = EditFileTool().run(
        {"path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True}, ctx
    )
    assert "2 replacements" in res
    assert "x = 2" in ReadFileTool().run({"path": "a.py"}, ctx)


def test_edit_missing_string(ctx: ToolContext):
    WriteFileTool().run({"path": "a.py", "content": "hello"}, ctx)
    with pytest.raises(ToolError):
        EditFileTool().run({"path": "a.py", "old_string": "nope", "new_string": "x"}, ctx)


def test_path_escape_blocked(ctx: ToolContext):
    with pytest.raises(ToolError):
        ReadFileTool().run({"path": "../../etc/passwd"}, ctx)


def test_path_escape_allowed_with_flag(tmp_path: Path):
    ctx = ToolContext(workdir=tmp_path, allow_outside_workdir=True)
    # should not raise on resolution (file may simply not exist)
    with pytest.raises(ToolError) as exc:
        ReadFileTool().run({"path": "../definitely-missing-xyz"}, ctx)
    assert "not found" in str(exc.value).lower()


def test_glob_and_grep(ctx: ToolContext):
    WriteFileTool().run({"path": "src/a.py", "content": "def foo():\n    return 1\n"}, ctx)
    WriteFileTool().run({"path": "src/b.py", "content": "def bar():\n    return 2\n"}, ctx)
    globbed = GlobTool().run({"pattern": "**/*.py"}, ctx)
    assert "a.py" in globbed and "b.py" in globbed

    grepped = GrepTool().run({"pattern": r"def \w+", "glob": "**/*.py"}, ctx)
    assert "foo" in grepped and "bar" in grepped


def test_grep_invalid_regex(ctx: ToolContext):
    with pytest.raises(ToolError):
        GrepTool().run({"pattern": "("}, ctx)


def test_list_dir(ctx: ToolContext):
    WriteFileTool().run({"path": "f.txt", "content": "x"}, ctx)
    out = ListDirTool().run({"path": "."}, ctx)
    assert "f.txt" in out


def test_write_snapshots_for_rewind(ctx: ToolContext):
    WriteFileTool().run({"path": "s.txt", "content": "one"}, ctx)
    EditFileTool().run({"path": "s.txt", "old_string": "one", "new_string": "two"}, ctx)
    # both mutations recorded a checkpoint; first had no prior file
    assert len(ctx.checkpoints) == 2
    assert ctx.checkpoints[0]["existed"] is False
    assert ctx.checkpoints[1]["before"] == "one"


def test_default_registry_has_expected_tools():
    reg = default_registry()
    names = set(reg.names())
    assert {"read_file", "write_file", "edit_file", "grep", "glob", "run_shell",
            "git_commit", "write_todos"} <= names
    # specs are well-formed
    for spec in reg.specs():
        assert "name" in spec and "parameters" in spec
