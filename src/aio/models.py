"""Config dataclasses (``ProviderConfig``, ``Config``), split out of config.py.

``config`` re-exports both for compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .defaults import DEFAULT_SYSTEM_PROMPT, context_window_for


@dataclass
class ProviderConfig:
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)
    cache: bool = True            # provider prompt caching
    thinking_tokens: int = 0      # extended-thinking budget, 0 = off
    max_retries: int = 3          # transient-error retries with backoff


@dataclass
class Config:
    provider: str
    providers: dict[str, ProviderConfig]
    workdir: Path
    auto_approve: bool = False
    allow_outside_workdir: bool = False
    max_steps: int = 50
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)
    #: project/instruction memory loaded from CLAUDE.md / AGENTS.md / .aio.md
    project_memory: str = ""
    #: compact codebase overview (tree/langs/key files) for agent priming (#J)
    project_map: str = ""
    #: PreToolUse/PostToolUse hooks (#6)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    #: granular tool permissions: tool name -> allow|deny|ask
    permissions: dict[str, str] = field(default_factory=dict)
    #: named output style: default|concise|explanatory|teacher
    output_style: str = "default"
    #: OpenTelemetry-style telemetry settings (#8)
    telemetry: dict[str, Any] = field(default_factory=dict)
    #: auto-retrieve relevant code (BM25) and inject it into context each turn
    auto_context: bool = True
    auto_context_results: int = 5
    #: self-verify and continue up to N times after an answer (0 = off)
    max_reflections: int = 0
    #: usable context window in tokens; 0 = auto-detect from the model name
    context_window: int = 0
    #: unified permission mode: plan | default | accept_edits | admin
    permission_mode: str = "default"

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.provider]

    @property
    def effective_context_window(self) -> int:
        """Tokens of history to keep before compacting — explicit, or per-model."""
        return self.context_window or context_window_for(self.active.model)
