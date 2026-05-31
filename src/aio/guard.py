"""Argument-scoped safety guardrails for tool calls.

Approval controls *whether* a tool runs; this is a last line of defence that
refuses a few unambiguously catastrophic shell commands outright, even if a
human (or an auto-approve config) said yes. Conservative by design — it only
blocks patterns that are almost never intentional, so it doesn't get in the way.
"""

from __future__ import annotations

import re

# (compiled pattern, human reason). Kept deliberately small and specific.
_DANGEROUS = [
    (re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (re.compile(r"\bmkfs(\.\w+)?\b"), "filesystem format (mkfs)"),
    (re.compile(r"\bdd\b[^\n]*\bof=/dev/(sd|nvme|hd|disk)"), "raw write to a block device"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|disk)\w*"), "redirect over a block device"),
    (re.compile(r"\b(shutdown|reboot|halt|poweroff)\b"), "host power/shutdown command"),
]

# Flag-order-independent detection for a recursive+forced delete of root/home.
_RM = re.compile(r"\brm\b")
_RECURSIVE = re.compile(r"-[a-zA-Z]*[rR]")
_FORCE = re.compile(r"-[a-zA-Z]*f")
_ROOT_TARGET = re.compile(r"(?:^|\s)(/|/\*|~|\$HOME)(?:/\*)?(?:\s|$)")


def dangerous_command(command: str) -> str | None:
    """Return a reason string if ``command`` is catastrophic, else ``None``."""
    cmd = (command or "").strip()
    for pat, reason in _DANGEROUS:
        if pat.search(cmd):
            return reason
    if _RM.search(cmd):
        recursive = bool(_RECURSIVE.search(cmd)) or "--recursive" in cmd
        force = bool(_FORCE.search(cmd)) or "--force" in cmd
        root = bool(_ROOT_TARGET.search(cmd)) or "--no-preserve-root" in cmd
        if recursive and force and root:
            return "recursive force-delete of the filesystem root / home"
    return None
