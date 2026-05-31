"""Tests for filesystem operation tools (mkdir/move/copy/delete/archive)."""

from __future__ import annotations

import pytest

from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.fileops import (
    ArchiveTool, CopyPathTool, DeletePathTool, MakeDirTool, MovePathTool,
)


def test_all_registered():
    names = {t.name for t in default_registry()}
    assert {"make_dir", "move_path", "copy_path", "delete_path", "archive"} <= names


def test_make_dir_creates_parents(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    MakeDirTool().run({"path": "a/b/c"}, ctx)
    assert (tmp_path / "a/b/c").is_dir()
    # idempotent
    out = MakeDirTool().run({"path": "a/b/c"}, ctx)
    assert "already existed" in out


def test_move_renames_file_and_is_rewindable(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n")
    ctx = ToolContext(workdir=tmp_path)
    MovePathTool().run({"source": "old.py", "dest": "sub/new.py"}, ctx)
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "sub/new.py").read_text() == "x = 1\n"
    assert ctx.working_set[-1] == "sub/new.py"
    assert len(ctx.checkpoints) == 1            # the move was snapshotted


def test_move_whole_directory(tmp_path):
    (tmp_path / "d").mkdir(); (tmp_path / "d/f.txt").write_text("hi")
    ctx = ToolContext(workdir=tmp_path)
    MovePathTool().run({"source": "d", "dest": "renamed"}, ctx)
    assert (tmp_path / "renamed/f.txt").read_text() == "hi" and not (tmp_path / "d").exists()


def test_move_refuses_overwrite_unless_flagged(tmp_path):
    (tmp_path / "a").write_text("1"); (tmp_path / "b").write_text("2")
    ctx = ToolContext(workdir=tmp_path)
    with pytest.raises(ToolError):
        MovePathTool().run({"source": "a", "dest": "b"}, ctx)
    MovePathTool().run({"source": "a", "dest": "b", "overwrite": True}, ctx)
    assert (tmp_path / "b").read_text() == "1"


def test_copy_file_and_dir(tmp_path):
    (tmp_path / "f.txt").write_text("data")
    (tmp_path / "d").mkdir(); (tmp_path / "d/x").write_text("y")
    ctx = ToolContext(workdir=tmp_path)
    CopyPathTool().run({"source": "f.txt", "dest": "f2.txt"}, ctx)
    CopyPathTool().run({"source": "d", "dest": "d2"}, ctx)
    assert (tmp_path / "f2.txt").read_text() == "data" and (tmp_path / "f.txt").exists()
    assert (tmp_path / "d2/x").read_text() == "y"


def test_delete_file_is_rewindable_dir_needs_recursive(tmp_path):
    (tmp_path / "f.txt").write_text("bye")
    (tmp_path / "d").mkdir(); (tmp_path / "d/x").write_text("z")
    ctx = ToolContext(workdir=tmp_path)
    DeletePathTool().run({"path": "f.txt"}, ctx)
    assert not (tmp_path / "f.txt").exists() and len(ctx.checkpoints) == 1
    with pytest.raises(ToolError):
        DeletePathTool().run({"path": "d"}, ctx)            # non-empty needs recursive
    DeletePathTool().run({"path": "d", "recursive": True}, ctx)
    assert not (tmp_path / "d").exists()


def test_delete_refuses_workdir(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    with pytest.raises(ToolError):
        DeletePathTool().run({"path": "."}, ctx)


def test_archive_roundtrip(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj/a.py").write_text("A")
    (tmp_path / "proj/sub").mkdir(); (tmp_path / "proj/sub/b.py").write_text("B")
    ctx = ToolContext(workdir=tmp_path)
    ArchiveTool().run({"action": "zip", "source": "proj", "dest": "proj.zip"}, ctx)
    assert (tmp_path / "proj.zip").is_file()
    ArchiveTool().run({"action": "unzip", "source": "proj.zip", "dest": "out"}, ctx)
    assert (tmp_path / "out/proj/a.py").read_text() == "A"
    assert (tmp_path / "out/proj/sub/b.py").read_text() == "B"


def test_unzip_rejects_zip_slip(tmp_path):
    import zipfile
    bad = tmp_path / "evil.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escape.txt", "pwned")     # path traversal
    ctx = ToolContext(workdir=tmp_path)
    with pytest.raises(ToolError):
        ArchiveTool().run({"action": "unzip", "source": "evil.zip", "dest": "out"}, ctx)
    assert not (tmp_path / "escape.txt").exists()


def test_operations_confined_to_workdir(tmp_path):
    (tmp_path / "f.txt").write_text("x")
    ctx = ToolContext(workdir=tmp_path)
    with pytest.raises(ToolError):
        MovePathTool().run({"source": "f.txt", "dest": "../out.txt"}, ctx)
