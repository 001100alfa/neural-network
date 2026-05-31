"""Message <-> plain-dict conversion, shared by the service mixins.

A dependency-free leaf module (only :mod:`aio.providers`) so the session /
conversation code can import it without a circular dependency on ``service``.
"""

from __future__ import annotations

from typing import Any

from .providers import Message, ToolCall


def msg_to_dict(m: Message) -> dict[str, Any]:
    return {
        "role": m.role,
        "content": m.content,
        "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in m.tool_calls],
        "tool_call_id": m.tool_call_id,
        "name": m.name,
    }


def msg_from_dict(d: dict[str, Any]) -> Message:
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
