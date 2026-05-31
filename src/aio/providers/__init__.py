"""Provider registry and factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .anthropic import AnthropicProvider
from .base import AssistantTurn, Message, Provider, ProviderError, ToolCall
from .openai_compat import (
    OllamaProvider,
    OpenAICompatProvider,
    OpenRouterProvider,
)

if TYPE_CHECKING:
    from aio.config import Config

PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAICompatProvider,
    # OpenAI-compatible providers — the endpoint comes from each one's base_url.
    "google": OpenAICompatProvider,
    "groq": OpenAICompatProvider,
    "mistral": OpenAICompatProvider,
    "deepseek": OpenAICompatProvider,
    "xai": OpenAICompatProvider,
    "together": OpenAICompatProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
}

__all__ = [
    "AssistantTurn",
    "Message",
    "Provider",
    "ProviderError",
    "ToolCall",
    "PROVIDERS",
    "build_provider",
]


def build_provider(config: "Config") -> Provider:
    """Instantiate the active provider described by ``config``."""

    name = config.provider
    if name not in PROVIDERS:
        raise ProviderError(
            f"Unknown provider '{name}'. Available: {', '.join(sorted(PROVIDERS))}"
        )
    pc = config.providers[name]
    cls = PROVIDERS[name]
    return cls(
        model=pc.model,
        api_key=pc.api_key,
        base_url=pc.base_url,
        max_tokens=pc.max_tokens,
        extra=pc.extra,
        cache=pc.cache,
        thinking_tokens=pc.thinking_tokens,
        max_retries=pc.max_retries,
    )
