"""Provider configuration, keys, model listing and connection tests."""

from __future__ import annotations

import os
from typing import Any

from .config import PROVIDER_DEFAULTS
from .providers import ProviderError
from .service_base import ServiceBase


class ProvidersMixin(ServiceBase):
    def configure(self, provider: str | None, model: str | None) -> dict[str, Any]:
        with self._lock:
            if provider:
                if provider not in PROVIDER_DEFAULTS:
                    raise ProviderError(f"unknown provider '{provider}'")
                self.config.provider = provider
                self.keys.set_active(provider)
            if model:
                self.config.active.model = model
                self.keys.set(self.config.provider, model=model)
            self._build_agent()
            return self.info()

    # -- AI providers / API keys panel -----------------------------------
    @staticmethod
    def _mask(key: str | None) -> str:
        if not key:
            return ""
        return ("•" * max(0, len(key) - 4)) + key[-4:] if len(key) > 4 else "••••"

    def providers_info(self) -> dict[str, Any]:
        """List every provider with masked key + configuration (never the raw key)."""
        out = []
        month = self._month()
        for name, d in PROVIDER_DEFAULTS.items():
            pc = self.config.providers[name]
            env_set = bool(d["env"] and os.environ.get(d["env"]))
            stored = bool(self.keys.get(name).get("api_key"))
            needs_key = d["env"] is not None
            budget = self.keys.get_budget(name)
            spent = self.keys.monthly(name, month).get("spent_usd", 0.0)
            out.append(
                {
                    "name": name,
                    "label": d["label"],
                    "model": pc.model,
                    "base_url": pc.base_url,
                    "env": d["env"],
                    "needs_key": needs_key,
                    "key_masked": self._mask(pc.api_key),
                    "configured": (not needs_key) or bool(pc.api_key),
                    "source": "stored" if stored else ("env" if env_set else ""),
                    "active": name == self.config.provider,
                    "budget_usd": budget,
                    "month_spent_usd": round(spent, 6),
                    "over_budget": bool(budget > 0 and spent >= budget),
                    "near_budget": bool(budget > 0 and 0.8 * budget <= spent < budget),
                }
            )
        return {"active": self.config.provider, "providers": out, "usage": self.usage_info()}

    def test_provider(self, name: str) -> dict[str, Any]:
        """Validate the key by listing models; returns {ok, models, count, error}."""
        if name not in PROVIDER_DEFAULTS:
            raise ProviderError(f"unknown provider '{name}'")
        from .providers import build_provider as _bp

        cfg_provider = self.config.provider
        try:
            self.config.provider = name  # build_provider reads config.active
            provider = _bp(self.config)
            models = provider.list_models()
        except ProviderError as exc:
            return {"ok": False, "error": str(exc), "models": [], "count": 0}
        except Exception as exc:  # pragma: no cover - defensive
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "models": [], "count": 0}
        finally:
            self.config.provider = cfg_provider
        return {"ok": True, "count": len(models), "models": models[:100], "error": ""}

    def set_provider_key(
        self,
        name: str,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        make_active: bool = False,
        budget: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if name not in PROVIDER_DEFAULTS:
                raise ProviderError(f"unknown provider '{name}'")
            self.keys.set(name, api_key=api_key, model=model, base_url=base_url)
            if budget is not None:
                self.keys.set_budget(name, budget)
            # reflect immediately on the live config
            pc = self.config.providers[name]
            if api_key is not None:
                pc.api_key = api_key or (
                    os.environ.get(PROVIDER_DEFAULTS[name]["env"] or "") or None
                )
            if model:
                pc.model = model
            if base_url:
                pc.base_url = base_url
            if make_active:
                self.config.provider = name
                self.keys.set_active(name)
            self._build_agent()
            return self.providers_info()

    # -- Terminal (cmd / bash) -------------------------------------------
