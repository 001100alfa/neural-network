"""Tests for the git_status / git_diff / git_commit tools (real repo)."""

from __future__ import annotations

import subprocess

import pytest

from aio.tools import ToolContext, ToolError
from aio.tools.git import GitCommitTool, GitDiffTool, GitStatusTool


def _repo(tmp_path):
    for args in (("init", "-q"), ("config", "user.email", "t@t"),
                 ("config", "user.name", "t"), ("config", "commit.gpgsign", "false")):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    return ToolContext(workdir=tmp_path)


def test_status_diff_commit_cycle(tmp_path):
    ctx = _repo(tmp_path)
    (tmp_path / "f.py").write_text("x = 1\n")

    status = GitStatusTool().run({}, ctx)
    assert "f.py" in status                                  # untracked shows up

    out = GitCommitTool().run({"message": "add f"}, ctx)
    assert "add f" in out or "master" in out or "main" in out

    (tmp_path / "f.py").write_text("x = 2\n")
    diff = GitDiffTool().run({}, ctx)
    assert "-x = 1" in diff and "+x = 2" in diff

    GitCommitTool().run({"message": "change f"}, ctx)
    assert GitDiffTool().run({}, ctx) == "(no changes)"      # clean after commit


def test_status_clean(tmp_path):
    ctx = _repo(tmp_path)
    (tmp_path / "f").write_text("a\n")
    GitCommitTool().run({"message": "c"}, ctx)
    status = GitStatusTool().run({}, ctx).strip()
    # a clean tree shows only the branch header line (no file-status lines)
    assert status.startswith("##") and len(status.splitlines()) == 1


def test_diff_staged_and_path(tmp_path):
    ctx = _repo(tmp_path)
    (tmp_path / "a.py").write_text("a\n")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True, capture_output=True)
    assert "a.py" in GitDiffTool().run({"staged": True}, ctx)


def test_commit_failure_raises(tmp_path):
    ctx = _repo(tmp_path)
    # nothing staged/changed -> git commit exits non-zero -> ToolError
    with pytest.raises(ToolError):
        GitCommitTool().run({"message": "empty", "pathspec": "."}, ctx)
