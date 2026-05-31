"""The core agent loop: model <-> tools until the task is done."""

from __future__ import annotations

import json

from .providers import Message, Provider, ToolCall
from .tools import ToolContext, ToolError, ToolRegistry
from .ui import UI

# Tools allowed while in read-only "plan mode".
READONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "git_status", "git_diff",
                  "find_symbol", "search_code"}

PLAN_MODE_NOTE = """

# PLAN MODE (read-only)
You are in PLAN MODE. Do NOT modify anything — only inspect with read_file,
list_dir, glob, grep, git_status and git_diff. Produce a clear, step-by-step plan
of what you WOULD do (files to change, commands to run), then stop and wait for
approval. Do not attempt write_file, edit_file, run_shell or git_commit.
"""


class Agent:
    """Drives a conversation, executing tool calls until the model stops."""

    def __init__(
        self,
        provider: Provider,
        tools: ToolRegistry,
        ctx: ToolContext,
        ui: UI,
        system_prompt: str,
        max_steps: int = 50,
        stream: bool = False,
        auto_compact: bool = True,
        context_limit: int = 120_000,
        plan_mode: bool = False,
        hooks=None,
        auto_context: bool = False,
        auto_context_k: int = 5,
        max_tool_calls: int = 0,
        deadline_s: float = 0.0,
        max_reflections: int = 0,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.ctx = ctx
        self.ui = ui
        self.hooks = hooks  # optional HookRunner (#6)
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        #: when True and the UI supports token(), stream the reply token-by-token
        self.stream = stream
        self.auto_compact = auto_compact
        self.context_limit = context_limit
        self.compact_keep = 6  # recent messages kept verbatim during compaction
        self._token_factor = 1.0   # self-calibration multiplier for token estimates (#1)
        self._last_raw_estimate = 0
        import threading
        self._usage_lock = threading.Lock()  # guards run_usage under parallel sub-agents
        self.plan_mode = plan_mode
        #: auto-retrieve relevant code into context each turn (RAG-lite, BM25)
        self.auto_context = auto_context
        self.auto_context_k = auto_context_k
        self._auto_context = ""        # transient retrieved block for the current turn
        #: backpressure: cap tool calls / wall-clock per run() (0 = unlimited)
        self.max_tool_calls = int(max_tool_calls)
        self.deadline_s = float(deadline_s)
        #: self-verify and continue up to N times after an answer (0 = off)
        self.max_reflections = int(max_reflections)
        self.messages: list[Message] = []
        #: running summary of compacted (older) messages, fed via the system prompt
        self.summary: str = ""
        #: token usage accumulated during the most recent run()
        self.run_usage: dict[str, int] = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        # expose sub-agent spawning to the task tool (#5)
        self.ctx.spawn_subagent = self._spawn_subagent

    def _spawn_subagent(self, prompt: str) -> str:
        """Run an isolated child agent (own context, same tools/provider)."""
        if hasattr(self.ui, "info"):
            self.ui.info(f"↳ sub-agent: {prompt[:80]}")
        child_ctx = ToolContext(
            workdir=self.ctx.workdir,
            ui=self.ui,
            auto_approve=self.ctx.auto_approve,
            allow_outside_workdir=self.ctx.allow_outside_workdir,
            approved=self.ctx.approved,
            checkpoints=self.ctx.checkpoints,   # share so parent can rewind sub-agent edits
            depth=self.ctx.depth + 1,
        )
        child = Agent(
            provider=self.provider,
            tools=self.tools,
            ctx=child_ctx,
            ui=self.ui,
            system_prompt=self.system_prompt,
            max_steps=self.max_steps,
            stream=False,            # sub-agents don't stream tokens to the UI
            auto_compact=self.auto_compact,
            context_limit=self.context_limit,
            hooks=self.hooks,
        )
        result = child.run(prompt)
        # roll the sub-agent's token usage into the parent's tally (thread-safe:
        # parallel_tasks may run several sub-agents concurrently)
        with self._usage_lock:
            for k in ("requests", "input_tokens", "output_tokens", "cache_read", "cache_write"):
                self.run_usage[k] = self.run_usage.get(k, 0) + child.run_usage.get(k, 0)
        return result or "(sub-agent finished with no summary)"

    def reset(self) -> None:
        self.messages = []
        self.summary = ""

    @staticmethod
    def _normalise_usage(usage: dict | None) -> tuple[int, int]:
        """Return (input_tokens, output_tokens) across provider formats."""
        if not usage:
            return 0, 0
        inp = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        out = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        return int(inp), int(out)

    # -- prompt / tool shaping (plan mode + compaction summary) ----------
    def _effective_system(self) -> str:
        parts = [self.system_prompt]
        if self.summary:
            parts.append(f"\n\n# Summary of earlier conversation\n{self.summary}")
        if self.plan_mode:
            parts.append(PLAN_MODE_NOTE)
        if self._auto_context:
            parts.append(
                "\n\n# Retrieved code context (auto, keyword search)\n"
                "These snippets were retrieved by relevance to the latest request and "
                "may be incomplete or irrelevant — verify by reading the files before "
                "relying on them.\n\n" + self._auto_context
            )
        return "".join(parts)

    # -- retrieval-augmented context (RAG-lite) --------------------------
    def _retrieve_context(self, query: str) -> None:
        """Populate self._auto_context with BM25-ranked snippets for ``query``."""
        self._auto_context = ""
        if not self.auto_context or not (query or "").strip():
            return
        try:
            if getattr(self.ctx, "code_searcher", None) is None:
                from .search import CodeSearcher

                self.ctx.code_searcher = CodeSearcher(self.ctx.workdir).build()
            hits = self.ctx.code_searcher.search(query, k=self.auto_context_k)
        except Exception:  # pragma: no cover - retrieval must never break a turn
            return
        if not hits:
            return
        blocks, budget = [], 6000
        for h in hits:
            snippet = "\n".join(h["snippet"].splitlines()[:18])
            block = f"## {h['path']}:{h['start_line']}-{h['end_line']}\n{snippet}"
            if budget - len(block) < 0:
                break
            budget -= len(block)
            blocks.append(block)
        self._auto_context = "\n\n".join(blocks)

    def _effective_specs(self) -> list[dict]:
        specs = self.tools.specs()
        if self.plan_mode:
            specs = [s for s in specs if s["name"] in READONLY_TOOLS]
        return specs

    # -- context compaction ----------------------------------------------
    def _raw_tokens(self, msgs: list[Message]) -> int:
        from .tokens import count_text

        total = count_text(self.summary)
        for m in msgs:
            total += count_text(m.content or "")
            for tc in m.tool_calls:
                total += count_text(tc.name) + count_text(json.dumps(tc.arguments))
        return total

    def _estimate_tokens(self, msgs: list[Message] | None = None) -> int:
        msgs = self.messages if msgs is None else msgs
        return int(self._raw_tokens(msgs) * self._token_factor)

    def _calibrate_tokens(self, turn_usage: dict | None) -> None:
        """Nudge the estimate toward the provider's reported input tokens (#1)."""
        if not turn_usage:
            return
        actual = (turn_usage.get("input_tokens") or turn_usage.get("prompt_tokens") or 0)
        actual += turn_usage.get("cache_read_input_tokens", 0) or 0
        actual += turn_usage.get("cache_creation_input_tokens", 0) or 0
        if actual <= 0:
            return
        est = self._last_raw_estimate
        if est and est > 0:
            ratio = actual / est
            if 0.2 < ratio < 5.0:  # ignore wild outliers
                # exponential moving average toward the observed ratio
                self._token_factor = 0.7 * self._token_factor + 0.3 * ratio

    def _summarize(self, msgs: list[Message]) -> str:
        lines = []
        for m in msgs:
            if m.role == "user":
                lines.append("User: " + (m.content or "")[:2000])
            elif m.role == "assistant":
                if m.content:
                    lines.append("Assistant: " + m.content[:2000])
                for tc in m.tool_calls:
                    lines.append(f"Assistant->{tc.name}({json.dumps(tc.arguments)[:200]})")
            elif m.role == "tool":
                lines.append("Tool result: " + (m.content or "")[:400])
        transcript = "\n".join(lines)[:12000]
        prompt = (
            "Summarise this earlier part of a coding session concisely. Preserve the "
            "user's goals, decisions, files created/edited, commands run and their "
            "outcomes, and any open tasks.\n\n" + transcript
        )
        try:
            turn = self.provider.chat([Message(role="user", content=prompt)], tools=[], system=None)
            return (turn.content or "").strip() or transcript[:2000]
        except Exception:  # pragma: no cover - network fallback
            return transcript[:2000]

    def _maybe_compact(self) -> None:
        """Summarise older messages into self.summary when context grows large."""
        if not self.auto_compact:
            return
        if self._estimate_tokens() < int(self.context_limit * 0.8):
            return
        if len(self.messages) <= self.compact_keep + 2:
            return
        # keep the last compact_keep messages, extending the cut forward so the
        # retained tail begins on a 'user' message (valid first message + role
        # alternation, no orphan tool_result)
        cut = len(self.messages) - self.compact_keep
        while cut < len(self.messages) and self.messages[cut].role != "user":
            cut += 1
        if cut <= 0 or cut >= len(self.messages):
            return
        head, tail = self.messages[:cut], self.messages[cut:]
        new_summary = self._summarize(head)
        self.summary = (self.summary + "\n" + new_summary).strip() if self.summary else new_summary
        self.messages = tail
        if hasattr(self.ui, "info"):
            self.ui.info(f"compacted {len(head)} earlier messages into the summary")

    def run(self, user_input: str, images: list[dict] | None = None) -> str:
        """Run a user turn; if reflection is enabled, verify and finish the work.

        Plain mode is a single tool-loop. With ``max_reflections > 0`` the agent
        also runs a verify→continue loop: after producing an answer it asks
        itself whether the task is actually complete, and if not, keeps going
        with the gap as a new instruction (bounded). This catches "looks done
        but isn't" — closer to plan→execute→verify→replan than a linear loop.
        """
        self.run_usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0,
                          "cache_read": 0, "cache_write": 0}
        result = self._run_once(user_input, images)
        for _ in range(self.max_reflections):
            gap = self._reflect()
            if gap is None:
                break
            if hasattr(self.ui, "info"):
                self.ui.info(f"self-review: not done yet — {gap[:120]}")
            result = self._run_once(
                f"Your previous attempt is incomplete: {gap}\n"
                "Continue and finish the task; do not repeat work already done.", None)
        return result

    def _reflect(self) -> str | None:
        """Ask the model to verify its own work; return a gap, or None if done."""
        probe = Message(role="user", content=(
            "Self-review: is the user's task now FULLY complete and verified "
            "(e.g. tests run and passing where relevant)? Reply with exactly "
            "'TASK_COMPLETE' if so, otherwise one short line stating what remains."))
        try:
            turn = self.provider.chat(self.messages + [probe], tools=[],
                                      system=self._effective_system())
        except Exception:  # pragma: no cover - never fail the run on a probe
            return None
        inp, out = self._normalise_usage(turn.usage)
        self.run_usage["requests"] += 1
        self.run_usage["input_tokens"] += inp
        self.run_usage["output_tokens"] += out
        text = (turn.content or "").strip()
        if not text or "TASK_COMPLETE" in text.upper():
            return None
        return text[:500]

    def _run_once(self, user_input: str, images: list[dict] | None = None) -> str:
        """One tool-loop turn to completion; returns the final assistant text."""

        import time as _time

        self.messages.append(Message(role="user", content=user_input, images=images or []))
        self._retrieve_context(user_input)
        self._maybe_compact()
        final_text = ""
        started = _time.time()
        tool_calls_made = 0

        use_stream = (
            self.stream and hasattr(self.ui, "token") and hasattr(self.provider, "stream_chat")
        )

        for _ in range(self.max_steps):
            self.ui.thinking()
            # remember what we estimated for this exact request, to calibrate after
            self._last_raw_estimate = self._raw_tokens(self.messages)
            if use_stream:
                turn = self.provider.stream_chat(
                    self.messages, tools=self._effective_specs(),
                    system=self._effective_system(), on_delta=self.ui.token,
                )
            else:
                turn = self.provider.chat(
                    self.messages, tools=self._effective_specs(), system=self._effective_system()
                )
            self._calibrate_tokens(turn.usage)
            inp, out = self._normalise_usage(turn.usage)
            self.run_usage["requests"] += 1
            self.run_usage["input_tokens"] += inp
            self.run_usage["output_tokens"] += out
            u = turn.usage or {}
            self.run_usage["cache_read"] += int(u.get("cache_read_input_tokens", 0) or 0)
            self.run_usage["cache_write"] += int(u.get("cache_creation_input_tokens", 0) or 0)
            self.messages.append(
                Message(role="assistant", content=turn.content, tool_calls=turn.tool_calls)
            )
            if turn.content:
                # In streaming mode the text was already delivered via token().
                if not use_stream:
                    self.ui.assistant(turn.content)
                final_text = turn.content

            if not turn.tool_calls:
                return final_text

            for call in turn.tool_calls:
                # backpressure: stop cleanly if a budget is set and exceeded
                if self.max_tool_calls and tool_calls_made >= self.max_tool_calls:
                    self.ui.warn(f"tool-call budget reached ({self.max_tool_calls}); stopping.")
                    return final_text
                if self.deadline_s and (_time.time() - started) > self.deadline_s:
                    self.ui.warn(f"time budget reached ({self.deadline_s:.0f}s); stopping.")
                    return final_text
                result = self._execute(call)
                tool_calls_made += 1
                self.messages.append(
                    Message(role="tool", content=result, tool_call_id=call.id, name=call.name)
                )

        self.ui.warn(f"Reached max steps ({self.max_steps}); stopping.")
        return final_text

    def _execute(self, call: ToolCall) -> str:
        tool = self.tools.get(call.name)
        if tool is None:
            self.ui.tool_result(f"unknown tool: {call.name}", error=True)
            return f"Error: unknown tool '{call.name}'."

        if self.plan_mode and call.name not in READONLY_TOOLS:
            self.ui.tool_result(f"blocked in plan mode: {call.name}", error=True)
            return "Error: plan mode is read-only; this tool is disabled. Present a plan instead."

        # Argument-scoped guardrail: refuse catastrophic shell commands outright,
        # even with approval/auto-approve (last line of defence).
        if call.name in ("run_shell", "run_background"):
            from .guard import dangerous_command

            reason = dangerous_command(call.arguments.get("command", ""))
            if reason:
                self.ui.tool_result(f"blocked by safety guardrail: {reason}", error=True)
                self._audit(call.name, call.arguments, "blocked", reason)
                return f"Error: refused — {reason}. This command is blocked by the safety guardrail."

        # Granular permissions (#7): explicit deny/allow rules override the
        # default approval flow.
        rule = self.ctx.permission(call.name)
        if rule == "deny":
            self.ui.tool_result(f"denied by permission rule: {call.name}", error=True)
            return f"Error: tool '{call.name}' is denied by the permission settings."
        pre_allowed = rule == "allow"

        # Approval flow for mutating tools.
        if (tool.needs_approval and not pre_allowed and not self.ctx.auto_approve
                and call.name not in self.ctx.approved):
            decision = self.ui.confirm(call.name, call.arguments)
            if decision == "no":
                return "Tool call rejected by user."
            if decision == "always":
                self.ctx.approved.add(call.name)
        else:
            self.ui.tool_call(call.name, call.arguments)

        # PreToolUse hooks (#6) — may block the call.
        if self.hooks is not None and self.hooks.has("PreToolUse"):
            pre = self.hooks.run("PreToolUse", call.name, call.arguments)
            for line in pre.outputs:
                self.ui.info(line) if hasattr(self.ui, "info") else None
            if pre.blocked:
                self.ui.tool_result(f"blocked by hook: {pre.reason}", error=True)
                return f"Error: blocked by PreToolUse hook: {pre.reason}"

        try:
            result = tool.run(call.arguments, self.ctx)
        except ToolError as exc:
            self.ui.tool_result(str(exc), error=True)
            self._audit(call.name, call.arguments, "error", str(exc))
            return f"Error: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            self.ui.tool_result(f"{type(exc).__name__}: {exc}", error=True)
            self._audit(call.name, call.arguments, "error", f"{type(exc).__name__}: {exc}")
            return f"Error: {type(exc).__name__}: {exc}"

        # PostToolUse hooks (#6) — observe the result (cannot block).
        if self.hooks is not None and self.hooks.has("PostToolUse"):
            post = self.hooks.run("PostToolUse", call.name, call.arguments, result=result)
            for line in post.outputs:
                self.ui.info(line) if hasattr(self.ui, "info") else None

        self.ui.tool_result(result)
        self._audit(call.name, call.arguments, "ok")
        return result

    def _audit(self, name: str, args: dict, status: str, detail: str = "") -> None:
        """Record a tool execution to the optional append-only audit log."""
        audit = getattr(self.ctx, "audit", None)
        if audit is None:
            return
        try:
            audit(name=name, args=args, status=status, detail=detail)
        except Exception:  # pragma: no cover - audit must never break a turn
            pass
