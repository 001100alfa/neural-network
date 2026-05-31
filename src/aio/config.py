"""Configuration loading.

Precedence (highest wins):
    1. CLI overrides
    2. environment variables
    3. project config (./.aio.toml)
    4. user config (~/.config/aio/config.toml)
    5. built-in defaults

The static data (provider catalog, system prompt, styles, context windows), the
config dataclasses and the API-key store live in their own modules
(:mod:`aio.defaults`, :mod:`aio.models`, :mod:`aio.keystore`); they are
re-exported here so existing imports keep working.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .defaults import (
    AUTODETECT_ORDER,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_SYSTEM_PROMPT,
    OUTPUT_STYLES,
    PROVIDER_DEFAULTS,
    context_window_for,
)
from .keystore import KeyStore, keys_file_path
from .models import Config, ProviderConfig

__all__ = [
    "PROVIDER_DEFAULTS", "AUTODETECT_ORDER", "DEFAULT_SYSTEM_PROMPT", "OUTPUT_STYLES",
    "DEFAULT_CONTEXT_WINDOW", "context_window_for",
    "Config", "ProviderConfig", "KeyStore", "keys_file_path",
    "load_config", "load_project_memory",
]


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
    for name in AUTODETECT_ORDER:
        env = PROVIDER_DEFAULTS[name]["env"]
        if env and os.environ.get(env):
            return name
    return "ollama"  # local fallback, no key needed


def _norm_perm_mode(value: Any) -> str:
    from .permissions import normalize

    return normalize(value if value else "default")


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
            cache=bool(fp.get("cache", True)),
            thinking_tokens=int(fp.get("thinking_tokens", 0)),
            max_retries=int(fp.get("max_retries", 3)),
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
        project_memory=load_project_memory(workdir),
        project_map=_load_project_map(workdir),
        hooks=file_cfg.get("hooks", []),
        permissions=file_cfg.get("permissions", {}) or {},
        output_style=file_cfg.get("output_style", "default"),
        telemetry=file_cfg.get("telemetry", {}) or {},
        auto_context=bool(overrides.get("auto_context",
                          file_cfg.get("auto_context", os.environ.get("AIO_NO_AUTO_CONTEXT") != "1"))),
        auto_context_results=int(file_cfg.get("auto_context_results", 5)),
        max_reflections=int(overrides.get("max_reflections", file_cfg.get("max_reflections", 0))),
        context_window=int(overrides.get("context_window",
                           file_cfg.get("context_window", os.environ.get("AIO_CONTEXT_WINDOW", 0)) or 0)),
        permission_mode=_norm_perm_mode(overrides.get("permission_mode",
                           file_cfg.get("permission_mode", os.environ.get("AIO_PERMISSION_MODE")))),
    )


def _load_project_map(workdir: Path) -> str:
    """Build the compact codebase overview (opt out with AIO_NO_PROJECT_MAP=1)."""
    if os.environ.get("AIO_NO_PROJECT_MAP"):
        return ""
    try:
        from .projectmap import build_project_map

        return build_project_map(workdir)
    except Exception:  # pragma: no cover - never block startup
        return ""


def load_project_memory(workdir: Path) -> str:
    """Concatenate instruction/memory files (CLAUDE.md / AGENTS.md / .aio.md) plus
    a global ~/.config/aio/CLAUDE.md, so the agent has standing project context."""
    parts: list[str] = []
    for name in ("CLAUDE.md", "AGENTS.md", ".aio.md"):
        f = workdir / name
        if f.is_file():
            try:
                parts.append(f"## {name}\n{f.read_text('utf-8')[:20000]}")
            except OSError:
                pass
    g = Path.home() / ".config" / "aio" / "CLAUDE.md"
    if g.is_file():
        try:
            parts.append(f"## global CLAUDE.md\n{g.read_text('utf-8')[:20000]}")
        except OSError:
            pass
    return "\n\n".join(parts)
