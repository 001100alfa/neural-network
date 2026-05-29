"""Git helper tools."""

from __future__ import annotations

import subprocess
from typing import Any

from .base import Tool, ToolContext, ToolError


def _git(ctx: ToolContext, *git_args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *git_args],
            cwd=str(ctx.workdir),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        raise ToolError("git is not installed or not on PATH.")
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise ToolError(f"git {' '.join(git_args)} failed:\n{out.strip()}")
    return out.strip()


class GitStatusTool(Tool):
    name = "git_status"
    description = "Show the current git status (porcelain + branch)."
    parameters = {"type": "object", "properties": {}}

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return _git(ctx, "status", "--short", "--branch") or "(clean working tree)"


class GitDiffTool(Tool):
    name = "git_diff"
    description = "Show the git diff. Set staged=true for the staged diff."
    parameters = {
        "type": "object",
        "properties": {
            "staged": {"type": "boolean", "description": "Show staged changes instead of unstaged."},
            "path": {"type": "string", "description": "Limit the diff to a path (optional)."},
        },
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        cmd = ["diff"]
        if args.get("staged"):
            cmd.append("--staged")
        if args.get("path"):
            cmd += ["--", args["path"]]
        return _git(ctx, *cmd) or "(no changes)"


class GitCommitTool(Tool):
    name = "git_commit"
    description = "Stage all changes (or a given pathspec) and create a commit with the given message."
    needs_approval = True
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Commit message."},
            "pathspec": {"type": "string", "description": "What to stage (default '-A' for everything)."},
        },
        "required": ["message"],
    }

    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        pathspec = args.get("pathspec", "-A")
        _git(ctx, "add", pathspec)
        out = _git(ctx, "commit", "-m", args["message"])
        return out
