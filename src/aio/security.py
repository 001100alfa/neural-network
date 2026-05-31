"""Security guard for the web dashboard.

The dashboard runs arbitrary shell/file tools, so an unauthenticated local
server means any other process or browser tab on the machine (or a malicious
web page via DNS-rebinding) could drive it. :class:`WebGuard` closes that:

* **Token auth** — a high-entropy bearer token, compared in constant time. The
  startup URL carries it as ``?token=``; on first load the server hands it back
  as a ``SameSite=Strict`` cookie so subsequent fetch/EventSource calls carry it
  automatically. Requests without it get 401.
* **Host allow-list** — rejects requests whose ``Host`` header isn't an expected
  loopback name, the classic defense against DNS-rebinding into a localhost bind.
* **Body-size cap** — oversized POST bodies are refused (413) before they're read.
* **Per-IP rate limiting** — a fixed window throttles bursts (429).

Pure stdlib; no effect when auth is explicitly disabled (``token=None``).
"""

from __future__ import annotations

import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs, urlparse

COOKIE_NAME = "aio_token"
DEFAULT_MAX_BODY = 25 * 1024 * 1024     # 25 MB (multimodal uploads fit)
DEFAULT_RATE = (240, 60)                # 240 requests / 60 s per client IP
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def new_token() -> str:
    """Return a fresh high-entropy URL-safe token."""
    return secrets.token_urlsafe(32)


class ApprovalBroker:
    """Coordinates human tool-approval decisions across request threads.

    The agent loop runs in one thread (the streaming request) and blocks in
    :meth:`wait` for a decision delivered by another thread (the ``/api/approve``
    request). The default on timeout is ``"no"`` — fail closed, never run a
    side-effecting tool just because the user walked away.
    """

    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._counter = 0

    def open(self) -> str:
        with self._lock:
            self._counter += 1
            rid = f"appr-{self._counter}-{secrets.token_hex(4)}"
            self._pending[rid] = {"event": threading.Event(), "decision": "no"}
            return rid

    def wait(self, rid: str) -> str:
        slot = self._pending.get(rid)
        if slot is None:
            return "no"
        signalled = slot["event"].wait(self.timeout)
        with self._lock:
            slot = self._pending.pop(rid, None)
        if not signalled or slot is None:
            return "no"          # timed out / cancelled -> deny (fail closed)
        return slot["decision"]

    def resolve(self, rid: str, decision: str) -> bool:
        decision = decision if decision in ("yes", "no", "always") else "no"
        with self._lock:
            slot = self._pending.get(rid)
            if slot is None:
                return False
            slot["decision"] = decision
            slot["event"].set()
            return True

    def pending_ids(self) -> list[str]:
        with self._lock:
            return list(self._pending)


def loopback_allowlist() -> set[str]:
    return set(LOOPBACK_HOSTS)


class WebGuard:
    """Auth / host / size / rate checks for the dashboard, all optional."""

    def __init__(self, token: str | None,
                 allowed_hosts: set[str] | None,
                 max_body: int = DEFAULT_MAX_BODY,
                 rate: tuple[int, int] = DEFAULT_RATE) -> None:
        self.token = token                  # None => auth disabled
        self.allowed_hosts = allowed_hosts  # None => host check disabled
        self.max_body = int(max_body)
        self.rate_max, self.rate_window = rate
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    # -- host ---------------------------------------------------------------
    def host_ok(self, host_header: str | None) -> bool:
        if self.allowed_hosts is None:
            return True
        host = (host_header or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in self.allowed_hosts

    # -- token --------------------------------------------------------------
    @staticmethod
    def extract_token(headers, path: str) -> str | None:
        """Pull a token from Authorization, ``?token=`` or the cookie."""
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:].strip()
        query = parse_qs(urlparse(path).query)
        if query.get("token"):
            return query["token"][0]
        raw = headers.get("Cookie")
        if raw:
            try:
                jar = SimpleCookie()
                jar.load(raw)
            except CookieError:  # pragma: no cover - malformed header
                return None
            if COOKIE_NAME in jar:
                return jar[COOKIE_NAME].value
        return None

    @staticmethod
    def token_in_query(path: str) -> bool:
        return bool(parse_qs(urlparse(path).query).get("token"))

    def authorized(self, provided: str | None) -> bool:
        if self.token is None:
            return True
        return provided is not None and secrets.compare_digest(provided, self.token)

    def cookie_header(self) -> str:
        """Value for ``Set-Cookie`` that pins the token for this origin."""
        return (f"{COOKIE_NAME}={self.token}; Path=/; SameSite=Strict; "
                f"HttpOnly; Max-Age=86400")

    # -- body size ----------------------------------------------------------
    def body_too_large(self, content_length: str | int | None) -> bool:
        try:
            return int(content_length or 0) > self.max_body
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return False

    # -- rate limiting ------------------------------------------------------
    def rate_ok(self, client_ip: str) -> bool:
        now = time.time()
        cutoff = now - self.rate_window
        with self._lock:
            q = self._hits.setdefault(client_ip, [])
            q[:] = [t for t in q if t >= cutoff]
            if len(q) >= self.rate_max:
                return False
            q.append(now)
            return True
