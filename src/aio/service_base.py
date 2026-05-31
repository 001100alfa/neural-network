"""Shared, typed base for the AgentService mixins.

AgentService's behaviour is split across focused mixins (sessions, providers,
panels) to keep any one file readable. They all share the state that
``AgentService.__init__`` sets up; declaring it here (annotations only, no
assignment) lets each mixin — and the type checker — see those attributes and
the few core methods they call, without runtime coupling.
"""

from __future__ import annotations

import threading
from typing import Any

from .agent import Agent
from .config import Config, KeyStore


class ServiceBase:
    # -- state populated by AgentService.__init__ -------------------------
    config: Config
    keys: KeyStore
    ui: Any
    gated: bool
    _approvals: Any
    telemetry: Any
    _lock: threading.Lock
    _usage_lock: threading.Lock
    usage: dict[str, Any]
    conversations: dict[str, list]
    _summaries: dict[str, str]
    _store: Any
    _active_conv: str
    plan_mode: bool
    agent: Agent
    _conv_agents: dict
    _conv_locks: dict
    _checkpoints: list
    _todos: list
    _mcp_servers: list
    _mcp_tools: list
    _static_host: str
    _static_httpd: Any
    _static_thread: Any
    _static_port: int | None

    # -- core methods the mixins call (implemented on AgentService) -------
    def _select_conv(self, conv_id: str) -> None: ...
    def _build_agent(self) -> None: ...

    def usage_info(self) -> dict[str, Any]:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        raise NotImplementedError

    @staticmethod
    def _month() -> str:
        raise NotImplementedError
