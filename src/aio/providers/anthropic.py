"""Anthropic (Claude) provider using the native Messages API."""

from __future__ import annotations

from typing import Any

from .base import AssistantTurn, Message, Provider, ProviderError, ToolCall

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name = "anthropic"

    def _build_payload(
        self, messages: list[Message], tools: list[dict], system: str | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        if not self.api_key:
            raise ProviderError(
                "Anthropic API key missing. Set ANTHROPIC_API_KEY or "
                "providers.anthropic.api_key in your config."
            )

        url = f"{self.base_url or 'https://api.anthropic.com'}/v1/messages"
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._convert_messages(messages),
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        body.update(self.extra)
        return url, headers, body

    @staticmethod
    def _convert_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                # handled via top-level "system" field; skip here
                continue
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
                continue
            if m.role == "assistant":
                blocks: list[dict[str, Any]] = []
                if m.content:
                    blocks.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                out.append({"role": "assistant", "content": blocks or ""})
                continue
            # user
            out.append({"role": "user", "content": m.content})
        return out

    def _parse_response(self, data: dict[str, Any]) -> AssistantTurn:
        if data.get("type") == "error":  # pragma: no cover - network path
            err = data.get("error", {})
            raise ProviderError(f"Anthropic error: {err.get('message', data)}")

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ToolCall.new_id()),
                        name=block.get("name", ""),
                        arguments=block.get("input", {}) or {},
                    )
                )
        return AssistantTurn(
            content="".join(text_parts).strip(),
            tool_calls=tool_calls,
            usage=data.get("usage"),
            raw=data,
        )
