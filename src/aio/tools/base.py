"""Tool abstractions shared by every built-in and MCP tool."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ToolError(Exception):
    """Raised by a tool when it cannot complete the request."""


@dataclass
class ToolContext:
    """Runtime context handed to every tool invocation."""

    workdir: Path
    ui: Any = None
    auto_approve: bool = False
    allow_outside_workdir: bool = False
    # tool names that have been granted "always allow" for this session
    approved: set[str] = field(default_factory=set)
    # granular permission rules: tool name -> "allow" | "deny" | "ask"
    permissions: dict[str, str] = field(default_factory=dict)

    def permission(self, tool_name: str) -> str:
        """Resolve the effective rule for ``tool_name`` (allow/deny/ask)."""
        return self.permissions.get(tool_name, self.permissions.get("*", "ask"))
    # file snapshots taken before mutating edits, for rewind/undo (#4)
    checkpoints: list[dict] = field(default_factory=list)
    # the agent's current task list (#7 TodoWrite)
    todos: list[dict] = field(default_factory=list)
    # callable(prompt:str) -> str that runs an isolated sub-agent (#5); set by Agent
    spawn_subagent: Any = None
    # recursion guard so sub-agents can't spawn sub-agents endlessly
    depth: int = 0
    # background processes started by run_background (#4): id -> dict
    background: dict = field(default_factory=dict)
    # lazily-built symbol index for find_symbol (#K)
    code_index: Any = None
    # lazily-built BM25 code searcher for search_code
    code_searcher: Any = None
    # when True, edits are approved hunk-by-hunk via ui.confirm_hunk (#9)
    per_hunk: bool = False

    def snapshot(self, path: "Path", label: str) -> None:
        """Record the pre-edit contents of ``path`` so the change can be undone."""
        import time as _time

        before = None
        try:
            if path.is_file():
                before = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            before = None
        self.checkpoints.append({
            "id": len(self.checkpoints) + 1,
            "path": str(path),
            "before": before,        # None => file did not exist (undo = delete)
            "existed": before is not None,
            "label": label,
            "ts": _time.time(),
        })

    def safe_path(self, path: str) -> Path:
        """Resolve ``path`` against the workdir and guard against escaping it."""

        p = Path(path)
        if not p.is_absolute():
            p = self.workdir / p
        p = p.resolve()
        if not self.allow_outside_workdir:
            wd = self.workdir.resolve()
            if wd != p and wd not in p.parents:
                raise ToolError(
                    f"Refusing to access '{p}' outside working directory '{wd}'. "
                    f"Use --allow-outside to override."
                )
        return p


class Tool(ABC):
    """Base class for an agent tool."""

    name: str = ""
    description: str = ""
    #: JSON schema for the tool's arguments (OpenAI-style "parameters")
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    #: whether the tool mutates state and should be confirmed before running
    needs_approval: bool = False

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Execute the tool and return a string result for the model."""

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    """A name -> Tool mapping with helpers for specs and dispatch."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)


def default_registry() -> ToolRegistry:
    """Build a registry with all built-in tools."""

    # Imported here to avoid a circular import at module load time.
    from .files import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
    from .git import GitCommitTool, GitDiffTool, GitStatusTool
    from .multiedit import MultiEditTool
    from .search import GlobTool, GrepTool, SearchCodeTool
    from .background import CheckBackgroundTool, RunBackgroundTool
    from .shell import RunShellTool
    from .symbols import FindSymbolTool
    from .task import ParallelTasksTool, TaskTool
    from .todos import WriteTodosTool
    from .web import WebFetchTool

    return ToolRegistry(
        [
            ReadFileTool(),
            WriteFileTool(),
            EditFileTool(),
            MultiEditTool(),
            ListDirTool(),
            GlobTool(),
            GrepTool(),
            SearchCodeTool(),
            FindSymbolTool(),
            RunShellTool(),
            GitStatusTool(),
            GitDiffTool(),
            GitCommitTool(),
            WriteTodosTool(),
            TaskTool(),
            ParallelTasksTool(),
            WebFetchTool(),
            RunBackgroundTool(),
            CheckBackgroundTool(),
        ]
    )


def _relpath(p: Path, ctx: ToolContext) -> str:
    try:
        return os.path.relpath(p, ctx.workdir)
    except ValueError:  # pragma: no cover - different drives on Windows
        return str(p)
