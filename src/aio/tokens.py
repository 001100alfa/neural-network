"""Token counting with a layered, honest backend story.

There is no zero-dependency local tokenizer for the Claude models, so counts
come from the best backend available, in order:

1. **tiktoken** (``cl100k_base``) if the optional package is installed — a real
   BPE tokenizer; exact for OpenAI models and a close proxy for others.
2. an improved **heuristic** otherwise: word/punctuation pieces with long words
   split into ~4-char subwords (much closer to BPE than a flat chars/4).

The agent additionally *self-calibrates* whichever backend is active against
the real input-token counts the provider reports, so the compaction threshold
tracks reality regardless of backend. :func:`active_backend` reports which one
is in use (surfaced in the dashboard so the estimate's provenance is honest).
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+|[^\w\s]")

try:  # optional, no hard dependency
    import tiktoken  # type: ignore

    _ENC = tiktoken.get_encoding("cl100k_base")
    _BACKEND = "tiktoken:cl100k_base"
except Exception:  # pragma: no cover - tiktoken not installed in CI
    _ENC = None
    _BACKEND = "heuristic"


def active_backend() -> str:
    """Return the name of the token-counting backend currently in use."""
    return _BACKEND


def _heuristic_count(text: str) -> int:
    """BPE-ish estimate: ~1 token per word-piece; long words split by ~4 chars.

    Punctuation is its own token. Far closer to BPE than a flat chars/4.
    """
    pieces = _WORD_RE.findall(text)
    total = 0
    for p in pieces:
        total += 1 + (len(p) - 1) // 4 if len(p) > 4 else 1
    return total or max(1, len(text) // 4)


def count_text(text: str) -> int:
    """Best-effort token count for a piece of text."""
    if not text:
        return 0
    if _ENC is not None:
        try:
            return len(_ENC.encode(text))
        except Exception:  # pragma: no cover - defensive; encoder edge cases
            pass
    return _heuristic_count(text)
