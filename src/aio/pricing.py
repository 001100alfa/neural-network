"""Rough cost estimation for the dashboard's usage indicator.

Prices are approximate public list prices in USD per 1,000,000 tokens as
``(input, output)`` and are matched by substring against the model id. They are
estimates only and change over time; treat the figures as a guide, not a bill.
"""

from __future__ import annotations

# USD per 1,000,000 tokens: model-id substring -> (input_rate, output_rate)
PRICES: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-haiku": (0.80, 4.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (15.0, 75.0),
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.0, 8.0),
    "o3-mini": (1.10, 4.40),
    # Google
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.0),
    "gemini": (0.10, 0.40),
    # Groq / Together (open models, approx)
    "llama-3.3-70b": (0.59, 0.79),
    "llama-3.1-8b": (0.05, 0.08),
    # DeepSeek
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-chat": (0.27, 1.10),
    # Mistral
    "mistral-large": (2.0, 6.0),
    "mistral-small": (0.20, 0.60),
    # xAI
    "grok-2": (2.0, 10.0),
    # Local
    "qwen": (0.0, 0.0),
    "ollama": (0.0, 0.0),
}


def estimate_cost(model: str | None, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """Return ``(usd_cost, known)``. ``known`` is False when no price matched."""
    m = (model or "").lower()
    best: tuple[str, float, float] | None = None
    for key, (ir, orr) in PRICES.items():
        if key in m and (best is None or len(key) > len(best[0])):
            best = (key, ir, orr)
    if best is None:
        return 0.0, False
    _, ir, orr = best
    return (input_tokens / 1_000_000 * ir) + (output_tokens / 1_000_000 * orr), True
