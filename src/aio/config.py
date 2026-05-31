"""Configuration loading.

Precedence (highest wins):
    1. CLI overrides
    2. environment variables
    3. project config (./.aio.toml)
    4. user config (~/.config/aio/config.toml)
    5. built-in defaults
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Built-in per-provider defaults. ``env`` is the environment variable that
# supplies the API key when one is not given in a config file or the key store.
# All providers except "anthropic" speak the OpenAI-compatible chat API.
PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic (Claude)",
        "model": "claude-sonnet-4-6",
        "base_url": "https://api.anthropic.com",
        "env": "ANTHROPIC_API_KEY",
    },
    "openai": {
        "label": "OpenAI (GPT)",
        "model": "gpt-4o",
        "base_url": "https://api.openai.com/v1",
        "env": "OPENAI_API_KEY",
    },
    "google": {
        "label": "Google Gemini",
        "model": "gemini-2.0-flash",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env": "GEMINI_API_KEY",
    },
    "groq": {
        "label": "Groq",
        "model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "env": "GROQ_API_KEY",
    },
    "mistral": {
        "label": "Mistral AI",
        "model": "mistral-large-latest",
        "base_url": "https://api.mistral.ai/v1",
        "env": "MISTRAL_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "env": "DEEPSEEK_API_KEY",
    },
    "xai": {
        "label": "xAI (Grok)",
        "model": "grok-2-latest",
        "base_url": "https://api.x.ai/v1",
        "env": "XAI_API_KEY",
    },
    "together": {
        "label": "Together AI",
        "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "base_url": "https://api.together.xyz/v1",
        "env": "TOGETHER_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "model": "anthropic/claude-sonnet-4-6",
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "label": "Ollama (local)",
        "model": "qwen2.5-coder",
        "base_url": "http://localhost:11434/v1",
        "env": None,  # local, no key required
    },
}

# Order used when auto-detecting which provider to use from available API keys.
AUTODETECT_ORDER = (
    "anthropic", "openai", "google", "groq", "mistral",
    "deepseek", "xai", "together", "openrouter",
)

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

# Named output styles (#7): appended to the system prompt to shape responses.
OUTPUT_STYLES: dict[str, str] = {
    "default": "",
    "concise": "\n\n# Output style: concise\nBe terse. Prefer the shortest correct "
               "answer; minimal prose, no preamble or recap.",
    "explanatory": "\n\n# Output style: explanatory\nExplain your reasoning and the "
                   "trade-offs as you work, so the user learns from the change.",
    "teacher": "\n\n# Output style: teacher\nTeach as you go: define key concepts, note "
               "why each step matters, and suggest what to learn next.",
}


@dataclass
class ProviderConfig:
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)
    cache: bool = True            # provider prompt caching
    thinking_tokens: int = 0      # extended-thinking budget, 0 = off
    max_retries: int = 3          # transient-error retries with backoff


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
    #: project/instruction memory loaded from CLAUDE.md / AGENTS.md / .aio.md
    project_memory: str = ""
    #: compact codebase overview (tree/langs/key files) for agent priming (#J)
    project_map: str = ""
    #: PreToolUse/PostToolUse hooks (#6)
    hooks: list[dict[str, Any]] = field(default_factory=list)
    #: granular tool permissions: tool name -> allow|deny|ask
    permissions: dict[str, str] = field(default_factory=dict)
    #: named output style: default|concise|explanatory|teacher
    output_style: str = "default"
    #: OpenTelemetry-style telemetry settings (#8)
    telemetry: dict[str, Any] = field(default_factory=dict)

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
    for name in AUTODETECT_ORDER:
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


def keys_file_path() -> Path:
    """Where API keys entered via the dashboard are persisted (JSON)."""
    env = os.environ.get("AIO_KEYS_FILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "aio" / "keys.json"


@dataclass
class KeyStore:
    """Local, persistent store of provider API keys / model / base_url.

    Stored as plain JSON (chmod 600) — comparable to other CLI tools. Intended
    for local/trusted use; the file is git-ignored.
    """

    path: Path
    data: dict[str, Any] = field(default_factory=lambda: {"providers": {}, "active": None})

    @classmethod
    def load(cls, path: Path | str | None = None) -> "KeyStore":
        p = Path(path) if path else keys_file_path()
        data: dict[str, Any] = {"providers": {}, "active": None}
        if p.is_file():
            try:
                loaded = json.loads(p.read_text("utf-8"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except (json.JSONDecodeError, OSError):
                pass
        data.setdefault("providers", {})
        data.setdefault("active", None)
        return cls(path=p, data=data)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        try:
            os.chmod(self.path, 0o600)
        except OSError:  # pragma: no cover - e.g. some Windows setups
            pass

    def get(self, name: str) -> dict[str, Any]:
        return self.data["providers"].get(name, {})

    def set(
        self,
        name: str,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        entry = self.data["providers"].setdefault(name, {})
        if api_key is not None:
            if api_key == "":
                entry.pop("api_key", None)
            else:
                entry["api_key"] = api_key
        if model:
            entry["model"] = model
        if base_url:
            entry["base_url"] = base_url
        self.save()

    def set_active(self, name: str) -> None:
        self.data["active"] = name
        self.save()

    # -- per-provider monthly budget + spend ------------------------------
    def set_budget(self, name: str, budget_usd: float) -> None:
        entry = self.data["providers"].setdefault(name, {})
        entry["budget_usd"] = round(float(budget_usd or 0.0), 4)
        self.save()

    def get_budget(self, name: str) -> float:
        try:
            return float(self.data["providers"].get(name, {}).get("budget_usd", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def monthly(self, name: str, month: str) -> dict[str, Any]:
        m = self.data.get("usage", {}).get(name)
        if not m or m.get("month") != month:
            return {"month": month, "spent_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}
        return m

    def record_spend(
        self, name: str, month: str, cost: float, input_tokens: int, output_tokens: int, requests: int
    ) -> None:
        usage = self.data.setdefault("usage", {})
        m = usage.get(name)
        if not m or m.get("month") != month:  # new month -> reset the counter
            m = {"month": month, "spent_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}
        m["spent_usd"] = round(m["spent_usd"] + cost, 6)
        m["input_tokens"] += input_tokens
        m["output_tokens"] += output_tokens
        m["requests"] += requests
        usage[name] = m
        self.save()

    def apply_to(self, config: "Config") -> None:
        """Overlay stored keys/models/base_urls (and active provider) onto config."""
        for name, pc in config.providers.items():
            stored = self.data["providers"].get(name, {})
            if stored.get("api_key"):
                pc.api_key = stored["api_key"]
            if stored.get("model"):
                pc.model = stored["model"]
            if stored.get("base_url"):
                pc.base_url = stored["base_url"]
        active = self.data.get("active")
        if active in config.providers:
            config.provider = active
