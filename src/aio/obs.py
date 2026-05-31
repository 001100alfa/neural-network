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


class AuditLog:
    """Append-only, secret-redacted, hash-chained record of tool executions.

    Each JSON line carries ``prev`` = the SHA-256 of the previous line, so any
    edit or truncation of the history is detectable. Rotates by size to bound
    disk use (the chain restarts in the new file).
    """

    def __init__(self, path, max_bytes: int = 5_000_000) -> None:
        import hashlib
        from pathlib import Path

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._hashlib = hashlib
        self._prev = self._tail_hash()

    def _tail_hash(self) -> str:
        try:
            last = b""
            with open(self.path, "rb") as fh:
                for raw in fh:
                    if raw.strip():
                        last = raw
            return self._hashlib.sha256(last).hexdigest() if last else "genesis"
        except OSError:
            return "genesis"

    def record(self, name: str, args: dict, status: str, detail: str = "") -> None:
        from .redact import redact_obj

        entry = {
            "ts": round(time.time(), 3), "tool": name, "status": status,
            "args": redact_obj(args), "detail": redact_obj(detail), "prev": self._prev,
        }
        line = (json.dumps(entry, default=str) + "\n").encode("utf-8")
        try:
            with self._lock:
                if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
                    self._prev = "genesis"
                    entry["prev"] = "genesis"
                    line = (json.dumps(entry, default=str) + "\n").encode("utf-8")
                with open(self.path, "ab") as fh:
                    fh.write(line)
                self._prev = self._hashlib.sha256(line).hexdigest()
        except OSError:  # pragma: no cover - disk full / perms
            pass


def log_event(level: str = "info", **fields) -> None:
    """Emit one structured JSON log line to stderr (secrets redacted)."""
    from .redact import redact_obj

    record = {"ts": round(time.time(), 3), "level": level, **redact_obj(fields)}
    try:
        sys.stderr.write(json.dumps(record, default=str) + "\n")
        sys.stderr.flush()
    except Exception:  # pragma: no cover - logging must never raise
        pass
