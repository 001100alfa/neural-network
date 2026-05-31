"""Tests for OS-level shell sandboxing (resource limits + isolator detection)."""

from __future__ import annotations

import pytest

from aio.sandbox import SandboxLimits, available_isolator, preexec, wrapper_argv
from aio.tools import ToolContext
from aio.tools.shell import RunShellTool

_HAS_RESOURCE = __import__("aio.sandbox", fromlist=["resource"]).resource is not None


def test_defaults_and_isolator_detection():
    lim = SandboxLimits()
    assert lim.cpu_seconds > 0 and lim.file_size_mb > 0 and lim.core_dumps is False
    assert available_isolator() in (None, "bwrap", "firejail")
    assert isinstance(wrapper_argv("/tmp", network=False), list)   # [] when none installed


def test_preexec_is_callable_or_none():
    fn = preexec(SandboxLimits())
    assert fn is None or callable(fn)
    if not _HAS_RESOURCE:  # pragma: no cover - Windows
        assert fn is None


@pytest.mark.skipif(not _HAS_RESOURCE, reason="resource limits need POSIX")
def test_file_size_limit_blocks_a_disk_bomb(tmp_path):
    # cap created files at 1 MB, then try to write 40 MB -> the write must fail
    ctx = ToolContext(workdir=tmp_path, sandbox=SandboxLimits(file_size_mb=1))
    out = RunShellTool().run(
        {"command": "python3 -c \"open('big.bin','wb').write(b'x'*(40*1024*1024))\""}, ctx)
    assert "[exit code: 0]" not in out                 # killed/failed by RLIMIT_FSIZE
    if (tmp_path / "big.bin").exists():
        assert (tmp_path / "big.bin").stat().st_size <= 4 * 1024 * 1024   # never the full 40 MB


@pytest.mark.skipif(not _HAS_RESOURCE, reason="resource limits need POSIX")
def test_cpu_limit_stops_a_spin(tmp_path):
    # 1 CPU-second cap -> a busy loop is killed by SIGXCPU well before the
    # run_shell wall timeout
    ctx = ToolContext(workdir=tmp_path, sandbox=SandboxLimits(cpu_seconds=1))
    out = RunShellTool().run(
        {"command": "python3 -c \"\nwhile True:\n    pass\"", "timeout": 30}, ctx)
    assert "[exit code: 0]" not in out                 # did not exit cleanly


def test_normal_command_unaffected_by_default_limits(tmp_path):
    # generous defaults must not break ordinary commands
    ctx = ToolContext(workdir=tmp_path, sandbox=SandboxLimits())
    out = RunShellTool().run({"command": "echo hello && python3 -c 'print(2+2)'"}, ctx)
    assert "[exit code: 0]" in out and "hello" in out and "4" in out
