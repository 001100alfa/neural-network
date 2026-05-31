"""A zero-dependency web dashboard for the AIO agent.

Serves a single-page dashboard and a small JSON API on top of the same
:class:`aio.agent.Agent` used by the CLI. Built entirely on the Python standard
library (``http.server``). Tool approvals are auto-granted in web mode (there is
no interactive terminal), so it is intended for local/trusted use.
"""

from __future__ import annotations

import difflib
import functools
import json
import os
import shutil
import subprocess
import threading
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
from typing import Any

from .agent import Agent
from .config import PROVIDER_DEFAULTS, Config, KeyStore
from .pricing import estimate_cost
from .providers import Message, ProviderError, ToolCall, build_provider
from .tools import ToolContext, ToolError, default_registry
from .web_ui import INDEX_HTML


class EventUI:
    """A UI implementation that records events instead of printing them.

    Implements the same surface the agent and tools call, so it can be dropped
    in wherever a terminal :class:`aio.ui.UI` is expected.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        # Optional live callback: when set, each event is delivered immediately
        # (used for Server-Sent Events streaming) in addition to being stored.
        self.sink = None

    def drain(self) -> list[dict[str, Any]]:
        out = self.events
        self.events = []
        return out

    def _emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self.sink is not None:
            try:
                self.sink(event)
            except Exception:  # pragma: no cover - client disconnect, etc.
                pass

    # -- methods used by the agent / tools --------------------------------
    def banner(self, *_a, **_k) -> None:  # no-op in web mode
        pass

    def info(self, text: str) -> None:
        self._emit({"type": "info", "text": text})

    def warn(self, text: str) -> None:
        self._emit({"type": "warn", "text": text})

    def error(self, text: str) -> None:
        self._emit({"type": "error", "text": text})

    def thinking(self, text: str = "thinking…") -> None:
        self._emit({"type": "thinking", "text": text})

    def assistant(self, text: str) -> None:
        self._emit({"type": "assistant", "text": text})

    def token(self, delta: str) -> None:
        if delta:
            self._emit({"type": "token", "text": delta})

    def todos_update(self, todos: list) -> None:
        self._emit({"type": "todos", "todos": todos})

    def tool_call(self, name: str, args: dict) -> None:
        self._emit({"type": "tool_call", "name": name, "args": args})

    def tool_result(self, text: str, error: bool = False) -> None:
        self._emit({"type": "tool_result", "text": text, "error": error})

    def show_diff(self, old: str, new: str, path: str) -> None:
        if old == new:
            return
        diff = "\n".join(
            difflib.unified_diff(
                old.splitlines(), new.splitlines(),
                fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="",
            )
        )
        self._emit({"type": "diff", "path": path, "diff": diff})

    def confirm(self, name: str, args: dict) -> str:
        # No interactive prompt available over HTTP; auto-approve.
        self.tool_call(name, args)
        return "yes"


MCP_CATALOG: list[dict[str, Any]] = [
    {"name": "filesystem", "desc": "Read/write files within a directory",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "."]},
    {"name": "git", "desc": "Inspect & operate on a local git repository",
     "command": "uvx", "args": ["mcp-server-git", "--repository", "."]},
    {"name": "github", "desc": "GitHub repos, issues, PRs, code search",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"],
     "env_hint": "GITHUB_PERSONAL_ACCESS_TOKEN"},
    {"name": "fetch", "desc": "Fetch a URL and convert it to Markdown",
     "command": "uvx", "args": ["mcp-server-fetch"]},
    {"name": "memory", "desc": "Persistent knowledge-graph memory across sessions",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
    {"name": "sequential-thinking", "desc": "Structured step-by-step reasoning scratchpad",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"]},
    {"name": "everything", "desc": "Reference server exercising all MCP features (testing)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-everything"]},
    {"name": "sqlite", "desc": "Query/inspect a SQLite database",
     "command": "uvx", "args": ["mcp-server-sqlite", "--db-path", "./app.db"]},
    {"name": "postgres", "desc": "Read-only access to a PostgreSQL database",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"]},
    {"name": "playwright", "desc": "Drive a real browser (Microsoft Playwright MCP)",
     "command": "npx", "args": ["-y", "@playwright/mcp@latest"]},
    {"name": "context7", "desc": "Up-to-date library/API documentation (Upstash Context7)",
     "command": "npx", "args": ["-y", "@upstash/context7-mcp"]},
    {"name": "time", "desc": "Current time & timezone conversion",
     "command": "uvx", "args": ["mcp-server-time"]},
    {"name": "brave-search", "desc": "Web search via the Brave Search API",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-brave-search"],
     "env_hint": "BRAVE_API_KEY"},
    {"name": "gitlab", "desc": "GitLab projects, issues, merge requests",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-gitlab"],
     "env_hint": "GITLAB_PERSONAL_ACCESS_TOKEN"},
    {"name": "slack", "desc": "Read/post Slack messages & channels",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
     "env_hint": "SLACK_BOT_TOKEN"},
    {"name": "sentry", "desc": "Inspect Sentry issues & stack traces",
     "command": "npx", "args": ["-y", "@sentry/mcp-server@latest"],
     "env_hint": "SENTRY_AUTH_TOKEN"},
    {"name": "notion", "desc": "Read/write Notion pages & databases",
     "command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"],
     "env_hint": "NOTION_TOKEN"},
    {"name": "docker", "desc": "Manage Docker containers & images",
     "command": "uvx", "args": ["docker-mcp"]},
    {"name": "puppeteer", "desc": "Browser automation & screenshots (Puppeteer)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-puppeteer"]},
    {"name": "google-drive", "desc": "Search & read Google Drive files (OAuth setup)",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-gdrive"]},
    {"name": "redis", "desc": "Read/write a Redis instance",
     "command": "npx", "args": ["-y", "@modelcontextprotocol/server-redis", "redis://localhost:6379"]},
    {"name": "kubernetes", "desc": "Inspect & manage a Kubernetes cluster",
     "command": "npx", "args": ["-y", "mcp-server-kubernetes"]},
    {"name": "mongodb", "desc": "Query a MongoDB database",
     "command": "npx", "args": ["-y", "mongodb-mcp-server"],
     "env_hint": "MDB_MCP_CONNECTION_STRING"},
    {"name": "obsidian", "desc": "Read/search an Obsidian vault",
     "command": "uvx", "args": ["mcp-obsidian"], "env_hint": "OBSIDIAN_API_KEY"},
    {"name": "figma", "desc": "Read Figma designs/components",
     "command": "npx", "args": ["-y", "figma-developer-mcp", "--stdio"],
     "env_hint": "FIGMA_API_KEY"},
]

# Servers configured out of the box (when nothing else is set): the coding /
# project catalog minus git+github. They are listed as "configured" but NOT
# auto-started (autostart=False) so launching never spawns 11 npx/uvx processes;
# each gets a Start button in the panel.
_DEFAULT_MCP_NAMES = {
    "filesystem", "fetch", "context7", "brave-search", "playwright",
    "sqlite", "postgres", "memory", "sequential-thinking", "time", "everything",
}
DEFAULT_MCP_SERVERS: list[dict[str, Any]] = [
    {"name": c["name"], "command": c["command"], "args": list(c["args"]), "autostart": False}
    for c in MCP_CATALOG if c["name"] in _DEFAULT_MCP_NAMES
]


def _extract_doc_text(name: str, data: bytes) -> str:
    """Best-effort text extraction from an attached document (text or PDF)."""
    lower = name.lower()
    if lower.endswith(".pdf") or data[:5] == b"%PDF-":
        # Minimal PDF text: pull text from BT...ET / Tj / TJ operators in
        # uncompressed streams. Good enough for simple PDFs; no dependencies.
        import re

        try:
            blob = data.decode("latin-1", "replace")
        except Exception:  # pragma: no cover
            return ""
        chunks = re.findall(r"\((?:\\.|[^()\\])*\)", blob)
        out = []
        for c in chunks:
            s = c[1:-1].replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\")
            if s.strip():
                out.append(s)
        text = " ".join(out)
        return text if text.strip() else "(PDF had no extractable plain text)"
    # treat everything else as text
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


def _msg_to_dict(m: Message) -> dict[str, Any]:
    return {
        "role": m.role,
        "content": m.content,
        "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls],
        "tool_call_id": m.tool_call_id,
        "name": m.name,
    }


def _msg_from_dict(d: dict[str, Any]) -> Message:
    return Message(
        role=d.get("role", "user"),
        content=d.get("content", "") or "",
        tool_calls=[
            ToolCall(id=c.get("id", ""), name=c.get("name", ""), arguments=c.get("arguments", {}) or {})
            for c in d.get("tool_calls", []) or []
        ],
        tool_call_id=d.get("tool_call_id"),
        name=d.get("name"),
    )


class AgentService:
    """Thread-safe wrapper around an Agent for the web server."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.ui = EventUI()
        self._lock = threading.Lock()
        self._usage_lock = threading.Lock()  # guards self.usage across concurrent chats
        # API keys entered via the dashboard are persisted here and overlaid on
        # top of env/config so they survive restarts.
        self.keys = KeyStore.load()
        self.keys.apply_to(self.config)
        # cumulative token usage / cost estimate for this session
        self.usage: dict[str, Any] = {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_write": 0,
            "est_cost_usd": 0.0, "cost_known": True, "by_provider": {},
        }
        # multiple concurrent conversations (tabs): id -> message history
        self.conversations: dict[str, list] = {"default": []}
        self._summaries: dict[str, str] = {}     # per-conversation compaction summary
        self._active_conv = "default"
        self.plan_mode = False                   # read-only planning mode
        self._conv_agents: dict = {}             # per-conversation Agents (#5)
        self._conv_locks: dict = {}              # per-conversation locks (#5)
        self._checkpoints: list = []             # file snapshots for rewind (#4)
        self._todos: list = []                   # current task list (#7)
        # MCP servers + their tools (loaded once, registered on every rebuild)
        self._mcp_servers: list = []
        self._mcp_tools: list = []
        # state for the embedded static "web server" panel
        self._static_host = "127.0.0.1"
        self._static_httpd: ThreadingHTTPServer | None = None
        self._static_thread: threading.Thread | None = None
        self._static_port: int | None = None
        self._reload_mcp()  # also builds the agent

    def _make_agent_for(self, ui, messages, summary: str = "", provider=None) -> Agent:
        """Build a fresh Agent bound to a specific UI + message history.

        Used per conversation so independent chats can run concurrently without
        sharing one agent/UI. Cross-conversation state (checkpoints, todos, MCP
        tools, permissions) is still wired in. ``provider`` may be supplied to
        share an already-built/overridden provider across conversations."""
        from .config import OUTPUT_STYLES
        from .hooks import HookRunner

        ctx = ToolContext(
            workdir=self.config.workdir,
            ui=ui,
            auto_approve=True,  # web mode auto-approves tool calls
            allow_outside_workdir=self.config.allow_outside_workdir,
            checkpoints=self._checkpoints,
            todos=self._todos,
            permissions=dict(self.config.permissions),
        )
        registry = default_registry()
        for tool in self._mcp_tools:
            registry.register(tool)
        system_prompt = self.config.system_prompt + OUTPUT_STYLES.get(self.config.output_style, "")
        if self.config.project_memory:
            system_prompt += "\n\n# Project memory (CLAUDE.md / AGENTS.md)\n" + self.config.project_memory
        hooks = HookRunner(self.keys.data.get("hooks") or self.config.hooks, self.config.workdir)
        agent = Agent(
            provider=provider or build_provider(self.config),
            tools=registry,
            ctx=ctx,
            ui=ui,
            system_prompt=system_prompt,
            max_steps=self.config.max_steps,
            stream=True,
            plan_mode=self.plan_mode,
            hooks=hooks,
        )
        agent.summary = summary
        agent.messages = messages
        return agent

    def _build_agent(self) -> None:
        """Rebuild agents after a global settings change; drop per-conv cache.

        ``self.agent``/``self.ui`` stay bound to the active conversation for the
        single-agent code paths (info, sessions, rewind, todos)."""
        self._conv_agents = {}   # conv_id -> Agent (lazy per conversation)
        msgs = self.conversations.setdefault(self._active_conv, [])
        self.agent = self._make_agent_for(self.ui, msgs,
                                          self._summaries.get(self._active_conv, ""))
        self._conv_agents[self._active_conv] = self.agent

    def _agent_for_conv(self, conv_id: str):
        """Return (agent, ui, lock) for a conversation, creating them on demand."""
        conv_id = conv_id or "default"
        msgs = self.conversations.setdefault(conv_id, [])
        agent = self._conv_agents.get(conv_id)
        if agent is None:
            # share the default agent's (possibly overridden) provider
            prov = getattr(getattr(self, "agent", None), "provider", None)
            agent = self._make_agent_for(EventUI(), msgs, self._summaries.get(conv_id, ""), provider=prov)
            self._conv_agents[conv_id] = agent
        else:
            agent.messages = msgs
            agent.summary = self._summaries.get(conv_id, "")
        lock = self._conv_locks.setdefault(conv_id, threading.Lock())
        return agent, agent.ui, lock

    # -- MCP servers (web mode) ------------------------------------------
    def _mcp_configs(self) -> list[dict]:
        """Configured MCP servers: key store -> .aio.toml -> built-in defaults."""
        stored = self.keys.data.get("mcp_servers")
        if stored is not None:
            return stored
        if self.config.mcp_servers:
            return list(self.config.mcp_servers)
        return [dict(s) for s in DEFAULT_MCP_SERVERS]

    def _reload_mcp(self) -> None:
        from .mcp import load_mcp_tools

        for s in self._mcp_servers:
            try:
                s.stop()
            except Exception:  # pragma: no cover
                pass
        # only start servers opted into autostart (manual adds default to True;
        # the built-in defaults are autostart=False until the user clicks Start)
        to_start = [c for c in self._mcp_configs() if c.get("autostart", True)]
        if to_start:
            self._mcp_tools, self._mcp_servers = load_mcp_tools(to_start, ui=self.ui)
        else:
            self._mcp_tools, self._mcp_servers = [], []
        self._build_agent()

    def stop_mcp(self) -> None:
        for s in self._mcp_servers:
            try:
                s.stop()
            except Exception:  # pragma: no cover
                pass

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.config.provider,
            "model": self.config.active.model,
            "workdir": str(self.config.workdir),
            "tools": [
                {"name": t.name, "description": t.description.splitlines()[0]}
                for t in self.agent.tools
            ],
            "history": len(self.agent.messages),
            "plan_mode": self.plan_mode,
            "memory": bool(self.config.project_memory),
            "summary": bool(self._summaries.get(self._active_conv)),
            "thinking_tokens": self.config.active.thinking_tokens,
            "cache": self.config.active.cache,
            "output_style": self.config.output_style,
        }

    def set_plan_mode(self, on: bool) -> dict[str, Any]:
        with self._lock:
            self.plan_mode = bool(on)
            self._build_agent()
            return {"plan_mode": self.plan_mode}

    def set_thinking(self, tokens: int) -> dict[str, Any]:
        """Set the extended-thinking budget for the active provider (#9)."""
        with self._lock:
            self.config.active.thinking_tokens = max(0, int(tokens or 0))
            self._build_agent()
            return {"thinking_tokens": self.config.active.thinking_tokens}

    def set_output_style(self, style: str) -> dict[str, Any]:
        from .config import OUTPUT_STYLES

        with self._lock:
            if style in OUTPUT_STYLES:
                self.config.output_style = style
                self._build_agent()
            return {"output_style": self.config.output_style, "styles": list(OUTPUT_STYLES)}

    # -- checkpoints / rewind (#4) and todos (#7) ------------------------
    def checkpoints_info(self) -> dict[str, Any]:
        cps = [
            {"id": c["id"], "path": c["path"], "label": c["label"], "existed": c["existed"]}
            for c in self._checkpoints
        ]
        return {"checkpoints": cps, "todos": self._todos}

    def rewind(self, checkpoint_id: int | None = None) -> dict[str, Any]:
        """Undo file changes back to (and including) ``checkpoint_id``.

        With no id, undo only the most recent change. Restores each snapshot's
        prior contents (or deletes files that did not exist before).
        """
        from pathlib import Path as _Path

        with self._lock:
            cps = self._checkpoints
            if not cps:
                return {"ok": False, "error": "nothing to rewind", **self.checkpoints_info()}
            target = cps[-1]["id"] if checkpoint_id is None else int(checkpoint_id)
            undone, kept = [], []
            for c in reversed(cps):
                if c["id"] < target:
                    kept.append(c)
                    continue
                p = _Path(c["path"])
                try:
                    if c["existed"]:
                        p.parent.mkdir(parents=True, exist_ok=True)
                        p.write_text(c["before"] or "", encoding="utf-8")
                    elif p.exists():
                        p.unlink()
                    undone.append(c["path"])
                except OSError as exc:  # pragma: no cover - fs edge
                    return {"ok": False, "error": f"{c['path']}: {exc}", **self.checkpoints_info()}
            # keep only checkpoints below the target
            self._checkpoints[:] = [c for c in cps if c["id"] < target]
            self.agent.ctx.checkpoints = self._checkpoints
            return {"ok": True, "undone": undone, **self.checkpoints_info()}

    def _select_conv(self, conv_id: str) -> None:
        """Make ``conv_id`` active for the single-agent paths (sessions, rewind…).

        Points the default ``self.agent`` at that conversation's history and, if
        a dedicated per-conversation agent exists, keeps it in sync too."""
        conv_id = conv_id or "default"
        msgs = self.conversations.setdefault(conv_id, [])
        summ = self._summaries.get(conv_id, "")
        self.agent.messages = msgs
        self.agent.summary = summ
        self._active_conv = conv_id
        cached = self._conv_agents.get(conv_id)
        if cached is not None and cached is not self.agent:
            cached.messages = msgs
            cached.summary = summ

    def _preprocess(self, message: str) -> str:
        """Expand a custom /command and any @file mentions before sending."""
        from .commands import expand_command, expand_mentions

        msg = message
        if msg.startswith("/"):
            name, _, rest = msg[1:].partition(" ")
            expanded = expand_command(self.config.workdir, name, rest.strip())
            if expanded is not None:
                msg = expanded
        return expand_mentions(self.config.workdir, msg, self.config.allow_outside_workdir)

    def list_commands(self) -> dict[str, Any]:
        from .commands import list_commands as _lc

        return {"commands": sorted(_lc(self.config.workdir).keys())}

    @staticmethod
    def _inline_files(message: str, files) -> str:
        """Append decoded text of attached non-image documents (#10)."""
        import base64

        blocks = []
        for f in files or []:
            name = f.get("name", "attachment")
            try:
                data = base64.b64decode(f.get("data", ""))
            except Exception:
                continue
            text = _extract_doc_text(name, data)
            if text:
                blocks.append(f"\n\n--- attached file: {name} ---\n{text[:100_000]}")
        return message + "".join(blocks)

    def chat(self, message: str, images=None, conv_id: str = "default", files=None) -> dict[str, Any]:
        # Build config-derived state under the global lock, then run the turn
        # under the per-conversation lock so other conversations aren't blocked.
        with self._lock:
            message = self._inline_files(self._preprocess(message), files)
            agent, ui, conv_lock = self._agent_for_conv(conv_id)
        with conv_lock:
            ui.drain()
            try:
                final = agent.run(message, images=images)
            except ProviderError as exc:
                ui.error(str(exc))
                final = ""
            self._summaries[conv_id or "default"] = agent.summary
            self._todos = agent.ctx.todos
            self._accumulate_usage(agent)
            return {"events": ui.drain(), "final": final, "usage": self.usage_info()}

    def chat_stream(self, message: str, emit, images=None, conv_id: str = "default", files=None) -> None:
        """Run a turn, delivering each event to ``emit`` as it happens.

        ``emit`` receives every agent/tool event live and a final
        ``{"type": "done", "final": ...}`` event when the turn completes. Runs
        under a per-conversation lock so independent chats stream concurrently.
        """
        with self._lock:
            message = self._inline_files(self._preprocess(message), files)
            agent, ui, conv_lock = self._agent_for_conv(conv_id)
        with conv_lock:
            ui.drain()
            ui.sink = emit
            try:
                final = agent.run(message, images=images)
            except ProviderError as exc:
                emit({"type": "error", "text": str(exc)})
                final = ""
            finally:
                ui.sink = None
            self._summaries[conv_id or "default"] = agent.summary
            self._todos = agent.ctx.todos
            self._accumulate_usage(agent)
            emit({"type": "done", "final": final, "usage": self.usage_info()})

    def _accumulate_usage(self, agent=None) -> None:
        ru = getattr(agent or self.agent, "run_usage", None) or {}
        inp = ru.get("input_tokens", 0)
        out = ru.get("output_tokens", 0)
        reqs = ru.get("requests", 0)
        if reqs == 0 and inp == 0 and out == 0:
            return
        provider, model = self.config.provider, self.config.active.model
        cost, known = estimate_cost(model, inp, out)
        with self._usage_lock:
            self._accumulate_usage_locked(provider, reqs, inp, out, ru, cost, known)

    def _accumulate_usage_locked(self, provider, reqs, inp, out, ru, cost, known) -> None:
        self.usage["requests"] += reqs
        self.usage["input_tokens"] += inp
        self.usage["output_tokens"] += out
        self.usage["cache_read"] = self.usage.get("cache_read", 0) + ru.get("cache_read", 0)
        self.usage["cache_write"] = self.usage.get("cache_write", 0) + ru.get("cache_write", 0)
        self.usage["est_cost_usd"] = round(self.usage["est_cost_usd"] + cost, 6)
        if not known and (inp or out):
            self.usage["cost_known"] = False
        bp = self.usage["by_provider"].setdefault(
            provider, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "est_cost_usd": 0.0}
        )
        bp["requests"] += reqs
        bp["input_tokens"] += inp
        bp["output_tokens"] += out
        bp["est_cost_usd"] = round(bp["est_cost_usd"] + cost, 6)
        # persist monthly spend for budget tracking
        self.keys.record_spend(provider, self._month(), cost, inp, out, reqs)
        self.usage["budget_warning"] = self._budget_warning(provider)

    @staticmethod
    def _month() -> str:
        import time
        return time.strftime("%Y-%m")

    def _budget_warning(self, provider: str) -> str:
        budget = self.keys.get_budget(provider)
        if budget <= 0:
            return ""
        spent = self.keys.monthly(provider, self._month()).get("spent_usd", 0.0)
        label = PROVIDER_DEFAULTS[provider]["label"]
        if spent >= budget:
            return f"{label}: monthly budget exceeded (${spent:.4f} / ${budget:.2f})"
        if spent >= 0.8 * budget:
            return f"{label}: nearing monthly budget (${spent:.4f} / ${budget:.2f})"
        return ""

    def usage_info(self) -> dict[str, Any]:
        return dict(self.usage)

    def reset(self, conv_id: str = "default") -> dict[str, Any]:
        with self._lock:
            self.conversations[conv_id or "default"] = []
            self._select_conv(conv_id)
            return {"ok": True}

    def close_conversation(self, conv_id: str) -> dict[str, Any]:
        with self._lock:
            self.conversations.pop(conv_id, None)
            self._conv_agents.pop(conv_id, None)
            self._conv_locks.pop(conv_id, None)
            self._summaries.pop(conv_id, None)
            if self._active_conv == conv_id:
                self._active_conv = "default"
            return {"ok": True}

    # -- MCP panel -------------------------------------------------------
    def mcp_info(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for t in self._mcp_tools:
            srv = t.name.split("__", 1)[0]
            counts[srv] = counts.get(srv, 0) + 1
        running = {getattr(s, "name", None) for s in self._mcp_servers}
        servers = []
        for c in self._mcp_configs():
            nm = c.get("name")
            servers.append({
                "name": nm, "command": c.get("command", ""), "args": c.get("args", []),
                "running": nm in running, "tools": counts.get(nm, 0),
                "autostart": bool(c.get("autostart", True)),
            })
        configured_names = {s["name"] for s in servers}
        catalog = [dict(c, installed=(c["name"] in configured_names)) for c in MCP_CATALOG]
        return {"servers": servers, "tool_total": len(self._mcp_tools), "catalog": catalog}

    def mcp_add(self, name, command, args=None, env=None) -> dict[str, Any]:
        if not name or not command:
            raise ProviderError("MCP server needs a name and a command")
        with self._lock:
            configs = [c for c in self._mcp_configs() if c.get("name") != name]
            entry = {"name": name, "command": command, "args": args or [], "autostart": True}
            if env:
                entry["env"] = env
            configs.append(entry)
            self.keys.data["mcp_servers"] = configs
            self.keys.save()
            self._reload_mcp()
            return self.mcp_info()

    def mcp_start(self, name) -> dict[str, Any]:
        """Opt a (configured-but-stopped) server into autostart and (re)start it."""
        with self._lock:
            configs = [dict(c) for c in self._mcp_configs()]
            for c in configs:
                if c.get("name") == name:
                    c["autostart"] = True
            self.keys.data["mcp_servers"] = configs
            self.keys.save()
            self._reload_mcp()
            return self.mcp_info()

    def mcp_remove(self, name) -> dict[str, Any]:
        with self._lock:
            self.keys.data["mcp_servers"] = [c for c in self._mcp_configs() if c.get("name") != name]
            self.keys.save()
            self._reload_mcp()
            return self.mcp_info()

    def mcp_restart(self) -> dict[str, Any]:
        with self._lock:
            self._reload_mcp()
            return self.mcp_info()

    # -- sessions: save / load conversation history ----------------------
    @staticmethod
    def _sessions_dir():
        from pathlib import Path

        env = os.environ.get("AIO_SESSIONS_DIR")
        d = Path(env) if env else (Path.home() / ".config" / "aio" / "sessions")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_session(self, title: str | None = None, session_id: str | None = None,
                     conv_id: str = "default") -> dict[str, Any]:
        import time

        with self._lock:
            self._select_conv(conv_id)
            msgs = self.agent.messages
            if not title:
                first = next((m.content for m in msgs if m.role == "user" and m.content), "")
                title = (first[:48] + "…") if len(first) > 48 else (first or "session")
            sid = session_id or f"{int(time.time() * 1000):x}"
            payload = {
                "id": sid,
                "title": title,
                "provider": self.config.provider,
                "model": self.config.active.model,
                "updated": time.time(),
                "messages": [_msg_to_dict(m) for m in msgs],
            }
            (self._sessions_dir() / f"{sid}.json").write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
            return {"ok": True, "id": sid, "title": title, "count": len(msgs)}

    def list_sessions(self) -> dict[str, Any]:
        out = []
        for f in self._sessions_dir().glob("*.json"):
            try:
                d = json.loads(f.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out.append({
                "id": d.get("id", f.stem),
                "title": d.get("title", f.stem),
                "provider": d.get("provider", ""),
                "model": d.get("model", ""),
                "updated": d.get("updated", f.stat().st_mtime),
                "count": len(d.get("messages", [])),
            })
        out.sort(key=lambda s: s["updated"], reverse=True)
        return {"sessions": out}

    def search_sessions(self, query: str) -> dict[str, Any]:
        """Full-text search across saved sessions (title + message content)."""
        q = (query or "").lower().strip()
        if not q:
            return {"results": []}
        results = []
        for f in self._sessions_dir().glob("*.json"):
            try:
                d = json.loads(f.read_text("utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            hits = [m.get("content") or "" for m in d.get("messages", [])
                    if q in (m.get("content") or "").lower()]
            title_hit = q in (d.get("title", "") or "").lower()
            if not hits and not title_hit:
                continue
            snippet = ""
            if hits:
                c = hits[0]
                i = c.lower().find(q)
                snippet = ("…" if i > 30 else "") + c[max(0, i - 30):i + 60]
            results.append({
                "id": d.get("id", f.stem), "title": d.get("title", f.stem),
                "count": len(d.get("messages", [])), "matches": len(hits), "snippet": snippet,
            })
        results.sort(key=lambda r: r["matches"], reverse=True)
        return {"results": results}

    def load_session(self, session_id: str, conv_id: str = "default") -> dict[str, Any]:
        with self._lock:
            f = self._sessions_dir() / f"{session_id}.json"
            if not f.is_file():
                return {"ok": False, "error": "session not found"}
            d = json.loads(f.read_text("utf-8"))
            conv_id = conv_id or "default"
            self.conversations[conv_id] = [_msg_from_dict(m) for m in d.get("messages", [])]
            self._select_conv(conv_id)
            return {"ok": True, "id": session_id, "title": d.get("title", ""),
                    "messages": d.get("messages", [])}

    def delete_session(self, session_id: str) -> dict[str, Any]:
        f = self._sessions_dir() / f"{session_id}.json"
        if f.is_file():
            f.unlink()
        return {"ok": True}

    # -- export / import a conversation ----------------------------------
    def export_session(self, conv_id: str = "default", fmt: str = "md") -> dict[str, Any]:
        msgs = self.conversations.get(conv_id or "default", [])
        if fmt == "json":
            content = json.dumps(
                {
                    "provider": self.config.provider,
                    "model": self.config.active.model,
                    "messages": [_msg_to_dict(m) for m in msgs],
                },
                indent=2,
            )
            return {"filename": "conversation.json", "mime": "application/json", "content": content}
        # markdown
        lines = [f"# AIO conversation ({self.config.provider} / {self.config.active.model})", ""]
        for m in msgs:
            if m.role == "user":
                lines += ["## 🧑 User", "", m.content or "", ""]
                if m.images:
                    lines += [f"_({len(m.images)} image attachment(s))_", ""]
            elif m.role == "assistant":
                lines += ["## 🤖 Assistant", ""]
                if m.content:
                    lines += [m.content, ""]
                for tc in m.tool_calls:
                    lines += [f"- 🔧 `{tc.name}` `{json.dumps(tc.arguments)}`"]
                if m.tool_calls:
                    lines += [""]
            elif m.role == "tool":
                body = (m.content or "")[:1000]
                lines += ["> **tool result:**", "", "```", body, "```", ""]
        return {"filename": "conversation.md", "mime": "text/markdown", "content": "\n".join(lines)}

    def import_session(self, data: Any, conv_id: str | None = None) -> dict[str, Any]:
        import time

        if isinstance(data, dict):
            raw = data.get("messages", [])
            title = data.get("title")
        elif isinstance(data, list):
            raw, title = data, None
        else:
            return {"ok": False, "error": "import expects JSON with a 'messages' list"}
        with self._lock:
            cid = conv_id or f"imp{int(time.time() * 1000):x}"
            self.conversations[cid] = [_msg_from_dict(m) for m in raw]
            self._select_conv(cid)
            return {
                "ok": True, "conv": cid, "title": title or "imported",
                "messages": [_msg_to_dict(m) for m in self.conversations[cid]],
            }

    def configure(self, provider: str | None, model: str | None) -> dict[str, Any]:
        with self._lock:
            if provider:
                if provider not in PROVIDER_DEFAULTS:
                    raise ProviderError(f"unknown provider '{provider}'")
                self.config.provider = provider
                self.keys.set_active(provider)
            if model:
                self.config.active.model = model
                self.keys.set(self.config.provider, model=model)
            self._build_agent()
            return self.info()

    # -- AI providers / API keys panel -----------------------------------
    @staticmethod
    def _mask(key: str | None) -> str:
        if not key:
            return ""
        return ("•" * max(0, len(key) - 4)) + key[-4:] if len(key) > 4 else "••••"

    def providers_info(self) -> dict[str, Any]:
        """List every provider with masked key + configuration (never the raw key)."""
        out = []
        month = self._month()
        for name, d in PROVIDER_DEFAULTS.items():
            pc = self.config.providers[name]
            env_set = bool(d["env"] and os.environ.get(d["env"]))
            stored = bool(self.keys.get(name).get("api_key"))
            needs_key = d["env"] is not None
            budget = self.keys.get_budget(name)
            spent = self.keys.monthly(name, month).get("spent_usd", 0.0)
            out.append(
                {
                    "name": name,
                    "label": d["label"],
                    "model": pc.model,
                    "base_url": pc.base_url,
                    "env": d["env"],
                    "needs_key": needs_key,
                    "key_masked": self._mask(pc.api_key),
                    "configured": (not needs_key) or bool(pc.api_key),
                    "source": "stored" if stored else ("env" if env_set else ""),
                    "active": name == self.config.provider,
                    "budget_usd": budget,
                    "month_spent_usd": round(spent, 6),
                    "over_budget": bool(budget > 0 and spent >= budget),
                    "near_budget": bool(budget > 0 and 0.8 * budget <= spent < budget),
                }
            )
        return {"active": self.config.provider, "providers": out, "usage": self.usage_info()}

    def test_provider(self, name: str) -> dict[str, Any]:
        """Validate the key by listing models; returns {ok, models, count, error}."""
        if name not in PROVIDER_DEFAULTS:
            raise ProviderError(f"unknown provider '{name}'")
        from .providers import build_provider as _bp

        cfg_provider = self.config.provider
        try:
            self.config.provider = name  # build_provider reads config.active
            provider = _bp(self.config)
            models = provider.list_models()
        except ProviderError as exc:
            return {"ok": False, "error": str(exc), "models": [], "count": 0}
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "models": [], "count": 0}
        finally:
            self.config.provider = cfg_provider
        return {"ok": True, "count": len(models), "models": models[:100], "error": ""}

    def set_provider_key(
        self,
        name: str,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        make_active: bool = False,
        budget: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if name not in PROVIDER_DEFAULTS:
                raise ProviderError(f"unknown provider '{name}'")
            self.keys.set(name, api_key=api_key, model=model, base_url=base_url)
            if budget is not None:
                self.keys.set_budget(name, budget)
            # reflect immediately on the live config
            pc = self.config.providers[name]
            if api_key is not None:
                pc.api_key = api_key or (
                    os.environ.get(PROVIDER_DEFAULTS[name]["env"] or "") or None
                )
            if model:
                pc.model = model
            if base_url:
                pc.base_url = base_url
            if make_active:
                self.config.provider = name
                self.keys.set_active(name)
            self._build_agent()
            return self.providers_info()

    # -- Terminal (cmd / bash) -------------------------------------------
    def exec_command(self, command: str, shell: str = "bash", timeout: int = 120) -> dict[str, Any]:
        """Run a command directly in the working directory (not via the model)."""

        command = (command or "").strip()
        if not command:
            return {"output": "", "exit_code": 0}
        exe = shutil.which(shell)
        try:
            if exe:
                proc = subprocess.run(
                    [exe, "-c", command], cwd=str(self.config.workdir),
                    capture_output=True, text=True, timeout=timeout,
                )
            else:
                proc = subprocess.run(
                    command, shell=True, cwd=str(self.config.workdir),
                    capture_output=True, text=True, timeout=timeout,
                )
        except subprocess.TimeoutExpired:
            return {"output": f"command timed out after {timeout}s", "exit_code": 124}
        out = (proc.stdout or "") + (proc.stderr or "")
        return {"output": out, "exit_code": proc.returncode}

    # -- Git --------------------------------------------------------------
    def _run_git(self, args: list[str]) -> str:
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(self.config.workdir),
                capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            return "git is not installed or not on PATH."
        return ((proc.stdout or "") + (proc.stderr or "")).strip() or "(no output)"

    def git_action(self, action: str, message: str = "", pathspec: str = "-A") -> dict[str, Any]:
        presets = {
            "status": ["status", "--short", "--branch"],
            "diff": ["diff"],
            "diff_staged": ["diff", "--staged"],
            "log": ["log", "--oneline", "-15"],
            "add": ["add", pathspec or "-A"],
        }
        if action == "commit":
            self._run_git(["add", pathspec or "-A"])
            return {"output": self._run_git(["commit", "-m", message or "update"])}
        if action not in presets:
            return {"output": f"unknown git action: {action}"}
        return {"output": self._run_git(presets[action])}

    # -- Static preview web server ---------------------------------------
    def server_status(self) -> dict[str, Any]:
        running = self._static_httpd is not None
        return {
            "running": running,
            "port": self._static_port if running else None,
            "url": f"http://{self._static_host}:{self._static_port}" if running else None,
        }

    def server_start(self, port: int = 8080) -> dict[str, Any]:
        with self._lock:
            if self._static_httpd is not None:
                return self.server_status()
            handler = functools.partial(
                SimpleHTTPRequestHandler, directory=str(self.config.workdir)
            )
            httpd = ThreadingHTTPServer((self._static_host, int(port)), handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            self._static_httpd = httpd
            self._static_thread = thread
            self._static_port = int(port)
            return self.server_status()

    def server_stop(self) -> dict[str, Any]:
        with self._lock:
            if self._static_httpd is not None:
                self._static_httpd.shutdown()
                self._static_httpd.server_close()
                self._static_httpd = None
                self._static_thread = None
                self._static_port = None
            return self.server_status()

    # -- Editor (in-browser IDE) -----------------------------------------
    def fs_tree(self, limit: int = 1000) -> dict[str, Any]:
        from .tools.search import IGNORE_DIRS

        root = self.config.workdir
        files: list[str] = []
        for p in sorted(root.rglob("*")):
            if p.is_dir():
                continue
            rel_parts = p.relative_to(root).parts
            if any(part in IGNORE_DIRS for part in rel_parts):
                continue
            files.append(str(p.relative_to(root)))
            if len(files) >= limit:
                break
        return {"root": str(root), "files": files}

    def fs_read(self, path: str) -> dict[str, Any]:
        try:
            p = self.agent.ctx.safe_path(path)
        except ToolError as exc:
            return {"error": str(exc)}
        if not p.is_file():
            return {"error": f"not a file: {path}"}
        try:
            content = p.read_text("utf-8")
        except UnicodeDecodeError:
            return {"error": f"binary file (cannot edit as text): {path}"}
        return {"path": path, "content": content}

    def fs_write(self, path: str, content: str) -> dict[str, Any]:
        try:
            p = self.agent.ctx.safe_path(path)
        except ToolError as exc:
            return {"error": str(exc)}
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "path": path, "bytes": len(content)}


def _make_handler(service: AgentService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # silence default stderr logging
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/api/info":
                self._json(200, service.info())
            elif self.path == "/api/providers":
                self._json(200, service.providers_info())
            elif self.path == "/api/usage":
                self._json(200, service.usage_info())
            elif self.path == "/api/sessions":
                self._json(200, service.list_sessions())
            elif self.path == "/api/mcp":
                self._json(200, service.mcp_info())
            elif self.path == "/api/checkpoints":
                self._json(200, service.checkpoints_info())
            elif self.path == "/api/commands":
                self._json(200, service.list_commands())
            elif self.path.startswith("/api/chat/stream"):
                self._chat_stream()
            else:
                self._json(404, {"error": "not found"})

        def _chat_stream(self, payload=None):
            if payload is None:  # GET: read message from the query string
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                payload = {"message": (qs.get("message") or [""])[0],
                           "conv": (qs.get("conv") or ["default"])[0]}
            message = payload.get("message", "")
            images = payload.get("images") or []
            files = payload.get("files") or []
            conv = payload.get("conv", "default")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            def emit(event):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                self.wfile.flush()

            try:
                service.chat_stream(message, emit, images=images, conv_id=conv, files=files)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                pass

        def do_POST(self):  # noqa: N802
            try:
                payload = self._read_json()
                if self.path == "/api/chat/stream":
                    self._chat_stream(payload)
                    return
                if self.path == "/api/chat":
                    self._json(200, service.chat(
                        payload.get("message", ""),
                        images=payload.get("images"), conv_id=payload.get("conv", "default"),
                        files=payload.get("files")))
                elif self.path == "/api/reset":
                    self._json(200, service.reset(payload.get("conv", "default")))
                elif self.path == "/api/conversation/close":
                    self._json(200, service.close_conversation(payload.get("conv", "")))
                elif self.path == "/api/mcp/add":
                    args = payload.get("args")
                    if isinstance(args, str):
                        import shlex
                        args = shlex.split(args)
                    self._json(200, service.mcp_add(
                        payload.get("name", ""), payload.get("command", ""),
                        args=args, env=payload.get("env")))
                elif self.path == "/api/mcp/start":
                    self._json(200, service.mcp_start(payload.get("name", "")))
                elif self.path == "/api/mcp/remove":
                    self._json(200, service.mcp_remove(payload.get("name", "")))
                elif self.path == "/api/mcp/restart":
                    self._json(200, service.mcp_restart())
                elif self.path == "/api/config":
                    self._json(200, service.configure(payload.get("provider"), payload.get("model")))
                elif self.path == "/api/plan":
                    self._json(200, service.set_plan_mode(bool(payload.get("on"))))
                elif self.path == "/api/rewind":
                    self._json(200, service.rewind(payload.get("id")))
                elif self.path == "/api/thinking":
                    self._json(200, service.set_thinking(payload.get("tokens", 0)))
                elif self.path == "/api/style":
                    self._json(200, service.set_output_style(payload.get("style", "default")))
                elif self.path == "/api/providers":
                    self._json(200, service.set_provider_key(
                        payload.get("provider", ""),
                        api_key=payload.get("api_key"),
                        model=payload.get("model"),
                        base_url=payload.get("base_url"),
                        make_active=bool(payload.get("make_active")),
                        budget=payload.get("budget"),
                    ))
                elif self.path == "/api/providers/test":
                    self._json(200, service.test_provider(payload.get("provider", "")))
                elif self.path == "/api/sessions/save":
                    self._json(200, service.save_session(
                        payload.get("title"), payload.get("id"), conv_id=payload.get("conv", "default")))
                elif self.path == "/api/sessions/load":
                    self._json(200, service.load_session(
                        payload.get("id", ""), conv_id=payload.get("conv", "default")))
                elif self.path == "/api/sessions/delete":
                    self._json(200, service.delete_session(payload.get("id", "")))
                elif self.path == "/api/sessions/export":
                    self._json(200, service.export_session(
                        payload.get("conv", "default"), payload.get("format", "md")))
                elif self.path == "/api/sessions/import":
                    self._json(200, service.import_session(
                        payload.get("data"), conv_id=payload.get("conv")))
                elif self.path == "/api/sessions/search":
                    self._json(200, service.search_sessions(payload.get("query", "")))
                elif self.path == "/api/exec":
                    self._json(200, service.exec_command(
                        payload.get("command", ""), payload.get("shell", "bash")))
                elif self.path == "/api/git":
                    self._json(200, service.git_action(
                        payload.get("action", "status"),
                        payload.get("message", ""),
                        payload.get("pathspec", "-A")))
                elif self.path == "/api/server":
                    action = payload.get("action", "status")
                    if action == "start":
                        self._json(200, service.server_start(payload.get("port", 8080)))
                    elif action == "stop":
                        self._json(200, service.server_stop())
                    else:
                        self._json(200, service.server_status())
                elif self.path == "/api/fs/tree":
                    self._json(200, service.fs_tree())
                elif self.path == "/api/fs/read":
                    self._json(200, service.fs_read(payload.get("path", "")))
                elif self.path == "/api/fs/write":
                    self._json(200, service.fs_write(payload.get("path", ""), payload.get("content", "")))
                else:
                    self._json(404, {"error": "not found"})
            except ProviderError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(
    config: Config, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False
) -> None:
    service = AgentService(config)
    httpd = ThreadingHTTPServer((host, port), _make_handler(service))
    # When bound to 0.0.0.0, the browsable URL is localhost.
    browse_host = "localhost" if host in ("0.0.0.0", "") else host
    url = f"http://{browse_host}:{port}"
    print(f"AIO web dashboard running at {url}")
    print(f"provider={config.provider}  model={config.active.model}  workdir={config.workdir}")
    print("tool calls are auto-approved in web mode. Ctrl+C to stop.")
    if open_browser:
        import threading
        import webbrowser

        # Open shortly after the server starts listening.
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        service.server_stop()
        service.stop_mcp()
        httpd.server_close()


