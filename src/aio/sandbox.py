"""OS-level sandboxing for shell tools — real isolation, not a regex denylist.

Two layers, both zero-dependency and best-effort:

1. **Resource limits** (POSIX ``resource``): a ``preexec_fn`` caps the child's
   CPU time, the size of any file it can create, and core dumps — so even an
   *allowed* command can't fork into a CPU spin, write a disk-filling file or
   dump core. Memory and process-count caps are opt-in (they can break normal
   interpreters / are uid-global), off by default.
2. **External isolator** (optional): if ``bwrap`` (bubblewrap) or ``firejail``
   is installed, :func:`wrapper_argv` returns a prefix that confines the command
   to the working directory and (optionally) drops network access. Absent that,
   the resource limits still apply.

Everything degrades gracefully: on Windows or without ``resource`` the preexec
is ``None`` and the command runs unwrapped.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

try:
    import resource  # POSIX only
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore


@dataclass
class SandboxLimits:
    cpu_seconds: int = 120          # wall is enforced separately; this caps CPU
    file_size_mb: int = 200         # largest file the command may create
    core_dumps: bool = False        # disable core dumps
    memory_mb: int = 0              # 0 = unlimited (RLIMIT_AS breaks some interpreters)
    processes: int = 0              # 0 = unlimited (RLIMIT_NPROC is uid-global)
    network: bool = True            # only honoured by the external isolator


def preexec(limits: SandboxLimits):
    """Return a ``preexec_fn`` that applies the limits, or ``None`` if unsupported."""
    if resource is None:  # pragma: no cover - Windows
        return None

    def _apply() -> None:  # pragma: no cover - runs in the forked child
        def _set(res, soft):
            try:
                hard = resource.getrlimit(res)[1]
                cap = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
                resource.setrlimit(res, (cap, hard))
            except (ValueError, OSError):
                pass

        if limits.cpu_seconds:
            _set(resource.RLIMIT_CPU, limits.cpu_seconds)
        if limits.file_size_mb:
            _set(resource.RLIMIT_FSIZE, limits.file_size_mb * 1024 * 1024)
        if not limits.core_dumps:
            _set(resource.RLIMIT_CORE, 0)
        if limits.memory_mb:
            _set(resource.RLIMIT_AS, limits.memory_mb * 1024 * 1024)
        if limits.processes:
            _set(resource.RLIMIT_NPROC, limits.processes)

    return _apply


def available_isolator() -> str | None:
    """Name of an external sandbox binary if present (bwrap preferred)."""
    for name in ("bwrap", "firejail"):
        if shutil.which(name):
            return name
    return None


def wrapper_argv(workdir: str, network: bool = True) -> list[str]:
    """Argv prefix that confines a command to ``workdir`` via an external isolator.

    Returns ``[]`` when no isolator is installed (resource limits still apply).
    """
    tool = available_isolator()
    if tool == "bwrap":  # pragma: no cover - depends on bwrap being installed
        argv = ["bwrap", "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
                "--bind", workdir, workdir, "--chdir", workdir,
                "--proc", "/proc", "--dev", "/dev", "--die-with-parent"]
        if not network:
            argv.append("--unshare-net")
        return argv
    if tool == "firejail":  # pragma: no cover - depends on firejail being installed
        argv = ["firejail", "--quiet", "--private=" + workdir]
        if not network:
            argv.append("--net=none")
        return argv
    return []
