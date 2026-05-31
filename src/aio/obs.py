"""Operational observability: structured request logging + process metrics.

The web server previously silenced all logging, so a production incident left
no trace. This adds JSON-lines request logs (to stderr) and an in-process
metrics registry exposed at ``/metrics`` in Prometheus text format, plus a
``/health`` liveness endpoint. Zero dependencies.
"""

from __future__ import annotations

import json
import sys
import threading
import time

API_VERSION = "1"


class Metrics:
    """Tiny thread-safe counter registry for the dashboard server."""

    def __init__(self) -> None:
        self.start = time.time()
        self._lock = threading.Lock()
        self.requests_total = 0
        self.errors_total = 0
        self.by_status: dict[int, int] = {}

    def record(self, status: int) -> None:
        with self._lock:
            self.requests_total += 1
            if status >= 400:
                self.errors_total += 1
            self.by_status[status] = self.by_status.get(status, 0) + 1

    def uptime_s(self) -> float:
        return time.time() - self.start

    def prometheus(self, extra: dict[str, float] | None = None) -> str:
        """Render the registry as Prometheus text exposition format."""
        with self._lock:
            by_status = dict(self.by_status)
            req, err = self.requests_total, self.errors_total
        lines = [
            "# HELP aio_uptime_seconds Process uptime in seconds.",
            "# TYPE aio_uptime_seconds gauge",
            f"aio_uptime_seconds {self.uptime_s():.3f}",
            "# HELP aio_http_requests_total Total HTTP requests served.",
            "# TYPE aio_http_requests_total counter",
            f"aio_http_requests_total {req}",
            "# HELP aio_http_errors_total HTTP responses with status >= 400.",
            "# TYPE aio_http_errors_total counter",
            f"aio_http_errors_total {err}",
        ]
        for status, n in sorted(by_status.items()):
            lines.append(f'aio_http_responses_total{{status="{status}"}} {n}')
        for key, value in (extra or {}).items():
            lines.append(f"# TYPE aio_{key} gauge")
            lines.append(f"aio_{key} {value}")
        return "\n".join(lines) + "\n"


def log_event(level: str = "info", **fields) -> None:
    """Emit one structured JSON log line to stderr."""
    record = {"ts": round(time.time(), 3), "level": level, **fields}
    try:
        sys.stderr.write(json.dumps(record, default=str) + "\n")
        sys.stderr.flush()
    except Exception:  # pragma: no cover - logging must never raise
        pass
