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
        # API keys entered via the dashboard are persisted here and overlaid on
        # top of env/config so they survive restarts.
        self.keys = KeyStore.load()
        self.keys.apply_to(self.config)
        # cumulative token usage / cost estimate for this session
        self.usage: dict[str, Any] = {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "est_cost_usd": 0.0, "cost_known": True, "by_provider": {},
        }
        # multiple concurrent conversations (tabs): id -> message history
        self.conversations: dict[str, list] = {"default": []}
        self._active_conv = "default"
        # MCP servers + their tools (loaded once, registered on every rebuild)
        self._mcp_servers: list = []
        self._mcp_tools: list = []
        # state for the embedded static "web server" panel
        self._static_host = "127.0.0.1"
        self._static_httpd: ThreadingHTTPServer | None = None
        self._static_thread: threading.Thread | None = None
        self._static_port: int | None = None
        self._reload_mcp()  # also builds the agent

    def _build_agent(self) -> None:
        ctx = ToolContext(
            workdir=self.config.workdir,
            ui=self.ui,
            auto_approve=True,  # web mode auto-approves tool calls
            allow_outside_workdir=self.config.allow_outside_workdir,
        )
        registry = default_registry()
        for tool in self._mcp_tools:
            registry.register(tool)
        self.agent = Agent(
            provider=build_provider(self.config),
            tools=registry,
            ctx=ctx,
            ui=self.ui,
            system_prompt=self.config.system_prompt,
            max_steps=self.config.max_steps,
            stream=True,  # web chat streams token-by-token over SSE
        )
        # keep the active conversation's history attached to the rebuilt agent
        self.agent.messages = self.conversations.setdefault(self._active_conv, [])

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
        }

    def _select_conv(self, conv_id: str) -> None:
        """Point the agent at the message history for ``conv_id`` (multi-tab)."""
        conv_id = conv_id or "default"
        self.agent.messages = self.conversations.setdefault(conv_id, [])
        self._active_conv = conv_id

    def chat(self, message: str, images=None, conv_id: str = "default") -> dict[str, Any]:
        with self._lock:
            self._select_conv(conv_id)
            self.ui.drain()
            try:
                final = self.agent.run(message, images=images)
            except ProviderError as exc:
                self.ui.error(str(exc))
                final = ""
            self._accumulate_usage()
            return {"events": self.ui.drain(), "final": final, "usage": self.usage_info()}

    def chat_stream(self, message: str, emit, images=None, conv_id: str = "default") -> None:
        """Run a turn, delivering each event to ``emit`` as it happens.

        ``emit`` receives every agent/tool event live and a final
        ``{"type": "done", "final": ...}`` event when the turn completes.
        """
        with self._lock:
            self._select_conv(conv_id)
            self.ui.drain()
            self.ui.sink = emit
            try:
                final = self.agent.run(message, images=images)
            except ProviderError as exc:
                emit({"type": "error", "text": str(exc)})
                final = ""
            finally:
                self.ui.sink = None
            self._accumulate_usage()
            emit({"type": "done", "final": final, "usage": self.usage_info()})

    def _accumulate_usage(self) -> None:
        ru = getattr(self.agent, "run_usage", None) or {}
        inp = ru.get("input_tokens", 0)
        out = ru.get("output_tokens", 0)
        reqs = ru.get("requests", 0)
        if reqs == 0 and inp == 0 and out == 0:
            return
        provider, model = self.config.provider, self.config.active.model
        cost, known = estimate_cost(model, inp, out)
        self.usage["requests"] += reqs
        self.usage["input_tokens"] += inp
        self.usage["output_tokens"] += out
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
                service.chat_stream(message, emit, images=images, conv_id=conv)
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
                        images=payload.get("images"), conv_id=payload.get("conv", "default")))
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


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AIO — Coding Agent Dashboard</title>
<style>
  :root{--bg:#0d1117;--panel:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
        --accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--mag:#bc8cff;--fz:14px;
        --field:#0d1117;--hover:#1c2128;}
  body.light{--bg:#ffffff;--panel:#f3f5f8;--border:#d0d7de;--text:#1f2328;--muted:#636c76;
        --accent:#0969da;--green:#1a7f37;--red:#cf222e;--yellow:#9a6700;--mag:#8250df;
        --field:#ffffff;--hover:#eaeef2;}
  *{box-sizing:border-box}
  body{margin:0;font:var(--fz)/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column}
  .settings{position:absolute;top:52px;right:14px;z-index:20;display:none;flex-direction:column;gap:10px;
        background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 14px;min-width:200px;
        box-shadow:0 6px 24px rgba(0,0,0,.4)}
  .settings.on{display:flex}
  .settings .srow{display:flex;justify-content:space-between;align-items:center;gap:12px;font-size:13px}
  .searchres{position:absolute;top:52px;z-index:20;display:none;flex-direction:column;max-width:420px;max-height:50vh;
        overflow:auto;background:var(--panel);border:1px solid var(--border);border-radius:8px;
        box-shadow:0 6px 24px rgba(0,0,0,.4)}
  .searchres.on{display:flex}
  .searchres .sr{padding:8px 10px;border-bottom:1px solid var(--border);cursor:pointer;font-size:12px}
  .searchres .sr:hover{background:var(--hover)}
  .searchres .sr b{color:var(--text)} .searchres .sr small{color:var(--muted)}
  main.dragging{outline:2px dashed var(--accent);outline-offset:-6px}
  header{display:flex;align-items:center;gap:14px;padding:12px 18px;border-bottom:1px solid var(--border);
         background:var(--panel)}
  header h1{font-size:16px;margin:0;letter-spacing:.5px}
  header h1 .tag{color:var(--accent)}
  .badges{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:center}
  select,input,button{background:var(--field);color:var(--text);border:1px solid var(--border);
        border-radius:6px;padding:6px 10px;font-size:13px}
  button{cursor:pointer}
  button:hover{border-color:var(--accent)}
  .layout{display:flex;flex:1;min-height:0}
  aside{width:240px;border-right:1px solid var(--border);padding:14px;overflow:auto;background:var(--panel)}
  aside h3{font-size:12px;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
  .tool{padding:6px 8px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px}
  .tool b{color:var(--mag)}
  .tool small{color:var(--muted);display:block}
  main{flex:1;display:flex;flex-direction:column;min-width:0}
  #log{flex:1;overflow:auto;padding:18px;display:flex;flex-direction:column;gap:12px}
  .msg{max-width:80%;padding:10px 14px;border-radius:10px;white-space:pre-wrap;word-wrap:break-word}
  .user{align-self:flex-end;background:#1f6feb33;border:1px solid #1f6feb55}
  .assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--border)}
  .event{align-self:flex-start;max-width:80%;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
         font-size:12.5px;border-radius:8px;border:1px solid var(--border);overflow:hidden}
  .event .head{padding:6px 10px;background:var(--hover);color:var(--accent)}
  .event .body{padding:8px 10px;white-space:pre-wrap;color:var(--muted);max-height:260px;overflow:auto}
  .event.err .head{color:var(--red)}
  .diff .add{color:var(--green)} .diff .del{color:var(--red)} .diff .hunk{color:var(--accent)}
  .thinking{color:var(--muted);font-style:italic;align-self:flex-start}
  footer{border-top:1px solid var(--border);padding:12px;display:flex;gap:10px;background:var(--panel)}
  #input{flex:1;resize:none;height:46px;font-size:14px}
  #send{padding:0 22px;background:#238636;border-color:#2ea043;font-weight:600}
  #send:disabled{opacity:.5;cursor:not-allowed}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:6px}
  /* right-hand tools panel */
  .panel{width:460px;border-left:1px solid var(--border);background:var(--panel);display:flex;flex-direction:column}
  .tabs{display:flex;border-bottom:1px solid var(--border)}
  .tabs button{flex:1;border:0;border-radius:0;background:transparent;color:var(--muted);padding:10px}
  .tabs button.active{color:var(--text);box-shadow:inset 0 -2px 0 var(--accent)}
  .tab{display:none;flex:1;flex-direction:column;min-height:0;padding:12px;gap:8px}
  .tab.active{display:flex}
  .console{flex:1;overflow:auto;background:#010409;border:1px solid var(--border);border-radius:6px;
           padding:8px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
           white-space:pre-wrap;color:#c9d1d9;min-height:120px}
  .row{display:flex;gap:6px}
  .row input,.row select{flex:1}
  .gitbtns{display:flex;flex-wrap:wrap;gap:6px}
  .gitbtns button{flex:1 1 30%}
  /* providers / keys panel */
  .phdr{display:flex;flex-direction:column;gap:2px;padding-bottom:8px;border-bottom:1px solid var(--border)}
  .phdr .muted{font-size:11px}
  .provlist{flex:1;overflow:auto;display:flex;flex-direction:column;gap:8px;padding-top:8px}
  .pcard{border:1px solid var(--border);border-radius:8px;padding:8px 10px;background:var(--field)}
  .pcard.active{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
  .pcard .ptitle{display:flex;align-items:center;gap:8px;margin-bottom:6px}
  .pcard .ptitle b{font-size:13px}
  .pdot{width:8px;height:8px;border-radius:50%;background:#484f58;flex:0 0 auto}
  .pdot.ok{background:var(--green)} .pdot.on{background:var(--accent)}
  .pcard .badge{font-size:10px;color:var(--muted);border:1px solid var(--border);border-radius:10px;padding:1px 6px}
  .pcard .badge.act{color:var(--accent);border-color:var(--accent)}
  .pcard input{width:100%;margin:2px 0;padding:4px 6px;font-size:12px}
  .pcard .prow{display:flex;gap:6px;align-items:center}
  .pcard .prow input{flex:1}
  .pcard .pacts{display:flex;gap:6px;margin-top:4px}
  .pcard .pacts button{flex:1;padding:4px}
  .padv{font-size:11px;color:var(--muted);cursor:pointer;user-select:none}
  .padv-body{display:none} .padv-body.on{display:block}
  .usagebar{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--text);
        background:var(--field);border:1px solid var(--border);border-radius:6px;padding:5px 8px;margin:8px 0}
  .usagebar b{color:var(--green)} .usagebar .bwarn{color:var(--red)}
  .sep{color:var(--border)}
  /* conversation tabs */
  .convtabs{display:flex;gap:4px;padding:8px 8px 0;overflow-x:auto;background:var(--bg);align-items:center}
  .convtabs .ctab{display:flex;align-items:center;gap:6px;padding:5px 10px;border:1px solid var(--border);
        border-bottom:0;border-radius:8px 8px 0 0;background:var(--panel);color:var(--muted);cursor:pointer;
        white-space:nowrap;font-size:12px}
  .convtabs .ctab.active{color:var(--text);background:var(--bg);box-shadow:inset 0 2px 0 var(--accent)}
  .convtabs .ctab .x{color:var(--muted)} .convtabs .ctab .x:hover{color:var(--red)}
  .convtabs .newconv{border:1px dashed var(--border);border-radius:8px;background:transparent;color:var(--muted);
        cursor:pointer;padding:5px 10px}
  /* image attachments */
  .attachbar{display:flex;gap:6px;flex-wrap:wrap;padding:0 18px}
  .attachbar .thumb{position:relative;width:48px;height:48px;border:1px solid var(--border);border-radius:6px;
        overflow:hidden;background:#010409}
  .attachbar .thumb img{width:100%;height:100%;object-fit:cover}
  .attachbar .thumb .x{position:absolute;top:0;right:2px;color:#fff;cursor:pointer;text-shadow:0 0 3px #000}
  .msg .imgs{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}
  .msg .imgs img{max-width:160px;max-height:160px;border-radius:6px;border:1px solid var(--border)}
  #attachBtn{padding:0 10px;background:var(--field)}
  /* mcp panel */
  .mcpadd{display:flex;flex-direction:column;gap:6px;border-top:1px solid var(--border);padding-top:10px;margin-top:8px}
  .mcpadd input{padding:5px 8px;font-size:12px}
  .mcpcathdr{font-size:12px;color:var(--text);font-weight:600;border-top:1px solid var(--border);
        padding-top:10px;margin-top:10px}
  .mcat{display:flex;align-items:flex-start;gap:8px;padding:6px 8px;border:1px solid var(--border);
        border-radius:6px;margin-bottom:6px}
  .mcat .info{flex:1;min-width:0}
  .mcat .info b{font-size:12.5px} .mcat .info .d{color:var(--muted);font-size:11px}
  .mcat .info .cmd{color:var(--muted);font-size:11px;font-family:ui-monospace,Menlo,monospace;
        white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .mcat .key{font-size:10px;color:var(--yellow);border:1px solid var(--yellow);border-radius:8px;padding:0 5px}
  .mcat button{padding:3px 8px;font-size:12px;white-space:nowrap}
  .mcat .ins{font-size:10px;color:var(--green)}
  .ptest{font-size:11px;margin-top:3px;min-height:14px}
  .ptest.ok{color:var(--green)} .ptest.err{color:var(--red)} .ptest.muted{color:var(--muted)}
  .ec0{color:var(--green)} .ecN{color:var(--red)}
  a.link{color:var(--accent)}
  /* editor / IDE tab */
  #edFile{flex:1}
  .ok{color:var(--green)} .muted{color:var(--muted)}
  .edtabs{display:flex;gap:4px;overflow-x:auto;min-height:30px}
  .edtabs .etab{display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid var(--border);
        border-bottom:0;border-radius:6px 6px 0 0;background:#0d1117;color:var(--muted);cursor:pointer;
        white-space:nowrap;font-size:12px}
  .edtabs .etab.active{color:var(--text);background:#010409;box-shadow:inset 0 2px 0 var(--accent)}
  .edtabs .etab .x{color:var(--muted);padding:0 2px} .edtabs .etab .x:hover{color:var(--red)}
  .edtabs .etab.dirty .name::after{content:" •";color:var(--yellow)}
  .editor-wrap{position:relative;flex:1;min-height:280px;border:1px solid var(--border);
        border-radius:0 6px 6px 6px;overflow:hidden;background:#010409;display:flex}
  .gutter{flex:0 0 auto;min-width:34px;overflow:hidden;background:#0b0f14;border-right:1px solid var(--border);
        color:#6e7681;text-align:right}
  .gutter .nums{padding:10px 6px 10px 8px;will-change:transform;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;line-height:1.5;white-space:pre}
  .code-area{position:relative;flex:1;overflow:hidden}
  .code-area pre.hl,.code-area textarea{margin:0;padding:10px;border:0;box-sizing:border-box;
        font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;line-height:1.5;tab-size:4;
        white-space:pre;word-wrap:normal;position:absolute;inset:0;width:100%;height:100%;overflow:auto}
  .code-area pre.hl{pointer-events:none;color:#c9d1d9;z-index:0}
  .code-area pre.hl code{font:inherit;white-space:pre}
  .code-area textarea{background:transparent;color:transparent;caret-color:#e6edf3;resize:none;z-index:1;outline:none}
  .findbar{position:absolute;top:6px;right:14px;z-index:5;display:none;flex-direction:column;gap:4px;
        background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:6px}
  .findbar.on{display:flex}
  .findbar .frow{display:flex;gap:4px;align-items:center}
  .findbar input{padding:3px 6px;font-size:12px;width:130px}
  .findbar .cnt{color:var(--muted);min-width:46px;text-align:center;font-size:12px}
  .findbar button{padding:2px 7px}
  /* syntax tokens */
  .t-comment{color:#8b949e;font-style:italic} .t-string{color:#a5d6ff} .t-keyword{color:#ff7b72}
  .t-number{color:#79c0ff} .t-tag{color:#7ee787} .t-atrule{color:#d2a8ff}
  .t-heading{color:#ff7b72;font-weight:bold} .t-code{color:#a5d6ff} .t-prop{color:#79c0ff}
</style>
</head>
<body>
<header>
  <h1>AIO <span class="tag">●</span> coding agent</h1>
  <div class="badges">
    <span><span class="dot"></span><span id="provider">…</span> / <span id="model">…</span></span>
    <select id="providerSel" title="switch provider">
      <option>anthropic</option><option>openai</option><option>google</option>
      <option>groq</option><option>mistral</option><option>deepseek</option>
      <option>xai</option><option>together</option><option>openrouter</option><option>ollama</option>
    </select>
    <input id="modelInput" placeholder="model name" size="16"/>
    <button id="apply">Apply</button>
    <span class="sep">·</span>
    <input id="searchInput" placeholder="🔎 search chats" size="13" autocomplete="off"/>
    <div id="searchResults" class="searchres"></div>
    <button id="saveSession" title="save this conversation">Save chat</button>
    <select id="sessionSel" title="saved sessions"><option value="">sessions…</option></select>
    <button id="loadSession">Load</button>
    <button id="reset">Clear</button>
    <span class="sep">·</span>
    <button id="exportMd" title="export as Markdown">⤓ MD</button>
    <button id="exportJson" title="export as JSON">⤓ JSON</button>
    <button id="importBtn" title="import a JSON conversation">Import</button>
    <input id="importInput" type="file" accept="application/json,.json" style="display:none"/>
    <button id="gearBtn" title="settings">⚙</button>
  </div>
  <div id="settingsPanel" class="settings">
    <div class="srow"><span>Theme</span>
      <select id="themeSel"><option value="dark">Dark</option><option value="light">Light</option></select>
    </div>
    <div class="srow"><span>Font size</span>
      <span><button id="fzMinus">−</button> <span id="fzVal">14</span>px <button id="fzPlus">+</button></span>
    </div>
  </div>
</header>
<div class="layout">
  <aside>
    <h3>Working dir</h3>
    <div id="workdir" style="color:var(--muted);word-break:break-all;margin-bottom:16px"></div>
    <h3>Tools</h3>
    <div id="tools"></div>
  </aside>
  <main>
    <div id="convTabs" class="convtabs"></div>
    <div id="log"></div>
    <div id="attachBar" class="attachbar"></div>
    <footer>
      <input id="fileInput" type="file" accept="image/*" multiple style="display:none"/>
      <button id="attachBtn" title="attach image(s)">📎</button>
      <textarea id="input" placeholder="Ask the agent to do something… (Enter to send, Shift+Enter for newline)"></textarea>
      <button id="send">Send</button>
    </footer>
  </main>
  <div class="panel">
    <div class="tabs">
      <button data-tab="editor" class="active">Editor</button>
      <button data-tab="providers">Providers</button>
      <button data-tab="mcp">MCP</button>
      <button data-tab="terminal">Terminal</button>
      <button data-tab="git">Git</button>
      <button data-tab="server">Web server</button>
    </div>

    <div class="tab" id="tab-mcp">
      <div class="phdr">
        <b>MCP servers</b>
        <span class="muted">stdio Model Context Protocol servers — their tools are added to the agent</span>
      </div>
      <div id="mcpList"></div>
      <div class="mcpadd">
        <input id="mcpName" placeholder="name (e.g. filesystem)"/>
        <input id="mcpCmd" placeholder="command (e.g. npx)"/>
        <input id="mcpArgs" placeholder="args (e.g. -y @modelcontextprotocol/server-filesystem .)"/>
        <div class="row">
          <button id="mcpAdd">Add &amp; start</button>
          <button id="mcpRestart">Restart all</button>
        </div>
      </div>
      <div class="mcpcathdr">Open-source servers for coding &amp; project work
        <span class="muted">— "Use" fills the form (edit path/token, then Add)</span></div>
      <div id="mcpCatalog" class="provlist"></div>
    </div>

    <div class="tab" id="tab-providers">
      <div class="phdr">
        <b>AI providers &amp; API keys</b>
        <span class="muted">keys are stored locally (chmod 600) and never sent anywhere except the provider you pick</span>
      </div>
      <div id="usageBar" class="usagebar" title="estimated; session totals">usage: —</div>
      <div id="provList" class="provlist"></div>
    </div>

    <div class="tab active" id="tab-editor">
      <div class="row">
        <select id="edFile"><option value="">— open a file —</option></select>
        <button id="edReload" title="reload file tree">⟳</button>
      </div>
      <div id="edTabs" class="edtabs"></div>
      <div class="editor-wrap">
        <div class="gutter" id="edGutter"><div class="nums" id="edNums">1</div></div>
        <div class="code-area">
          <pre class="hl" id="edHLpre"><code id="edHL"></code></pre>
          <textarea id="edText" spellcheck="false" wrap="off" placeholder="select a file to edit…"></textarea>
        </div>
        <div class="findbar" id="edFindBar">
          <div class="frow">
            <input id="edFindInput" placeholder="find" spellcheck="false"/>
            <span class="cnt" id="edFindCnt">0/0</span>
            <button id="edFindPrev" title="previous (Shift+Enter)">↑</button>
            <button id="edFindNext" title="next (Enter)">↓</button>
            <button id="edFindClose" title="close (Esc)">×</button>
          </div>
          <div class="frow">
            <input id="edReplaceInput" placeholder="replace" spellcheck="false"/>
            <button id="edReplaceOne" title="replace current match">Replace</button>
            <button id="edReplaceAll" title="replace all matches">All</button>
          </div>
        </div>
      </div>
      <div class="row">
        <span id="edStatus" class="muted" style="flex:1;align-self:center"></span>
        <button id="edRevert">Revert</button>
        <button id="edSave">Save</button>
      </div>
    </div>

    <div class="tab" id="tab-terminal">
      <div class="console" id="termOut">$ run shell / bash commands here (executed directly, not via the model)
</div>
      <div class="row">
        <select id="termShell"><option>bash</option><option>sh</option><option>cmd</option></select>
        <input id="termCmd" placeholder="e.g. ls -la  /  npm test"/>
        <button id="termRun">Run</button>
      </div>
    </div>

    <div class="tab" id="tab-git">
      <div class="gitbtns">
        <button data-git="status">status</button>
        <button data-git="diff">diff</button>
        <button data-git="diff_staged">staged</button>
        <button data-git="log">log</button>
        <button data-git="add">add -A</button>
      </div>
      <div class="console diff" id="gitOut">git output…
</div>
      <div class="row">
        <input id="gitMsg" placeholder="commit message"/>
        <button id="gitCommit">Commit</button>
      </div>
    </div>

    <div class="tab" id="tab-server">
      <div class="console" id="srvOut">Serve the working directory as static files.
</div>
      <div class="row">
        <input id="srvPort" value="8080" style="max-width:90px"/>
        <button id="srvStart">Start</button>
        <button id="srvStop">Stop</button>
        <button id="srvStatus">Status</button>
      </div>
    </div>
  </div>
</div>
<script>
const log = document.getElementById('log');
const input = document.getElementById('input');
const send = document.getElementById('send');

function el(cls, text){const d=document.createElement('div');d.className=cls;if(text!=null)d.textContent=text;return d;}
function scroll(){log.scrollTop = log.scrollHeight;}

function addMsg(role, text){ if(!text) return; const d=el('msg '+role, text); log.appendChild(d); scroll(); }
function addThinking(t){ const d=el('thinking', t||'thinking…'); log.appendChild(d); scroll(); return d; }

// streaming: build the assistant bubble token-by-token
let curStream=null;
function appendToken(t){
  if(!curStream){ curStream=el('msg assistant'); curStream.textContent=''; log.appendChild(curStream); }
  curStream.textContent += t; scroll();
}
function endStream(){ curStream=null; }

function addEvent(ev){
  if(ev.type==='thinking'){ return; }
  if(ev.type==='token'){ appendToken(ev.text); return; }
  endStream();  // any non-token event finalises the streamed bubble
  if(ev.type==='assistant'){ addMsg('assistant', ev.text); return; }
  if(ev.type==='tool_call'){
    const wrap=el('event'); wrap.appendChild(Object.assign(el('head'),
      {textContent:'⚙ '+ev.name+'('+Object.entries(ev.args||{}).map(([k,v])=>k+'='+short(v)).join(', ')+')'}));
    log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='tool_result'){
    const wrap=el('event'+(ev.error?' err':'')); wrap.appendChild(Object.assign(el('head'),{textContent: ev.error?'✗ error':'↳ result'}));
    wrap.appendChild(Object.assign(el('body'),{textContent: ev.text})); log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='diff'){
    const wrap=el('event diff'); wrap.appendChild(Object.assign(el('head'),{textContent:'± '+ev.path}));
    const body=el('body'); ev.diff.split('\n').forEach(line=>{
      let c=''; if(line.startsWith('+')&&!line.startsWith('+++'))c='add';
      else if(line.startsWith('-')&&!line.startsWith('---'))c='del';
      else if(line.startsWith('@@'))c='hunk';
      const ln=document.createElement('div'); ln.className=c; ln.textContent=line; body.appendChild(ln);
    });
    wrap.appendChild(body); log.appendChild(wrap); scroll(); return;
  }
  if(ev.type==='info'||ev.type==='warn'||ev.type==='error'){
    const wrap=el('event'+(ev.type==='error'?' err':'')); wrap.appendChild(Object.assign(el('head'),{textContent:ev.type}));
    wrap.appendChild(Object.assign(el('body'),{textContent:ev.text})); log.appendChild(wrap); scroll(); return;
  }
}
function short(v){v=String(v).replace(/\n/g,'\\n');return v.length>60?v.slice(0,60)+'…':v;}

async function loadInfo(){
  const r=await fetch('/api/info'); const d=await r.json();
  document.getElementById('provider').textContent=d.provider;
  document.getElementById('model').textContent=d.model;
  document.getElementById('workdir').textContent=d.workdir;
  document.getElementById('providerSel').value=d.provider;
  document.getElementById('modelInput').value=d.model;
  document.getElementById('tools').innerHTML='';
  d.tools.forEach(t=>{const x=el('tool');x.innerHTML='<b>'+t.name+'</b><small>'+t.description+'</small>';
    document.getElementById('tools').appendChild(x);});
}

// ---- conversation tabs (multiple concurrent chats) ----
let convs={}, activeConv=null, convSeq=0;
function newConv(title){ const id='c'+(++convSeq); convs[id]={title:title||('Chat '+convSeq), html:''}; return id; }
function renderConvTabs(){
  const bar=document.getElementById('convTabs'); bar.innerHTML='';
  Object.keys(convs).forEach(id=>{
    const t=el('ctab'+(id===activeConv?' active':''));
    const nm=document.createElement('span'); nm.textContent=convs[id].title; nm.onclick=()=>switchConv(id);
    t.appendChild(nm);
    if(Object.keys(convs).length>1){ const x=document.createElement('span'); x.className='x'; x.textContent='×';
      x.onclick=(e)=>{e.stopPropagation(); closeConv(id);}; t.appendChild(x); }
    bar.appendChild(t);
  });
  const add=document.createElement('button'); add.className='newconv'; add.textContent='+ New chat';
  add.onclick=()=>{ switchConv(newConv()); }; bar.appendChild(add);
}
function switchConv(id){
  if(activeConv && convs[activeConv]) convs[activeConv].html=log.innerHTML;
  activeConv=id; curStream=null; log.innerHTML=(convs[id]&&convs[id].html)||''; renderConvTabs(); scroll();
}
async function closeConv(id){
  try{ await fetch('/api/conversation/close',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:id})}); }catch(_){}
  delete convs[id];
  if(activeConv===id){ const rest=Object.keys(convs); switchConv(rest[0]||newConv()); }
  else renderConvTabs();
}

// ---- image attachments (multimodal) ----
let pending=[];
function renderAttachments(){
  const bar=document.getElementById('attachBar'); bar.innerHTML='';
  pending.forEach((p,i)=>{ const th=el('thumb'); const im=document.createElement('img');
    im.src='data:'+p.media_type+';base64,'+p.data; th.appendChild(im);
    const x=document.createElement('span'); x.className='x'; x.textContent='×';
    x.onclick=()=>{pending.splice(i,1);renderAttachments();}; th.appendChild(x); bar.appendChild(th); });
}
function addFiles(files){
  [...files].forEach(f=>{ if(f.type && f.type.indexOf('image/')!==0) return;
    const r=new FileReader();
    r.onload=()=>{ const b64=String(r.result).split(',')[1]||'';
      pending.push({media_type:f.type||'image/png',data:b64,name:f.name||'pasted'}); renderAttachments(); };
    r.readAsDataURL(f); });
}
document.getElementById('attachBtn').onclick=()=>document.getElementById('fileInput').click();
document.getElementById('fileInput').addEventListener('change',(e)=>{ addFiles(e.target.files); e.target.value=''; });
// drag & drop onto the chat area
['dragover','drop'].forEach(ev=>document.getElementById('log').addEventListener(ev,e=>{e.preventDefault();}));
document.getElementById('log').addEventListener('drop',e=>{ if(e.dataTransfer&&e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
const mainEl=document.querySelector('main');
['dragover','drop'].forEach(ev=>mainEl.addEventListener(ev,e=>{e.preventDefault(); mainEl.classList.toggle('dragging', ev==='dragover');}));
mainEl.addEventListener('drop',e=>{ mainEl.classList.remove('dragging'); if(e.dataTransfer&&e.dataTransfer.files.length) addFiles(e.dataTransfer.files); });
mainEl.addEventListener('dragleave',()=>mainEl.classList.remove('dragging'));
// paste images from the clipboard
document.addEventListener('paste',e=>{ const items=(e.clipboardData||{}).items||[];
  const imgs=[...items].filter(it=>it.type&&it.type.indexOf('image/')===0).map(it=>it.getAsFile()).filter(Boolean);
  if(imgs.length){ addFiles(imgs); } });
function addUserMsg(text, imgs){
  const d=el('msg user'); if(text) d.textContent=text;
  if(imgs && imgs.length){ const box=el('imgs'); imgs.forEach(p=>{const im=document.createElement('img');
    im.src='data:'+p.media_type+';base64,'+p.data; box.appendChild(im);}); d.appendChild(box); }
  log.appendChild(d); scroll();
}

function sysMsg(text){ const d=el('event'); d.appendChild(Object.assign(el('head'),{textContent:text})); log.appendChild(d); scroll(); }
async function runSlash(text){
  const [cmd, ...rest]=text.slice(1).split(/\s+/); const arg=rest.join(' ').trim();
  switch((cmd||'').toLowerCase()){
    case 'help': sysMsg('commands: /new /clear /save /export [md|json] /provider <name> /model <name> /theme [light|dark] /help'); break;
    case 'new': switchConv(newConv()); break;
    case 'clear': document.getElementById('reset').click(); break;
    case 'save': document.getElementById('saveSession').click(); break;
    case 'export': exportConv((arg||'md').toLowerCase()==='json'?'json':'md'); break;
    case 'provider':
      if(arg){ document.getElementById('providerSel').value=arg; await fetch('/api/config',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:arg})}); loadInfo(); sysMsg('provider → '+arg); }
      break;
    case 'model':
      if(arg){ await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({model:arg})}); loadInfo(); sysMsg('model → '+arg); }
      break;
    case 'theme':{ const s=getSettings(); s.theme=(arg==='light'?'light':'dark'); saveSettings(s); sysMsg('theme → '+s.theme); break; }
    default: sysMsg('unknown command: /'+cmd+' (try /help)');
  }
}

async function sendMsg(){
  const text=input.value.trim(); if(!text && pending.length===0) return;
  if(text.startsWith('/') && pending.length===0){ input.value=''; await runSlash(text); input.focus(); return; }
  const imgs=pending.map(p=>({media_type:p.media_type,data:p.data}));
  addUserMsg(text, pending); input.value=''; pending=[]; renderAttachments();
  send.disabled=true; const think=addThinking();
  let sawDiff=false, gotFirst=false;
  try{
    const resp=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text, images:imgs, conv:activeConv})});
    const reader=resp.body.getReader(); const dec=new TextDecoder(); let buf='';
    for(;;){
      const {value,done}=await reader.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const frame=buf.slice(0,i); buf=buf.slice(i+2);
        const dl=frame.split('\n').find(l=>l.startsWith('data:')); if(!dl) continue;
        let ev; try{ ev=JSON.parse(dl.slice(5).trim()); }catch(_){ continue; }
        if(!gotFirst){ think.remove(); gotFirst=true; }
        if(ev.type==='done'){ if(ev.usage) renderUsage(ev.usage); break; }
        if(ev.type==='diff') sawDiff=true;
        if(ev.type!=='thinking') addEvent(ev);
      }
    }
  }catch(e){ if(!gotFirst) think.remove(); addEvent({type:'error',text:String(e)}); }
  endStream(); send.disabled=false; input.focus();
  if(sawDiff){ loadTree(); if(window.__edRefresh) window.__edRefresh(); }
}

send.onclick=sendMsg;
input.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMsg();}                 // Enter / Ctrl+Enter: send
  if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();sendMsg();}
});
// global keyboard shortcuts (chosen to avoid clobbering browser defaults)
document.addEventListener('keydown',e=>{
  if(e.altKey && (e.key==='n'||e.key==='N')){ e.preventDefault(); switchConv(newConv()); input.focus(); }
  else if(e.altKey && (e.key==='w'||e.key==='W')){ e.preventDefault(); if(activeConv) closeConv(activeConv); }
  else if((e.ctrlKey||e.metaKey) && e.key===','){ e.preventDefault(); document.getElementById('settingsPanel').classList.toggle('on'); }
  else if(e.key==='Escape'){ document.getElementById('settingsPanel').classList.remove('on');
    const sr=document.getElementById('searchResults'); if(sr) sr.classList.remove('on'); }
});
document.getElementById('reset').onclick=async()=>{
  await fetch('/api/reset',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv})});
  log.innerHTML=''; if(convs[activeConv]) convs[activeConv].html=''; loadInfo();
};
document.getElementById('apply').onclick=async()=>{
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({provider:document.getElementById('providerSel').value,
      model:document.getElementById('modelInput').value})});
  loadInfo();
};

// ---- sessions: save / load conversation history ----
const sessionSel=document.getElementById('sessionSel');
async function loadSessions(){
  const r=await fetch('/api/sessions'); const d=await r.json();
  sessionSel.innerHTML='<option value="">sessions… ('+(d.sessions||[]).length+')</option>';
  (d.sessions||[]).forEach(s=>{const o=document.createElement('option');o.value=s.id;
    o.textContent=s.title+' · '+s.count+' msg'; sessionSel.appendChild(o);});
}
function renderHistory(msgs){
  log.innerHTML='';
  (msgs||[]).forEach(m=>{
    if(m.role==='user'){ addUserMsg(m.content, (m.images||[])); }
    else if(m.role==='assistant'){
      if(m.content) addMsg('assistant', m.content);
      (m.tool_calls||[]).forEach(tc=>addEvent({type:'tool_call',name:tc.name,args:tc.arguments}));
    } else if(m.role==='tool'){ addEvent({type:'tool_result',text:m.content}); }
  });
}
document.getElementById('saveSession').onclick=async()=>{
  const r=await fetch('/api/sessions/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv})});
  const d=await r.json(); await loadSessions();
  if(d.id) sessionSel.value=d.id;
  if(d.title && convs[activeConv]){ convs[activeConv].title=d.title; renderConvTabs(); }
};
document.getElementById('loadSession').onclick=async()=>{
  const id=sessionSel.value; if(!id) return;
  const r=await fetch('/api/sessions/load',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id, conv:activeConv})});
  const d=await r.json();
  if(d.ok){ renderHistory(d.messages); if(d.title && convs[activeConv]){convs[activeConv].title=d.title; renderConvTabs();} }
};
loadSessions();

// ---- export / import a conversation ----
function download(filename, content, mime){
  const blob=new Blob([content],{type:mime||'text/plain'}); const url=URL.createObjectURL(blob);
  const a=document.createElement('a'); a.href=url; a.download=filename; document.body.appendChild(a);
  a.click(); a.remove(); URL.revokeObjectURL(url);
}
async function exportConv(fmt){
  const r=await fetch('/api/sessions/export',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({conv:activeConv, format:fmt})});
  const d=await r.json(); download(d.filename, d.content, d.mime);
}
document.getElementById('exportMd').onclick=()=>exportConv('md');
document.getElementById('exportJson').onclick=()=>exportConv('json');
document.getElementById('importBtn').onclick=()=>document.getElementById('importInput').click();
document.getElementById('importInput').addEventListener('change',(e)=>{
  const f=e.target.files[0]; if(!f) return; const rd=new FileReader();
  rd.onload=async()=>{ let data; try{ data=JSON.parse(String(rd.result)); }catch(err){ alert('Not valid JSON'); return; }
    const r=await fetch('/api/sessions/import',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({data})});
    const d=await r.json();
    if(d.ok){ const id=newConv(d.title||'imported'); switchConv(id); renderHistory(d.messages);
      convs[id].html=log.innerHTML; }
  };
  rd.readAsText(f); e.target.value='';
});

// ---- settings: theme + font size (persisted in localStorage) ----
function getSettings(){ try{ return JSON.parse(localStorage.getItem('aio_settings')||'{}'); }catch(_){ return {}; } }
function applySettings(){
  const s=getSettings(); const theme=s.theme||'dark'; const fz=s.font||14;
  document.body.classList.toggle('light', theme==='light');
  document.documentElement.style.setProperty('--fz', fz+'px');
  const ts=document.getElementById('themeSel'); if(ts) ts.value=theme;
  const fv=document.getElementById('fzVal'); if(fv) fv.textContent=fz;
}
function saveSettings(s){ localStorage.setItem('aio_settings', JSON.stringify(s)); applySettings(); }
document.getElementById('gearBtn').onclick=()=>document.getElementById('settingsPanel').classList.toggle('on');
document.getElementById('themeSel').onchange=(e)=>{ const s=getSettings(); s.theme=e.target.value; saveSettings(s); };
document.getElementById('fzMinus').onclick=()=>{ const s=getSettings(); s.font=Math.max(11,(s.font||14)-1); saveSettings(s); };
document.getElementById('fzPlus').onclick=()=>{ const s=getSettings(); s.font=Math.min(22,(s.font||14)+1); saveSettings(s); };
applySettings();

// ---- search across saved conversations ----
const searchInput=document.getElementById('searchInput');
const searchResults=document.getElementById('searchResults');
async function loadSessionById(id, title){
  const r=await fetch('/api/sessions/load',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id, conv:activeConv})});
  const d=await r.json();
  if(d.ok){ renderHistory(d.messages);
    if((title||d.title)&&convs[activeConv]){convs[activeConv].title=(title||d.title); renderConvTabs();} }
}
let searchTimer=null;
function placeSearch(){ const r=searchInput.getBoundingClientRect(); searchResults.style.left=r.left+'px'; }
searchInput.addEventListener('input',()=>{ clearTimeout(searchTimer); searchTimer=setTimeout(doSearch,250); });
async function doSearch(){
  const q=searchInput.value.trim();
  if(!q){ searchResults.classList.remove('on'); return; }
  const r=await fetch('/api/sessions/search',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({query:q})});
  const d=await r.json(); searchResults.innerHTML=''; placeSearch();
  if(!(d.results||[]).length){ searchResults.innerHTML='<div class="sr"><small>no matches</small></div>'; }
  (d.results||[]).forEach(s=>{ const it=el('sr');
    it.innerHTML='<b>'+esc(s.title)+'</b> <small>· '+s.matches+' match(es) · '+s.count+' msg</small>'
      +'<br><small>'+esc(s.snippet||'')+'</small>';
    it.onclick=()=>{ searchResults.classList.remove('on'); searchInput.value=''; loadSessionById(s.id, s.title); };
    searchResults.appendChild(it); });
  searchResults.classList.add('on');
}
document.addEventListener('click',(e)=>{ if(!searchResults.contains(e.target) && e.target!==searchInput)
  searchResults.classList.remove('on'); });


// ---- MCP panel ----
const mcpList=document.getElementById('mcpList');
async function loadMcp(){
  const r=await fetch('/api/mcp'); const d=await r.json(); mcpList.innerHTML='';
  if(!(d.servers||[]).length){ mcpList.appendChild(el('muted','No MCP servers configured. Add one below.')); }
  (d.servers||[]).forEach(s=>{
    const c=el('pcard'); const t=el('ptitle');
    t.appendChild(el('pdot'+(s.running?' ok':'')));
    const nm=document.createElement('b'); nm.textContent=s.name; t.appendChild(nm);
    const sp=document.createElement('span'); sp.style.flex='1'; t.appendChild(sp);
    const badge=el('badge'); badge.textContent=(s.running?'running · ':'stopped · ')+s.tools+' tools'; t.appendChild(badge);
    c.appendChild(t);
    const cmd=el('muted', s.command+' '+(s.args||[]).join(' ')); cmd.style.fontSize='11px'; c.appendChild(cmd);
    const acts=el('pacts');
    if(!s.running){ const st=document.createElement('button'); st.textContent='Start';
      st.onclick=async()=>{ st.disabled=true; st.textContent='starting…';
        await fetch('/api/mcp/start',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({name:s.name})}); loadMcp(); loadInfo(); };
      acts.appendChild(st); }
    const rm=document.createElement('button'); rm.textContent='Remove';
    rm.onclick=async()=>{ await fetch('/api/mcp/remove',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:s.name})}); loadMcp(); loadInfo(); };
    acts.appendChild(rm); c.appendChild(acts); mcpList.appendChild(c);
  });
  renderCatalog(d.catalog||[]);
}
function renderCatalog(cat){
  const box=document.getElementById('mcpCatalog'); box.innerHTML='';
  cat.forEach(s=>{
    const row=el('mcat'); const info=el('info');
    const title=document.createElement('div');
    title.innerHTML='<b>'+esc(s.name)+'</b>'+(s.installed?' <span class="ins">✓ added</span>':'')
      +(s.env_hint?' <span class="key" title="needs env var">'+esc(s.env_hint)+'</span>':'');
    info.appendChild(title);
    info.appendChild(Object.assign(el('d'),{textContent:s.desc||''}));
    info.appendChild(Object.assign(el('cmd'),{textContent:s.command+' '+(s.args||[]).join(' ')}));
    const use=document.createElement('button'); use.textContent='Use →';
    use.onclick=()=>{
      document.getElementById('mcpName').value=s.name;
      document.getElementById('mcpCmd').value=s.command;
      document.getElementById('mcpArgs').value=(s.args||[]).join(' ');
      document.getElementById('mcpName').scrollIntoView({block:'nearest'});
      document.getElementById('mcpArgs').focus();
    };
    row.appendChild(info); row.appendChild(use); box.appendChild(row);
  });
}
document.getElementById('mcpAdd').onclick=async()=>{
  const name=document.getElementById('mcpName').value.trim();
  const command=document.getElementById('mcpCmd').value.trim();
  const args=document.getElementById('mcpArgs').value.trim();
  if(!name||!command) return;
  await fetch('/api/mcp/add',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name, command, args})});
  document.getElementById('mcpName').value=''; document.getElementById('mcpCmd').value='';
  document.getElementById('mcpArgs').value=''; loadMcp(); loadInfo();
};
document.getElementById('mcpRestart').onclick=async()=>{
  await fetch('/api/mcp/restart',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}); loadMcp(); loadInfo();
};
const mcpTabBtn=document.querySelector('.tabs button[data-tab="mcp"]');
if(mcpTabBtn) mcpTabBtn.addEventListener('click', loadMcp);

// start with one conversation
switchConv(newConv('Chat 1'));

// ---- tabs ----
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  b.classList.add('active');
  document.getElementById('tab-'+b.dataset.tab).classList.add('active');
});

// ---- Terminal (cmd / bash) ----
const termOut=document.getElementById('termOut'), termCmd=document.getElementById('termCmd');
async function runTerm(){
  const command=termCmd.value.trim(); if(!command) return;
  const shell=document.getElementById('termShell').value;
  termOut.textContent += '\n$ '+command+'\n'; termCmd.value='';
  termOut.scrollTop=termOut.scrollHeight;
  try{
    const r=await fetch('/api/exec',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({command,shell})});
    const d=await r.json();
    termOut.textContent += (d.output||'');
    const tag=document.createElement('div'); tag.className=d.exit_code===0?'ec0':'ecN';
    tag.textContent='[exit '+d.exit_code+']'; termOut.appendChild(tag);
  }catch(e){ termOut.textContent += 'error: '+e+'\n'; }
  termOut.scrollTop=termOut.scrollHeight;
}
document.getElementById('termRun').onclick=runTerm;
termCmd.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();runTerm();}});

// ---- Git ----
const gitOut=document.getElementById('gitOut');
function renderGit(text){
  gitOut.innerHTML='';
  (text||'').split('\n').forEach(line=>{
    let c=''; if(line.startsWith('+')&&!line.startsWith('+++'))c='add';
    else if(line.startsWith('-')&&!line.startsWith('---'))c='del';
    else if(line.startsWith('@@'))c='hunk';
    const ln=document.createElement('div'); ln.className=c; ln.textContent=line; gitOut.appendChild(ln);
  });
}
async function git(action,extra){
  const r=await fetch('/api/git',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({action},extra||{}))});
  const d=await r.json(); renderGit(d.output);
}
document.querySelectorAll('[data-git]').forEach(b=>b.onclick=()=>git(b.dataset.git));
document.getElementById('gitCommit').onclick=()=>{
  const message=document.getElementById('gitMsg').value.trim();
  if(!message){renderGit('enter a commit message first.');return;}
  git('commit',{message}).then(()=>{document.getElementById('gitMsg').value='';});
};

// ---- Web server ----
const srvOut=document.getElementById('srvOut');
function renderSrv(d){
  if(d.running){ srvOut.innerHTML='serving working dir at <a class="link" target="_blank" href="'+d.url+'">'+d.url+'</a>'; }
  else{ srvOut.textContent='server stopped.'; }
}
async function srv(action){
  const port=document.getElementById('srvPort').value;
  const r=await fetch('/api/server',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action,port:parseInt(port)||8080})});
  renderSrv(await r.json());
}
document.getElementById('srvStart').onclick=()=>srv('start');
document.getElementById('srvStop').onclick=()=>srv('stop');
document.getElementById('srvStatus').onclick=()=>srv('status');

// ---- Editor (in-browser IDE) : syntax highlighting + multi-file tabs ----
const edFile=document.getElementById('edFile'), edText=document.getElementById('edText'),
      edStatus=document.getElementById('edStatus'), edHL=document.getElementById('edHL'),
      edHLpre=document.getElementById('edHLpre'), edTabs=document.getElementById('edTabs'),
      edNums=document.getElementById('edNums');
const LINE_H=18.75; // 12.5px font * 1.5 line-height

// --- tiny zero-dependency syntax highlighter ---
function esc(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
const KW={
  python:'def class return if elif else for while import from as with try except finally raise in not and or is None True False lambda yield global nonlocal pass break continue assert del async await self print',
  js:'function return if else for while var let const new class extends import export default from typeof instanceof in of await async yield try catch finally throw switch case break continue this null true false undefined delete void do',
  shell:'if then fi else elif for do done case esac in function while until select export local return'};
function kw(l){return '\\b(?:'+KW[l].trim().split(/\s+/).join('|')+')\\b';}
const STR="'(?:\\\\.|[^'\\\\])*'|\"(?:\\\\.|[^\"\\\\])*\"";
const LANGS={
  python:{comment:'#.*',string:"'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"|"+STR,keyword:kw('python'),number:'\\b\\d[\\d_]*\\.?\\d*\\b'},
  js:{comment:'//.*|/\\*[\\s\\S]*?\\*/',string:STR+"|`(?:\\\\.|[^`\\\\])*`",keyword:kw('js'),number:'\\b\\d[\\d_]*\\.?\\d*\\b'},
  json:{string:'"(?:\\\\.|[^"\\\\])*"',keyword:'\\b(?:true|false|null)\\b',number:'-?\\b\\d[\\d_]*\\.?\\d*(?:[eE][+-]?\\d+)?\\b'},
  css:{comment:'/\\*[\\s\\S]*?\\*/',atrule:'@[\\w-]+',string:STR,number:'-?\\b\\d*\\.?\\d+(?:px|em|rem|%|vh|vw|s|ms|fr|deg)?\\b'},
  html:{comment:'<!--[\\s\\S]*?-->',tag:'</?[a-zA-Z][\\w:-]*|/?>',string:STR},
  shell:{comment:'#.*',string:STR,keyword:kw('shell'),number:'\\b\\d+\\b'},
  md:{heading:'^#{1,6}.*',code:'```[\\s\\S]*?```|`[^`]*`'}};
const EXT={py:'python',pyw:'python',js:'js',jsx:'js',ts:'js',tsx:'js',mjs:'js',json:'json',
  css:'css',scss:'css',html:'html',htm:'html',xml:'html',sh:'shell',bash:'shell',md:'md',markdown:'md'};
function langFor(path){const m=(path||'').match(/\.([A-Za-z0-9]+)$/);return m?EXT[m[1].toLowerCase()]:null;}
const RX={};
function compile(lang){if(RX[lang])return RX[lang];const spec=LANGS[lang];const keys=Object.keys(spec);
  RX[lang]={keys,re:new RegExp(keys.map(k=>'(?<'+k+'>'+spec[k]+')').join('|'),'gms')};return RX[lang];}
function highlight(code,lang){
  if(!lang||!LANGS[lang])return esc(code);
  const {re}=compile(lang); re.lastIndex=0; let out='',last=0,m;
  while((m=re.exec(code))){
    out+=esc(code.slice(last,m.index));
    const g=Object.keys(m.groups).find(k=>m.groups[k]!==undefined);
    out+='<span class="t-'+g+'">'+esc(m[0])+'</span>';
    last=m.index+m[0].length;
    if(m[0].length===0)re.lastIndex++;
  }
  out+=esc(code.slice(last)); return out;
}

// --- multi-file tab state ---
let tabs=[], active=-1;
function activeTab(){return active>=0?tabs[active]:null;}
function updateGutter(){
  const n=(edText.value.match(/\n/g)||[]).length+1;
  let s=''; for(let i=1;i<=n;i++) s+=i+'\n';
  edNums.textContent=s;
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';
}
function render(){
  edHL.innerHTML=highlight(edText.value, active>=0?langFor(tabs[active].path):null);
  edHLpre.scrollTop=edText.scrollTop; edHLpre.scrollLeft=edText.scrollLeft;
  updateGutter();
}
function renderTabs(){
  edTabs.innerHTML='';
  tabs.forEach((t,i)=>{
    const el=document.createElement('div'); el.className='etab'+(i===active?' active':'')+(t.dirty?' dirty':'');
    const name=document.createElement('span'); name.className='name'; name.textContent=t.path.split('/').pop();
    name.title=t.path; name.onclick=()=>activate(i);
    const x=document.createElement('span'); x.className='x'; x.textContent='×';
    x.onclick=(e)=>{e.stopPropagation();closeTab(i);};
    el.appendChild(name); el.appendChild(x); edTabs.appendChild(el);
  });
}
function activate(i){
  active=i; const t=tabs[i];
  edText.value=t.content; edFile.value=t.path;
  edStatus.innerHTML='<span class="muted">'+t.path+'</span>';
  renderTabs(); render(); edText.focus();
}
async function openPath(path){
  if(!path)return;
  const existing=tabs.findIndex(t=>t.path===path);
  if(existing>=0){activate(existing);return;}
  const r=await fetch('/api/fs/read',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path})});
  const d=await r.json();
  if(d.error){edStatus.innerHTML='<span class="ecN">'+d.error+'</span>';return;}
  tabs.push({path,content:d.content,clean:d.content,dirty:false}); activate(tabs.length-1);
}
function closeTab(i){
  if(tabs[i].dirty && !confirm('Discard unsaved changes in '+tabs[i].path+'?'))return;
  tabs.splice(i,1);
  if(tabs.length===0){active=-1;edText.value='';edFile.value='';edStatus.textContent='';renderTabs();render();return;}
  activate(Math.min(i,tabs.length-1));
}
async function saveActive(){
  const t=activeTab(); if(!t){edStatus.textContent='no file open';return;}
  t.content=edText.value;
  const r=await fetch('/api/fs/write',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({path:t.path,content:t.content})});
  const d=await r.json();
  if(d.error){edStatus.innerHTML='<span class="ecN">'+d.error+'</span>';return;}
  t.clean=t.content; t.dirty=false; renderTabs();
  edStatus.innerHTML='<span class="ok">saved '+t.path+' ('+d.bytes+' bytes)</span>';
}
async function loadTree(){
  const r=await fetch('/api/fs/tree',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await r.json();
  edFile.innerHTML='<option value="">— open a file —</option>';
  (d.files||[]).forEach(f=>{const o=document.createElement('option');o.value=f;o.textContent=f;edFile.appendChild(o);});
  if(active>=0)edFile.value=tabs[active].path;
}
edFile.onchange=()=>openPath(edFile.value);
document.getElementById('edReload').onclick=loadTree;
document.getElementById('edSave').onclick=saveActive;
document.getElementById('edRevert').onclick=()=>{const t=activeTab();if(!t)return;
  t.content=t.clean;t.dirty=false;edText.value=t.clean;renderTabs();render();
  edStatus.innerHTML='<span class="muted">reverted '+t.path+'</span>';};
edText.addEventListener('input',()=>{const t=activeTab();if(t){t.content=edText.value;
  const d=t.content!==t.clean; if(d!==t.dirty){t.dirty=d;renderTabs();}} render();});
edText.addEventListener('scroll',()=>{edHLpre.scrollTop=edText.scrollTop;edHLpre.scrollLeft=edText.scrollLeft;
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';});
edText.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveActive();}
  if((e.ctrlKey||e.metaKey)&&(e.key==='f'||e.key==='F')){e.preventDefault();openFind();}
  if(e.key==='Tab'){e.preventDefault();const s=edText.selectionStart,en=edText.selectionEnd;
    edText.value=edText.value.slice(0,s)+'    '+edText.value.slice(en);
    edText.selectionStart=edText.selectionEnd=s+4;edText.dispatchEvent(new Event('input'));}
});

// ---- find within the editor (Ctrl/Cmd+F) ----
const edFindBar=document.getElementById('edFindBar'), edFindInput=document.getElementById('edFindInput'),
      edFindCnt=document.getElementById('edFindCnt');
let findMatches=[], findIdx=-1;
function openFind(){
  edFindBar.classList.add('on');
  const sel=edText.value.substring(edText.selectionStart,edText.selectionEnd);
  if(sel && sel.length<60 && !sel.includes('\n')) edFindInput.value=sel;
  edFindInput.focus(); edFindInput.select(); runFind();
}
function closeFind(){ edFindBar.classList.remove('on'); edText.focus(); }
function runFind(){
  const q=edFindInput.value; findMatches=[]; findIdx=-1;
  if(q){ const hay=edText.value.toLowerCase(), needle=q.toLowerCase();
    let i=hay.indexOf(needle);
    while(i!==-1){ findMatches.push(i); i=hay.indexOf(needle, i+Math.max(1,needle.length)); } }
  if(findMatches.length){ findIdx=0; jumpFind(); }
  else { edFindCnt.textContent=q?'0/0':'0/0'; }
}
function jumpFind(){
  if(findIdx<0||!findMatches.length) return;
  const start=findMatches[findIdx], end=start+edFindInput.value.length;
  edText.setSelectionRange(start,end); // visible (greyed) without stealing focus from the find box
  const line=(edText.value.slice(0,start).match(/\n/g)||[]).length;
  edText.scrollTop=Math.max(0, line*LINE_H - edText.clientHeight/2);
  edNums.style.transform='translateY('+(-edText.scrollTop)+'px)';
  edHLpre.scrollTop=edText.scrollTop;
  edFindCnt.textContent=(findIdx+1)+'/'+findMatches.length;
}
function nextFind(d){ if(!findMatches.length)return; findIdx=(findIdx+d+findMatches.length)%findMatches.length; jumpFind(); }
edFindInput.addEventListener('input', runFind);
edFindInput.addEventListener('keydown', e=>{
  if(e.key==='Enter'){ e.preventDefault(); nextFind(e.shiftKey?-1:1); }
  if(e.key==='Escape'){ e.preventDefault(); closeFind(); }
});
document.getElementById('edFindNext').onclick=()=>nextFind(1);
document.getElementById('edFindPrev').onclick=()=>nextFind(-1);
document.getElementById('edFindClose').onclick=closeFind;

// ---- replace ----
const edReplaceInput=document.getElementById('edReplaceInput');
function replaceOne(){
  if(findIdx<0||!findMatches.length||!edFindInput.value)return;
  const start=findMatches[findIdx], end=start+edFindInput.value.length;
  edText.value=edText.value.slice(0,start)+edReplaceInput.value+edText.value.slice(end);
  edText.dispatchEvent(new Event('input'));   // updates tab/dirty + re-highlight
  runFind();                                  // recompute (jumps to first remaining)
}
function replaceAll(){
  if(!edFindInput.value||!findMatches.length)return;
  let v=edText.value; const flen=edFindInput.value.length, rep=edReplaceInput.value, n=findMatches.length;
  for(let i=findMatches.length-1;i>=0;i--){const s=findMatches[i]; v=v.slice(0,s)+rep+v.slice(s+flen);}
  edText.value=v; edText.dispatchEvent(new Event('input'));
  runFind(); edFindCnt.textContent='replaced '+n;
}
document.getElementById('edReplaceOne').onclick=replaceOne;
document.getElementById('edReplaceAll').onclick=replaceAll;
edReplaceInput.addEventListener('keydown',e=>{
  if(e.key==='Enter'){e.preventDefault();replaceOne();}
  if(e.key==='Escape'){e.preventDefault();closeFind();}
});
// re-read open, unmodified files after the agent edits them on disk
async function refreshOpen(){
  for(const t of tabs){ if(t.dirty)continue;
    const r=await fetch('/api/fs/read',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({path:t.path})});
    const d=await r.json(); if(!d.error){t.content=d.content;t.clean=d.content;}
  }
  if(active>=0){edText.value=tabs[active].content;render();}
}
window.__edRefresh=refreshOpen;
loadTree();

// ---- Providers / API keys panel ----
const provList=document.getElementById('provList');
const usageBar=document.getElementById('usageBar');
let provDlSeq=0;
function fmt(n){ return (n||0).toLocaleString(); }
function renderUsage(u){
  if(!u){ return; }
  const cost = u.cost_known ? ('$'+(u.est_cost_usd||0).toFixed(4)) : ('~$'+(u.est_cost_usd||0).toFixed(4)+' (partial)');
  let html='usage — requests <b>'+fmt(u.requests)+'</b> · in <b>'+fmt(u.input_tokens)
    +'</b> tok · out <b>'+fmt(u.output_tokens)+'</b> tok · est. cost <b>'+cost+'</b>';
  if(u.budget_warning){ html+='<br><span class="bwarn">⚠ '+u.budget_warning+'</span>'; }
  usageBar.innerHTML=html;
}
async function refreshUsage(){ try{ const r=await fetch('/api/usage'); renderUsage(await r.json()); }catch(_){} }
async function loadProviders(){
  const r=await fetch('/api/providers'); const d=await r.json();
  provList.innerHTML='';
  d.providers.forEach(p=>provList.appendChild(provCard(p)));
  renderUsage(d.usage);
}
function provCard(p){
  const card=el('pcard'+(p.active?' active':''));
  const t=el('ptitle');
  t.appendChild(el('pdot'+(p.active?' on':(p.configured?' ok':''))));
  const nm=document.createElement('b'); nm.textContent=p.label; t.appendChild(nm);
  const sp=document.createElement('span'); sp.style.flex='1'; t.appendChild(sp);
  const badge=el('badge'+(p.active?' act':''));
  badge.textContent=p.active?'active':(p.configured?(p.source||'set'):(p.needs_key?'no key':'local'));
  t.appendChild(badge); card.appendChild(t);

  let keyInput=null;
  if(p.needs_key){
    keyInput=document.createElement('input'); keyInput.type='password'; keyInput.autocomplete='off';
    keyInput.placeholder = p.configured ? ('saved '+p.key_masked+' — type new to replace') : ('API key  ('+(p.env||'')+')');
    card.appendChild(keyInput);
  } else {
    const note=el('muted','local runtime — no API key required'); note.style.fontSize='11px'; card.appendChild(note);
  }
  // model field backed by a datalist that "Test" fills with the live model list
  const modelInput=document.createElement('input'); modelInput.value=p.model||''; modelInput.placeholder='model';
  const dl=document.createElement('datalist'); dl.id='dl_'+p.name+'_'+(provDlSeq++); modelInput.setAttribute('list', dl.id);
  card.appendChild(modelInput); card.appendChild(dl);

  const adv=el('padv','▸ advanced (base URL)');
  const advBody=el('padv-body');
  const baseInput=document.createElement('input'); baseInput.value=p.base_url||''; baseInput.placeholder='base URL';
  advBody.appendChild(baseInput);
  adv.onclick=()=>{const on=advBody.classList.toggle('on'); adv.textContent=(on?'▾':'▸')+' advanced (base URL)';};
  card.appendChild(adv); card.appendChild(advBody);

  // monthly budget + spend
  const budgetInput=document.createElement('input'); budgetInput.type='number';
  budgetInput.step='0.01'; budgetInput.min='0';
  budgetInput.value = p.budget_usd ? p.budget_usd : '';
  budgetInput.placeholder='monthly budget $ (0 = none)';
  card.appendChild(budgetInput);
  if(p.budget_usd>0 || p.month_spent_usd>0){
    const spent=el('ptest '+(p.over_budget?'err':(p.near_budget?'err':'muted')));
    spent.textContent = p.budget_usd>0
      ? ('this month: $'+(p.month_spent_usd||0).toFixed(4)+' / $'+p.budget_usd.toFixed(2)
          +(p.over_budget?'  ⚠ over budget':(p.near_budget?'  ⚠ nearing':'')))
      : ('this month: $'+(p.month_spent_usd||0).toFixed(4));
    card.appendChild(spent);
  }

  const status=el('ptest muted',''); card.appendChild(status);

  const acts=el('pacts');
  const testBtn=document.createElement('button'); testBtn.textContent='Test';
  testBtn.onclick=()=>testProvider(p.name, keyInput, modelInput, baseInput, budgetInput, dl, status, testBtn);
  const saveBtn=document.createElement('button'); saveBtn.textContent='Save';
  saveBtn.onclick=()=>saveProvider(p.name, keyInput, modelInput, baseInput, budgetInput, false);
  const useBtn=document.createElement('button'); useBtn.textContent=p.active?'In use':'Use';
  useBtn.disabled=!!p.active;
  useBtn.onclick=()=>saveProvider(p.name, keyInput, modelInput, baseInput, budgetInput, true);
  acts.appendChild(testBtn); acts.appendChild(saveBtn); acts.appendChild(useBtn);
  card.appendChild(acts);
  return card;
}
async function testProvider(name, keyInput, modelInput, baseInput, budgetInput, dl, status, btn){
  // Persist any typed key/base first so the test uses current values.
  if((keyInput && keyInput.value) || (baseInput && baseInput.value)){
    await saveProviderQuiet(name, keyInput, modelInput, baseInput, budgetInput);
  }
  status.className='ptest muted'; status.textContent='testing…'; btn.disabled=true;
  try{
    const r=await fetch('/api/providers/test',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({provider:name})});
    const d=await r.json();
    if(d.ok){
      status.className='ptest ok'; status.textContent='✓ connected — '+d.count+' models';
      dl.innerHTML=''; (d.models||[]).forEach(m=>{const o=document.createElement('option');o.value=m;dl.appendChild(o);});
    } else {
      status.className='ptest err'; status.textContent='✗ '+(d.error||'failed').split('\n')[0].slice(0,140);
    }
  }catch(e){ status.className='ptest err'; status.textContent='✗ '+e; }
  btn.disabled=false;
}
function provBody(name, keyInput, modelInput, baseInput, budgetInput, makeActive){
  const body={provider:name, model:modelInput.value, base_url:baseInput.value};
  if(keyInput && keyInput.value) body.api_key=keyInput.value;
  if(budgetInput && budgetInput.value!=='') body.budget=parseFloat(budgetInput.value)||0;
  if(makeActive) body.make_active=true;
  return body;
}
async function saveProviderQuiet(name, keyInput, modelInput, baseInput, budgetInput){
  await fetch('/api/providers',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(provBody(name, keyInput, modelInput, baseInput, budgetInput, false))});
}
async function saveProvider(name, keyInput, modelInput, baseInput, budgetInput, makeActive){
  await fetch('/api/providers',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(provBody(name, keyInput, modelInput, baseInput, budgetInput, makeActive))});
  await loadProviders();
  loadInfo();
}
const provTabBtn=document.querySelector('.tabs button[data-tab="providers"]');
if(provTabBtn) provTabBtn.addEventListener('click', loadProviders);
loadProviders();

loadInfo();
</script>
</body>
</html>
"""
