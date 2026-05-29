"""OpenAI-compatible provider.

The same chat-completions wire format is spoken by OpenAI, OpenRouter and
Ollama, so all three are handled by this single class with different default
endpoints/keys supplied via configuration.
"""

from __future__ import annotations

import json
from typing import Any

from .base import AssistantTurn, Message, Provider, ProviderError, ToolCall


class OpenAICompatProvider(Provider):
    name = "openai"

    # Subclasses/config may override these defaults.
    default_base_url = "https://api.openai.com/v1"

    def _build_payload(
        self, messages: list[Message], tools: list[dict], system: str | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        base = self.base_url or self.default_base_url
        url = f"{base}/chat/completions"

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        # OpenRouter likes these but they are harmless elsewhere.
        headers.setdefault("http-referer", "https://github.com/001100alfa/neural-network")
        headers.setdefault("x-title", "AIO Coding Agent")

        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._convert_messages(messages, system),
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        body.update(self.extra)
        return url, headers, body

    @staticmethod
    def _convert_messages(messages: list[Message], system: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if system:
            out.append({"role": "system", "content": system})
        for m in messages:
            if m.role == "system":
                out.append({"role": "system", "content": m.content})
            elif m.role == "tool":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.tool_call_id,
                        "content": m.content,
                    }
                )
            elif m.role == "assistant":
                msg: dict[str, Any] = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ]
                out.append(msg)
            else:  # user
                out.append({"role": "user", "content": m.content})
        return out

    def _models_request(self) -> tuple[str, dict[str, str]]:
        base = self.base_url or self.default_base_url
        headers = {}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return f"{base}/models", headers

    @staticmethod
    def _parse_models(data: dict[str, Any]) -> list[str]:
        items = data.get("data", data) if isinstance(data, dict) else data
        ids = []
        for m in items or []:
            mid = m.get("id") if isinstance(m, dict) else None
            if mid:
                ids.append(mid)
        return sorted(ids)

    def list_models(self) -> list[str]:
        url, headers = self._models_request()
        return self._parse_models(self._get(url, headers))

    def stream_chat(self, messages, tools=None, system=None, on_delta=None):
        url, headers, body = self._build_payload(messages, tools or [], system)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        parts: list[str] = []
        frags: dict[int, dict] = {}
        usage = None
        for line in self._stream_lines(url, headers, body):  # pragma: no cover - network
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]" or not data:
                if data == "[DONE]":
                    break
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or [{}]
            delta = choices[0].get("delta", {}) if choices else {}
            if delta.get("content"):
                parts.append(delta["content"])
                if on_delta:
                    on_delta(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = frags.setdefault(tc.get("index", 0), {"id": None, "name": "", "args": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function", {})
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
        tool_calls = []
        for idx in sorted(frags):
            s = frags[idx]
            try:
                args = json.loads(s["args"]) if s["args"] else {}
            except json.JSONDecodeError:
                args = {"_raw": s["args"]}
            tool_calls.append(ToolCall(id=s["id"] or ToolCall.new_id(), name=s["name"], arguments=args))
        return AssistantTurn(content="".join(parts).strip(), tool_calls=tool_calls, usage=usage)

    def _parse_response(self, data: dict[str, Any]) -> AssistantTurn:
        if "error" in data and "choices" not in data:  # pragma: no cover - network path
            err = data["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise ProviderError(f"{self.name} error: {msg}")

        choices = data.get("choices") or []
        if not choices:  # pragma: no cover - network path
            raise ProviderError(f"{self.name}: empty response: {data}")

        message = choices[0].get("message", {})
        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ToolCall.new_id()),
                    name=fn.get("name", ""),
                    arguments=args or {},
                )
            )
        return AssistantTurn(
            content=content.strip(),
            tool_calls=tool_calls,
            usage=data.get("usage"),
            raw=data,
        )


class OpenRouterProvider(OpenAICompatProvider):
    name = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"


class OllamaProvider(OpenAICompatProvider):
    name = "ollama"
    default_base_url = "http://localhost:11434/v1"
