"""The core agent loop: model <-> tools until the task is done."""

from __future__ import annotations

import json

from .providers import Message, Provider, ToolCall
from .tools import ToolContext, ToolError, ToolRegistry
from .ui import UI

# Tools allowed while in read-only "plan mode".
READONLY_TOOLS = {"read_file", "list_dir", "glob", "grep", "git_status", "git_diff"}

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
        self.plan_mode = plan_mode
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
        # roll the sub-agent's token usage into the parent's tally
        for k in ("requests", "input_tokens", "output_tokens"):
            self.run_usage[k] += child.run_usage.get(k, 0)
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
        return "".join(parts)

    def _effective_specs(self) -> list[dict]:
        specs = self.tools.specs()
        if self.plan_mode:
            specs = [s for s in specs if s["name"] in READONLY_TOOLS]
        return specs

    # -- context compaction ----------------------------------------------
    def _estimate_tokens(self, msgs: list[Message] | None = None) -> int:
        msgs = self.messages if msgs is None else msgs
        chars = len(self.summary)
        for m in msgs:
            chars += len(m.content or "")
            for tc in m.tool_calls:
                chars += len(tc.name) + len(json.dumps(tc.arguments))
        return chars // 4  # ~4 chars per token

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
        """Run one user turn to completion; returns the final assistant text."""

        self.messages.append(Message(role="user", content=user_input, images=images or []))
        self.run_usage = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        self._maybe_compact()
        final_text = ""

        use_stream = (
            self.stream and hasattr(self.ui, "token") and hasattr(self.provider, "stream_chat")
        )

        for _ in range(self.max_steps):
            self.ui.thinking()
            if use_stream:
                turn = self.provider.stream_chat(
                    self.messages, tools=self._effective_specs(),
                    system=self._effective_system(), on_delta=self.ui.token,
                )
            else:
                turn = self.provider.chat(
                    self.messages, tools=self._effective_specs(), system=self._effective_system()
                )
            inp, out = self._normalise_usage(turn.usage)
            self.run_usage["requests"] += 1
            self.run_usage["input_tokens"] += inp
            self.run_usage["output_tokens"] += out
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
                result = self._execute(call)
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

        # Approval flow for mutating tools.
        if tool.needs_approval and not self.ctx.auto_approve and call.name not in self.ctx.approved:
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
            return f"Error: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            self.ui.tool_result(f"{type(exc).__name__}: {exc}", error=True)
            return f"Error: {type(exc).__name__}: {exc}"

        # PostToolUse hooks (#6) — observe the result (cannot block).
        if self.hooks is not None and self.hooks.has("PostToolUse"):
            post = self.hooks.run("PostToolUse", call.name, call.arguments, result=result)
            for line in post.outputs:
                self.ui.info(line) if hasattr(self.ui, "info") else None

        self.ui.tool_result(result)
        return result
