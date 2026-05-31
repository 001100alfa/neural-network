"""Provider-neutral message/tool types and the ``Provider`` base class.

Every backend (Anthropic, OpenAI, OpenRouter, Ollama, ...) speaks a different
wire format. We normalise everything to the small set of dataclasses below so
the agent loop never has to care which model it is talking to.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single tool/function invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]

    @staticmethod
    def new_id() -> str:
        return "call_" + uuid.uuid4().hex[:24]


@dataclass
class Message:
    """A unified chat message.

    ``role`` is one of: ``system``, ``user``, ``assistant``, ``tool``.
    Assistant messages may carry ``tool_calls``; ``tool`` messages carry the
    result of one call and reference it via ``tool_call_id``.
    """

    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    #: optional image attachments on user messages: {"media_type","data"(base64)}
    images: list[dict] = field(default_factory=list)


@dataclass
class AssistantTurn:
    """A single assistant response."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


class ProviderError(RuntimeError):
    """Raised when a provider request fails."""


class Provider(ABC):
    """Base class for all model providers."""

    #: short identifier, e.g. "anthropic"
    name: str = "base"

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 180.0,
        max_tokens: int = 4096,
        extra: dict[str, Any] | None = None,
        cache: bool = True,
        thinking_tokens: int = 0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.extra = extra or {}
        #: enable provider prompt caching where supported (#8)
        self.cache = cache
        #: extended-thinking budget in tokens (0 = off) (#9)
        self.thinking_tokens = int(thinking_tokens or 0)

    # -- subclasses implement these three ---------------------------------

    @abstractmethod
    def _build_payload(
        self, messages: list[Message], tools: list[dict], system: str | None
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        """Return ``(url, headers, json_body)`` for the chat request."""

    @abstractmethod
    def _parse_response(self, data: dict[str, Any]) -> AssistantTurn:
        """Turn a raw JSON response into an :class:`AssistantTurn`."""

    # -- shared HTTP plumbing ---------------------------------------------

    def chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system: str | None = None,
    ) -> AssistantTurn:
        url, headers, body = self._build_payload(messages, tools or [], system)
        data = self._post(url, headers, body)
        return self._parse_response(data)

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        raw = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
        return self._send(req, url)

    def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=headers, method="GET")
        return self._send(req, url)

    def _send(self, req: "urllib.request.Request", url: str) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", "replace")
            raise ProviderError(
                f"{self.name} request failed: HTTP {exc.code}\n{detail}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError(
                f"{self.name} request failed: {exc.reason}. "
                f"Is the endpoint reachable ({url})?"
            ) from exc

    # -- model discovery / connection test --------------------------------

    def list_models(self) -> list[str]:
        """Return the model IDs available for this provider's API key.

        Doubles as a lightweight connection/key test: a successful call proves
        the endpoint is reachable and the key is accepted. Subclasses override.
        """
        raise NotImplementedError(f"{self.name} does not support model listing")

    # -- token streaming ---------------------------------------------------

    def stream_chat(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        system: str | None = None,
        on_delta=None,
    ) -> AssistantTurn:
        """Like :meth:`chat`, but invoke ``on_delta(text)`` for each text chunk.

        The base implementation is non-streaming: it makes one request and emits
        the whole reply as a single delta. Providers that support server-sent
        streaming override this to deliver tokens as they arrive.
        """
        turn = self.chat(messages, tools, system)
        if on_delta and turn.content:
            on_delta(turn.content)
        return turn

    def _stream_lines(self, url: str, headers: dict[str, str], body: dict[str, Any]):
        """Yield raw SSE lines from a streaming POST (best-effort)."""
        raw = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    yield line.decode("utf-8", "replace").rstrip("\r\n")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = exc.read().decode("utf-8", "replace")
            raise ProviderError(f"{self.name} stream failed: HTTP {exc.code}\n{detail}") from exc
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise ProviderError(f"{self.name} stream failed: {exc.reason} ({url})") from exc
