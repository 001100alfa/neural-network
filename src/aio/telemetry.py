"""Dependency-free OpenTelemetry-style telemetry (#8).

Emits one span per agent turn using OTel's GenAI semantic-convention attribute
names (``gen_ai.*``), written as JSONL locally and optionally POSTed to an OTLP
HTTP endpoint as a minimal ``ResourceSpans`` payload. No third-party packages.

Configure in .aio.toml:

    [telemetry]
    enabled = true
    file = "aio-otel.jsonl"            # local JSONL (optional)
    otlp_endpoint = "http://localhost:4318/v1/traces"   # optional OTLP/HTTP
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


def _now_ns() -> int:
    return time.time_ns()


@dataclass
class Telemetry:
    enabled: bool = False
    file: str | None = None
    otlp_endpoint: str | None = None
    service_name: str = "aio"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: dict | None) -> "Telemetry":
        cfg = cfg or {}
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            file=cfg.get("file"),
            otlp_endpoint=cfg.get("otlp_endpoint"),
            service_name=cfg.get("service_name", "aio"),
            headers=cfg.get("headers", {}) or {},
        )

    def record_turn(
        self,
        *,
        provider: str,
        model: str,
        usage: dict[str, Any],
        duration_s: float,
        ok: bool = True,
        operation: str = "chat",
    ) -> dict[str, Any]:
        """Build + emit a span for one agent turn. Returns the span dict."""
        if not self.enabled:
            return {}
        end = _now_ns()
        start = end - int(duration_s * 1e9)
        attrs = {
            "gen_ai.system": provider,
            "gen_ai.request.model": model,
            "gen_ai.operation.name": operation,
            "gen_ai.usage.input_tokens": int(usage.get("input_tokens", 0) or 0),
            "gen_ai.usage.output_tokens": int(usage.get("output_tokens", 0) or 0),
            "aio.usage.cache_read_tokens": int(usage.get("cache_read", 0) or 0),
            "aio.usage.cache_write_tokens": int(usage.get("cache_write", 0) or 0),
            "aio.requests": int(usage.get("requests", 0) or 0),
        }
        span = {
            "name": f"{operation} {model}",
            "start_time_unix_nano": start,
            "end_time_unix_nano": end,
            "status": {"code": "OK" if ok else "ERROR"},
            "attributes": attrs,
            "service.name": self.service_name,
        }
        self._write_jsonl(span)
        self._post_otlp(span)
        return span

    # -- exporters --------------------------------------------------------
    def _write_jsonl(self, span: dict) -> None:
        if not self.file:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.file)) or ".", exist_ok=True)
            with open(self.file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(span) + "\n")
        except OSError:  # pragma: no cover - fs edge
            pass

    def _otlp_payload(self, span: dict) -> dict:
        """Minimal OTLP/HTTP ResourceSpans JSON for one span."""
        def kv(k, v):
            if isinstance(v, bool):
                val = {"boolValue": v}
            elif isinstance(v, int):
                val = {"intValue": str(v)}
            elif isinstance(v, float):
                val = {"doubleValue": v}
            else:
                val = {"stringValue": str(v)}
            return {"key": k, "value": val}

        return {
            "resourceSpans": [{
                "resource": {"attributes": [kv("service.name", self.service_name)]},
                "scopeSpans": [{
                    "scope": {"name": "aio"},
                    "spans": [{
                        "name": span["name"],
                        "startTimeUnixNano": str(span["start_time_unix_nano"]),
                        "endTimeUnixNano": str(span["end_time_unix_nano"]),
                        "attributes": [kv(k, v) for k, v in span["attributes"].items()],
                        "status": {"code": 1 if span["status"]["code"] == "OK" else 2},
                    }],
                }],
            }]
        }

    def _post_otlp(self, span: dict) -> None:
        if not self.otlp_endpoint:
            return
        body = json.dumps(self._otlp_payload(span)).encode("utf-8")
        headers = {"content-type": "application/json"}
        headers.update(self.headers)
        req = urllib.request.Request(self.otlp_endpoint, data=body, headers=headers, method="POST")
        try:  # pragma: no cover - network
            urllib.request.urlopen(req, timeout=5).close()
        except (urllib.error.URLError, OSError):
            pass  # telemetry must never break the agent
