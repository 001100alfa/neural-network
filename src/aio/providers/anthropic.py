"""Anthropic (Claude) provider using the native Messages API."""

from __future__ import annotations

import json
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
        # Anthropic requires roles to alternate, so all tool results that follow
        # an assistant turn (one per tool call) must be merged into a SINGLE
        # user message containing multiple tool_result blocks.
        pending_results: list[dict[str, Any]] = []

        def flush_results() -> None:
            if pending_results:
                out.append({"role": "user", "content": list(pending_results)})
                pending_results.clear()

        for m in messages:
            if m.role == "system":
                # handled via top-level "system" field; skip here
                continue
            if m.role == "tool":
                pending_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
                continue
            # any non-tool message closes the current batch of tool results
            flush_results()
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
            else:  # user
                if m.images:
                    blocks = [{"type": "text", "text": m.content}] if m.content else []
                    for img in m.images:
                        blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": img.get("media_type", "image/png"),
                                "data": img.get("data", ""),
                            },
                        })
                    out.append({"role": "user", "content": blocks})
                else:
                    out.append({"role": "user", "content": m.content})

        flush_results()
        return out

    def _models_request(self) -> tuple[str, dict[str, str]]:
        if not self.api_key:
            raise ProviderError("Anthropic API key missing.")
        url = f"{self.base_url or 'https://api.anthropic.com'}/v1/models?limit=1000"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        return url, headers

    @staticmethod
    def _parse_models(data: dict[str, Any]) -> list[str]:
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]

    def list_models(self) -> list[str]:
        url, headers = self._models_request()
        return self._parse_models(self._get(url, headers))

    def stream_chat(self, messages, tools=None, system=None, on_delta=None):
        url, headers, body = self._build_payload(messages, tools or [], system)
        body["stream"] = True
        parts: list[str] = []
        blocks: dict[int, dict] = {}
        usage: dict[str, Any] = {}
        for line in self._stream_lines(url, headers, body):  # pragma: no cover - network
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data:
                continue
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            etype = ev.get("type")
            if etype == "message_start":
                u = ev.get("message", {}).get("usage")
                if u:
                    usage.update(u)
            elif etype == "content_block_start":
                cb = ev.get("content_block", {})
                if cb.get("type") == "tool_use":
                    blocks[ev.get("index", 0)] = {"id": cb.get("id"), "name": cb.get("name", ""), "json": ""}
            elif etype == "content_block_delta":
                d = ev.get("delta", {})
                if d.get("type") == "text_delta":
                    parts.append(d.get("text", ""))
                    if on_delta:
                        on_delta(d.get("text", ""))
                elif d.get("type") == "input_json_delta":
                    blk = blocks.get(ev.get("index", 0))
                    if blk is not None:
                        blk["json"] += d.get("partial_json", "")
            elif etype == "message_delta":
                u = ev.get("usage")
                if u:
                    usage.update(u)
        tool_calls = []
        for idx in sorted(blocks):
            b = blocks[idx]
            try:
                args = json.loads(b["json"]) if b["json"] else {}
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(ToolCall(id=b["id"] or ToolCall.new_id(), name=b["name"], arguments=args))
        return AssistantTurn(content="".join(parts).strip(), tool_calls=tool_calls, usage=usage or None)

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
