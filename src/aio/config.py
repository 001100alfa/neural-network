"""Configuration loading.

Precedence (highest wins):
    1. CLI overrides
    2. environment variables
    3. project config (./.aio.toml)
    4. user config (~/.config/aio/config.toml)
    5. built-in defaults
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Built-in per-provider defaults. ``env`` is the environment variable that
# supplies the API key when one is not given in a config file.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
    },
    "openrouter": {
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "model": "qwen2.5-coder",
        "base_url": "http://localhost:11434/v1",
        "env": None,  # local, no key required
    },
}

DEFAULT_SYSTEM_PROMPT = """\
You are AIO, an all-in-one open-source terminal coding agent operating inside a \
user's project directory. You help with software engineering tasks: reading and \
understanding code, implementing features, fixing bugs, running tests, and using git.

You have tools to read, write, and edit files; search by filename and content; run \
shell commands; and use git. Guidelines:
- Read files before editing them; make focused, minimal changes.
- Prefer the provided tools over guessing. Verify your work (e.g. run tests).
- Be concise. Explain what you are about to do, then do it.
- Never run destructive commands without a clear reason; the user must approve \
  mutating actions.
- When the task is complete, give a short summary of what changed.
"""


@dataclass
class ProviderConfig:
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    provider: str
    providers: dict[str, ProviderConfig]
    workdir: Path
    auto_approve: bool = False
    allow_outside_workdir: bool = False
    max_steps: int = 50
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.provider]


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _auto_detect_provider(file_cfg: dict[str, Any]) -> str:
    """Pick a sensible default provider based on available API keys."""

    if file_cfg.get("provider"):
        return str(file_cfg["provider"])
    for name in ("anthropic", "openai", "openrouter"):
        env = PROVIDER_DEFAULTS[name]["env"]
        if env and os.environ.get(env):
            return name
    return "ollama"  # local fallback, no key needed


def load_config(
    workdir: Path | None = None,
    overrides: dict[str, Any] | None = None,
    config_path: Path | None = None,
) -> Config:
    overrides = overrides or {}
    workdir = Path(workdir or Path.cwd()).resolve()

    user_cfg = _read_toml(Path.home() / ".config" / "aio" / "config.toml")
    if config_path is not None:
        project_cfg = _read_toml(config_path)
    else:
        project_cfg = _read_toml(workdir / ".aio.toml")
    file_cfg = _deep_merge(user_cfg, project_cfg)

    provider = overrides.get("provider") or _auto_detect_provider(file_cfg)
    if provider not in PROVIDER_DEFAULTS:
        raise ValueError(
            f"Unknown provider '{provider}'. "
            f"Choose from: {', '.join(PROVIDER_DEFAULTS)}"
        )

    file_providers = file_cfg.get("providers", {})
    providers: dict[str, ProviderConfig] = {}
    for name, defaults in PROVIDER_DEFAULTS.items():
        fp = file_providers.get(name, {})
        env_name = defaults["env"]
        api_key = fp.get("api_key") or (os.environ.get(env_name) if env_name else None)
        model = fp.get("model", defaults["model"])
        if name == provider and overrides.get("model"):
            model = overrides["model"]
        providers[name] = ProviderConfig(
            model=model,
            api_key=api_key,
            base_url=fp.get("base_url", defaults["base_url"]),
            max_tokens=int(fp.get("max_tokens", 4096)),
            extra=fp.get("extra", {}),
        )

    mcp_servers = file_cfg.get("mcp", {}).get("servers", [])

    return Config(
        provider=provider,
        providers=providers,
        workdir=workdir,
        auto_approve=bool(overrides.get("auto_approve", file_cfg.get("auto_approve", False))),
        allow_outside_workdir=bool(
            overrides.get("allow_outside_workdir", file_cfg.get("allow_outside_workdir", False))
        ),
        max_steps=int(overrides.get("max_steps", file_cfg.get("max_steps", 50))),
        system_prompt=file_cfg.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
        mcp_servers=mcp_servers,
    )
