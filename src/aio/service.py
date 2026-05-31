"""Service layer for the `aio --web` dashboard.

Houses :class:`EventUI` (records agent/tool events instead of printing them)
and :class:`AgentService` (per-conversation agents, sessions, providers, MCP,
git/shell/server panels, telemetry) — everything that sits between the HTTP
handler in :mod:`aio.web` and the underlying :class:`aio.agent.Agent`.
"""

from __future__ import annotations

import difflib
import threading
from http.server import ThreadingHTTPServer
from typing import Any

from .agent import Agent
from .catalog import DEFAULT_MCP_SERVERS, MCP_CATALOG
from .config import PROVIDER_DEFAULTS, Config, KeyStore
from .pricing import estimate_cost
from .providers import ProviderError, build_provider
from .service_panels import PanelsMixin
from .service_providers import ProvidersMixin
from .service_sessions import SessionsMixin
from .tokens import active_backend as _token_backend
from .tools import ToolContext, default_registry


class EventUI:
    """A UI implementation that records events instead of printing them.

    Implements the same surface the agent and tools call, so it can be dropped
    in wherever a terminal :class:`aio.ui.UI` is expected.
    """

    def __init__(self, broker=None) -> None:
        self.events: list[dict[str, Any]] = []
        # Optional live callback: when set, each event is delivered immediately
        # (used for Server-Sent Events streaming) in addition to being stored.
        self.sink = None
        # ApprovalBroker for the interactive tool-approval gate (web mode).
        self._broker = broker

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
        # Without a broker, or with no live stream to ask over, we can't run an
        # interactive prompt -> approve (the non-streaming fallback path).
        if self._broker is None or self.sink is None:
            self.tool_call(name, args)
            return "yes"
        # Gated: ask the browser and block this turn until it answers (or the
        # broker times out, which denies). Read-only tools never reach here
        # because they set needs_approval = False.
        rid = self._broker.open()
        self._emit({"type": "tool_approval", "id": rid, "name": name, "args": args})
        decision = self._broker.wait(rid)
        self._emit({"type": "tool_approval_resolved", "id": rid, "decision": decision})
        if decision in ("yes", "always"):
            self.tool_call(name, args)
        return decision




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


class AgentService(SessionsMixin, ProvidersMixin, PanelsMixin):
    """Thread-safe wrapper around an Agent for the web server.

    Behaviour is split across mixins (see service_sessions/providers/panels);
    this class owns construction, the agent loop, approvals and core state.
    """

    def __init__(self, config: Config, gated: bool = True) -> None:
        self.config = config
        # Tool-approval gate: when gated (default), side-effecting tools must be
        # approved over the live stream; otherwise they auto-approve (legacy).
        from .security import ApprovalBroker

        self.gated = bool(gated)
        self._approvals = ApprovalBroker()
        self.ui = EventUI(self._approvals)
        self._lock = threading.Lock()
        self._usage_lock = threading.Lock()  # guards self.usage across concurrent chats
        # API keys entered via the dashboard are persisted here and overlaid on
        # top of env/config so they survive restarts.
        self.keys = KeyStore.load()
        self.keys.apply_to(self.config)
        from .telemetry import Telemetry
        self.telemetry = Telemetry.from_config(self.config.telemetry)
        # cumulative token usage / cost estimate for this session
        self.usage: dict[str, Any] = {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read": 0, "cache_write": 0,
            "est_cost_usd": 0.0, "cost_known": True, "by_provider": {},
        }
        # multiple concurrent conversations (tabs): id -> message history
        self.conversations: dict[str, list] = {"default": []}
        self._summaries: dict[str, str] = {}     # per-conversation compaction summary
        self._store = None                       # lazily-opened SQLite session store
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
        self._restore_conversations()  # bring back live tabs from a previous run
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
            # gated mode -> route side-effecting tools through the approval gate;
            # auto mode -> approve everything (opt-in, legacy behaviour).
            auto_approve=not self.gated,
            allow_outside_workdir=self.config.allow_outside_workdir,
            checkpoints=self._checkpoints,
            todos=self._todos,
            permissions=dict(self.config.permissions),
        )
        registry = default_registry()
        for tool in self._mcp_tools:
            registry.register(tool)
        system_prompt = self.config.system_prompt + OUTPUT_STYLES.get(self.config.output_style, "")
        if self.config.project_map:
            system_prompt += "\n\n# Project map (auto-generated)\n" + self.config.project_map
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
            auto_context=self.config.auto_context,
            auto_context_k=self.config.auto_context_results,
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
            agent = self._make_agent_for(EventUI(self._approvals), msgs,
                                         self._summaries.get(conv_id, ""), provider=prov)
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
            "token_backend": _token_backend(),
            "tool_approval": "gated" if self.gated else "auto",
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
            import time as _t
            t0 = _t.time(); ok = True
            try:
                final = agent.run(message, images=images)
            except ProviderError as exc:
                ui.error(str(exc))
                final = ""; ok = False
            self._summaries[conv_id or "default"] = agent.summary
            self._todos = agent.ctx.todos
            self._accumulate_usage(agent)
            self._record_telemetry(agent, ok, _t.time() - t0)
            self._persist_conversation(conv_id)
            return {"events": ui.drain(), "final": final, "usage": self.usage_info()}

    def _record_telemetry(self, agent, ok: bool, duration_s: float) -> None:
        try:
            self.telemetry.record_turn(
                provider=self.config.provider, model=self.config.active.model,
                usage=getattr(agent, "run_usage", {}) or {}, duration_s=duration_s, ok=ok,
            )
        except Exception:  # pragma: no cover - telemetry must never break a turn
            pass

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
            import time as _t
            t0 = _t.time(); ok = True
            try:
                final = agent.run(message, images=images)
            except ProviderError as exc:
                emit({"type": "error", "text": str(exc)})
                final = ""; ok = False
            finally:
                ui.sink = None
            self._record_telemetry(agent, ok, _t.time() - t0)
            self._summaries[conv_id or "default"] = agent.summary
            self._todos = agent.ctx.todos
            self._accumulate_usage(agent)
            self._persist_conversation(conv_id)
            emit({"type": "done", "final": final, "usage": self.usage_info()})

    def resolve_approval(self, request_id: str, decision: str) -> dict[str, Any]:
        """Record a user's allow/deny/always decision for a pending tool call."""
        ok = self._approvals.resolve(request_id or "", decision or "no")
        return {"ok": ok}

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
            cid = conv_id or "default"
            self.conversations[cid] = []
            self._summaries.pop(cid, None)
            self._select_conv(conv_id)
            self._drop_persisted_conversation(cid)
            return {"ok": True}

    def close_conversation(self, conv_id: str) -> dict[str, Any]:
        with self._lock:
            self.conversations.pop(conv_id, None)
            self._conv_agents.pop(conv_id, None)
            self._conv_locks.pop(conv_id, None)
            self._summaries.pop(conv_id, None)
            if self._active_conv == conv_id:
                self._active_conv = "default"
            self._drop_persisted_conversation(conv_id)
            return {"ok": True}

    def _drop_persisted_conversation(self, conv_id: str) -> None:
        try:
            self._session_store().delete_conversation(conv_id or "default")
        except Exception:  # pragma: no cover - never block on the store
            pass

    # -- MCP panel -------------------------------------------------------
    def mcp_info(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for t in self._mcp_tools:
            srv = t.name.split("__", 1)[0]
            counts[srv] = counts.get(srv, 0) + 1
        running = {getattr(s, "name", None) for s in self._mcp_servers}
        servers = []
        for c in self._mcp_configs():
            nm = c.get("name") or ""
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
