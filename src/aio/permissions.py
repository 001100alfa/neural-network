"""Permission modes — a single, unified control over what the agent may do.

Mirrors the way Claude Code exposes one permission setting instead of a handful
of scattered flags. Four modes, from most to least restrictive:

* ``plan``          — read-only; no mutating tool runs at all (produce a plan).
* ``default``       — ask the user to approve each side-effecting tool.
* ``accept_edits``  — auto-approve file EDITS (read/write/edit/move/...), but
                      still ask for shell and other higher-risk tools.
* ``admin``         — elevated: auto-approve everything (the safety guardrail on
                      catastrophic shell commands still applies). The "admin /
                      bypass" mode for trusted, unattended runs.

The mode resolves a per-tool decision (``allow`` / ``ask`` / ``deny``) that the
agent's existing approval flow honours, so explicit ``permissions`` rules and
the catastrophic-command guardrail keep working on top of it.
"""

from __future__ import annotations

MODES = ("plan", "default", "accept_edits", "admin")

# Tools that only edit/inspect files (safe to auto-approve under accept_edits).
# Shell, git_commit, background, screenshot, MCP etc. are NOT here on purpose.
_EDIT_TOOLS = frozenset({
    "read_file", "write_file", "edit_file", "multi_edit", "rename_symbol",
    "list_dir", "glob", "grep", "find_symbol", "search_code", "write_todos",
    "make_dir", "move_path", "copy_path", "delete_path", "archive",
})

# Read-only tools allowed even in plan mode (kept in sync with agent.READONLY_TOOLS).
_READONLY = frozenset({
    "read_file", "list_dir", "glob", "grep", "git_status", "git_diff",
    "find_symbol", "search_code",
})


def normalize(mode: str | None) -> str:
    """Map user input (incl. Claude-Code-style aliases) to a canonical mode."""
    m = (mode or "default").strip().lower().replace("-", "_")
    aliases = {
        "acceptedits": "accept_edits", "accept_edits": "accept_edits",
        "bypasspermissions": "admin", "bypass": "admin", "yes": "admin",
        "elevated": "admin", "auto": "admin",
        "readonly": "plan", "read_only": "plan",
    }
    m = aliases.get(m, m)
    return m if m in MODES else "default"


def decision(mode: str, tool_name: str, needs_approval: bool) -> str:
    """Return 'allow' | 'ask' | 'deny' for a tool under ``mode``.

    Does NOT encode explicit per-tool ``permissions`` rules or the guardrail —
    those are layered on by the agent; this is just the mode's baseline.
    """
    mode = normalize(mode)
    if not needs_approval:
        # read-only / non-mutating tools never need approval...
        if mode == "plan" and tool_name not in _READONLY:
            return "deny"
        return "allow"
    if mode == "plan":
        return "deny"                       # no mutations at all
    if mode == "admin":
        return "allow"                      # elevated: approve everything
    if mode == "accept_edits":
        return "allow" if tool_name in _EDIT_TOOLS else "ask"
    return "ask"                            # default: ask for every mutation


def describe(mode: str) -> str:
    mode = normalize(mode)
    return {
        "plan": "plan (read-only; no changes)",
        "default": "default (ask before each change)",
        "accept_edits": "accept-edits (auto-approve file edits; ask for shell/etc.)",
        "admin": "admin (elevated; auto-approve everything — guardrail still blocks catastrophes)",
    }[mode]
