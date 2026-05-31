"""Secret redaction — keep API keys and tokens out of logs, exports and audits.

Secrets routinely end up in tool output, conversation text and request logs.
:func:`redact` masks the common shapes (provider keys, bearer tokens, AWS keys,
``KEY=value`` assignments) while leaving a short prefix so a value stays
recognisable. Pure functions, no dependencies.
"""

from __future__ import annotations

import re
from typing import Any

_MASK = "***"

def _mask_token(tok: str) -> str:
    keep = 4 if len(tok) > 8 else 1
    return tok[:keep] + "…" + _MASK


# Keyword/value forms: mask the *value* (group 4), keep the label + separator.
_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|bearer)\b"
    r"(\s*[:=]\s*|\s+)(['\"]?)([^\s'\"]{6,})"
)
# Bare provider-key shapes: mask the whole match.
_BARE = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),          # OpenAI / Anthropic
    re.compile(r"\bgsk_[A-Za-z0-9]{6,}"),            # Groq
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{6,}"),    # Slack
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),     # GitHub
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),             # AWS access key id
]


def redact(text: str) -> str:
    """Return ``text`` with secret-looking substrings masked."""
    if not text:
        return text
    out = _ASSIGN.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{_mask_token(m.group(4))}", text)
    for pat in _BARE:
        out = pat.sub(lambda m: _mask_token(m.group(0)), out)
    return out


def redact_obj(obj: Any) -> Any:
    """Recursively redact string values in dicts/lists (for structured logs)."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    return obj
