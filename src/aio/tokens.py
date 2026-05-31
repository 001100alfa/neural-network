"""Token counting (#1).

Prefers a real tokenizer (``tiktoken``) when installed; otherwise uses an
improved heuristic that counts word- and punctuation-like pieces rather than a
flat chars/4. The agent further *self-calibrates* this against the actual input
token counts the provider reports, so the compaction threshold tracks reality.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+|[^\w\s]")

try:  # optional, no hard dependency
    import tiktoken  # type: ignore

    _ENC = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - tiktoken not installed
    _ENC = None


def count_text(text: str) -> int:
    """Best-effort token count for a piece of text."""
    if not text:
        return 0
    if _ENC is not None:  # pragma: no cover - depends on optional dep
        try:
            return len(_ENC.encode(text))
        except Exception:
            pass
    # Heuristic: ~1 token per word-piece, but long words split into ~4-char
    # subwords; punctuation is its own token. Closer to BPE than chars/4.
    pieces = _WORD_RE.findall(text)
    total = 0
    for p in pieces:
        total += 1 + (len(p) - 1) // 4 if len(p) > 4 else 1
    return total or max(1, len(text) // 4)
