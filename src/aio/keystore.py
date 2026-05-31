"""Local API-key store, split out of ``config.py``.

Persists provider API keys / model / base_url and per-provider monthly spend as
plain JSON (chmod 600) — intended for local/trusted use; the file is git-ignored.
``config`` re-exports ``KeyStore`` and ``keys_file_path`` for compatibility.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from .models import Config


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
