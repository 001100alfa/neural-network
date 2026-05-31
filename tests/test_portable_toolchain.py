"""Tests for the portable-toolchain wiring (Docker-free Linux tools on Windows).

The Windows launcher prepends a bundled ``tools\\`` (PortableGit) to PATH so the
agent's ``git`` / ``bash`` / ``grep`` calls resolve to the portable Unix tools.
We can't run a real .bat here, but we verify the contract that makes it work:

1. the agent invokes ``git`` by name (PATH-resolved), not an absolute path, so a
   PATH prepend actually redirects it;
2. prepending a directory to PATH does redirect a bare-name subprocess call
   (the exact mechanism the launcher relies on);
3. the launcher scripts contain the correct PATH wiring.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_git_tool_invokes_by_name_not_absolute_path():
    # The whole scheme depends on git being resolved via PATH.
    src = (ROOT / "src/aio/tools/git.py").read_text()
    assert '["git", *git_args]' in src
    panels = (ROOT / "src/aio/service_panels.py").read_text()
    assert '["git", *args]' in panels


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shim script")
def test_path_prepend_redirects_a_bare_name_call(tmp_path, monkeypatch):
    # Build a fake toolchain dir with a `git` shim that prints a marker, then
    # prove that prepending it to PATH makes a bare `git` call resolve to it —
    # exactly what run.bat does with tools\.
    toolbin = tmp_path / "tools" / "cmd"
    toolbin.mkdir(parents=True)
    shim = toolbin / "git"
    shim.write_text("#!/bin/sh\necho PORTABLE_GIT_SHIM\n")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("PATH", str(toolbin) + os.pathsep + os.environ.get("PATH", ""))
    out = subprocess.run(["git"], capture_output=True, text=True).stdout
    assert "PORTABLE_GIT_SHIM" in out
    assert shutil.which("git") == str(shim)   # resolved to the bundled tool


def test_launcher_prepends_tools_to_path():
    bat = (ROOT / "run.bat").read_text()
    assert "tools\\cmd\\git.exe" in bat              # detects the bundled toolchain
    assert "tools\\cmd;" in bat and "tools\\usr\\bin" in bat  # prepends bin dirs to PATH


def test_setup_scripts_present_and_wired():
    assert (ROOT / "setup-tools.bat").is_file()
    ps = (ROOT / "setup-tools.ps1").read_text()
    assert "PortableGit" in ps and "git-for-windows" in ps
    assert "tools" in ps                              # extracts into tools\
    # gitignored so the downloaded toolchain isn't committed
    assert "/tools/" in (ROOT / ".gitignore").read_text()
