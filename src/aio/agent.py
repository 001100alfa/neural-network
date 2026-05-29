"""The core agent loop: model <-> tools until the task is done."""

from __future__ import annotations

from .providers import Message, Provider, ToolCall
from .tools import ToolContext, ToolError, ToolRegistry
from .ui import UI


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
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.ctx = ctx
        self.ui = ui
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.messages: list[Message] = []

    def reset(self) -> None:
        self.messages = []

    def run(self, user_input: str) -> str:
        """Run one user turn to completion; returns the final assistant text."""

        self.messages.append(Message(role="user", content=user_input))
        final_text = ""

        for _ in range(self.max_steps):
            self.ui.thinking()
            turn = self.provider.chat(
                self.messages, tools=self.tools.specs(), system=self.system_prompt
            )
            self.messages.append(
                Message(role="assistant", content=turn.content, tool_calls=turn.tool_calls)
            )
            if turn.content:
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

        # Approval flow for mutating tools.
        if tool.needs_approval and not self.ctx.auto_approve and call.name not in self.ctx.approved:
            decision = self.ui.confirm(call.name, call.arguments)
            if decision == "no":
                return "Tool call rejected by user."
            if decision == "always":
                self.ctx.approved.add(call.name)
        else:
            self.ui.tool_call(call.name, call.arguments)

        try:
            result = tool.run(call.arguments, self.ctx)
        except ToolError as exc:
            self.ui.tool_result(str(exc), error=True)
            return f"Error: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            self.ui.tool_result(f"{type(exc).__name__}: {exc}", error=True)
            return f"Error: {type(exc).__name__}: {exc}"

        self.ui.tool_result(result)
        return result
