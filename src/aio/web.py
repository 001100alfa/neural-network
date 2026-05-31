"""A zero-dependency web server for the AIO dashboard.

Serves the single-page dashboard (see :mod:`aio.web_ui`) and a small JSON API
on top of :class:`aio.service.AgentService`, built entirely on the Python
standard library (``http.server``).

Because tool approvals are auto-granted in web mode (there is no interactive
terminal), the server is protected by :class:`aio.security.WebGuard`: a
per-session access token (handed off via the startup URL and pinned as a
SameSite cookie), a loopback Host allow-list against DNS-rebinding, a request
body-size cap and per-IP rate limiting. Auth can be disabled explicitly
(``require_auth=False`` / ``--web-no-auth``) for trusted isolated hosts.

:class:`EventUI` and :class:`AgentService` are re-exported here for backwards
compatibility (``from aio.web import AgentService, EventUI``).
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import __version__
from .config import Config
from .obs import API_VERSION, Metrics, log_event
from .providers import ProviderError
from .security import WebGuard, loopback_allowlist, new_token
from .service import AgentService, EventUI, _extract_doc_text
from .web_ui import static_asset

# Service-layer symbols are re-exported for backwards compatibility
# (``from aio.web import AgentService, EventUI``).
__all__ = ["AgentService", "EventUI", "_extract_doc_text", "serve"]


def _make_handler(service: AgentService, guard: "WebGuard | None" = None,
                  metrics: "Metrics | None" = None):
    # No guard supplied -> a fully permissive one (auth/host/rate checks off).
    active_guard = guard or WebGuard(token=None, allowed_hosts=None)
    active_metrics = metrics or Metrics()

    class Handler(BaseHTTPRequestHandler):
        _cookie: str | None = None

        def log_message(self, *_a):  # default stderr logging -> structured logs below
            pass

        def _security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-AIO-API-Version", API_VERSION)
            if self._cookie:
                self.send_header("Set-Cookie", self._cookie)

        def _observe(self, code: int) -> None:
            active_metrics.record(code)
            log_event("info" if code < 400 else "warn",
                      event="http", method=self.command, path=self.path.split("?")[0],
                      status=code, client=self.client_address[0] if self.client_address else "?")

        def _preflight(self) -> bool:
            """Host / rate / token checks; writes the error response on failure."""
            if not active_guard.host_ok(self.headers.get("Host")):
                self._json(403, {"error": "forbidden: unexpected Host header"})
                return False
            client_ip = self.client_address[0] if self.client_address else "?"
            if not active_guard.rate_ok(client_ip):
                self._json(429, {"error": "rate limit exceeded; slow down"})
                return False
            token = active_guard.extract_token(self.headers, self.path)
            if not active_guard.authorized(token):
                self._json(401, {"error": "unauthorized: open the dashboard via the "
                                          "URL printed at startup (it carries your token)"})
                return False
            # token arrived in the query string -> pin it as a cookie for next time
            if active_guard.token is not None and active_guard.token_in_query(self.path):
                self._cookie = active_guard.cookie_header()
            return True

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self._security_headers()
            self.end_headers()
            self.wfile.write(body)
            self._observe(code)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _canon_path(self) -> None:
            """Accept the versioned API alias: /api/v1/... maps to /api/..."""
            self.path = re.sub(r"^/api/v1(/|$|\?)", r"/api\1", self.path)

        def _operational(self) -> bool:
            """Unauthenticated liveness/metrics endpoints (still host+rate gated)."""
            path = self.path.split("?")[0]
            if path not in ("/health", "/metrics", "/api/version"):
                return False
            if not active_guard.host_ok(self.headers.get("Host")):
                self._json(403, {"error": "forbidden: unexpected Host header"})
                return True
            # /metrics may be protected (operational data) via env opt-in.
            if path == "/metrics" and os.environ.get("AIO_METRICS_PROTECTED") == "1":
                if not active_guard.authorized(active_guard.extract_token(self.headers, self.path)):
                    self._json(401, {"error": "unauthorized: metrics are protected"})
                    return True
            if path == "/metrics":
                extra = {
                    "conversations": float(len(service.conversations)),
                    "tokens_input_total": float(service.usage.get("input_tokens", 0)),
                    "tokens_output_total": float(service.usage.get("output_tokens", 0)),
                }
                self._send(200, active_metrics.prometheus(extra).encode("utf-8"),
                           "text/plain; version=0.0.4; charset=utf-8")
            else:
                self._json(200, {"status": "ok", "version": __version__,
                                 "api_version": API_VERSION, "uptime_s": round(active_metrics.uptime_s(), 1)})
            return True

        def do_GET(self):  # noqa: N802
            self._canon_path()
            if self._operational():
                return
            if not self._preflight():
                return
            asset = static_asset(self.path.split("?")[0])
            if asset is not None:
                body, content_type = asset
                self._send(200, body, content_type)
            elif self.path == "/api/info":
                self._json(200, service.info())
            elif self.path == "/api/providers":
                self._json(200, service.providers_info())
            elif self.path == "/api/usage":
                self._json(200, service.usage_info())
            elif self.path == "/api/sessions":
                self._json(200, service.list_sessions())
            elif self.path == "/api/mcp":
                self._json(200, service.mcp_info())
            elif self.path == "/api/checkpoints":
                self._json(200, service.checkpoints_info())
            elif self.path == "/api/commands":
                self._json(200, service.list_commands())
            elif self.path.startswith("/api/chat/stream"):
                self._chat_stream()
            else:
                self._json(404, {"error": "not found"})

        def _chat_stream(self, payload=None):
            if payload is None:  # GET: read message from the query string
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                payload = {"message": (qs.get("message") or [""])[0],
                           "conv": (qs.get("conv") or ["default"])[0]}
            message = payload.get("message", "")
            images = payload.get("images") or []
            files = payload.get("files") or []
            conv = payload.get("conv", "default")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self._security_headers()
            self.end_headers()

            def emit(event):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                self.wfile.flush()

            try:
                service.chat_stream(message, emit, images=images, conv_id=conv, files=files)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                pass

        def do_POST(self):  # noqa: N802
            self._canon_path()
            if not self._preflight():
                return
            if active_guard.body_too_large(self.headers.get("Content-Length")):
                self._json(413, {"error": "request body too large"})
                return
            try:
                payload = self._read_json()
                if self.path == "/api/chat/stream":
                    self._chat_stream(payload)
                    return
                if self.path == "/api/chat":
                    self._json(200, service.chat(
                        payload.get("message", ""),
                        images=payload.get("images"), conv_id=payload.get("conv", "default"),
                        files=payload.get("files")))
                elif self.path == "/api/approve":
                    self._json(200, service.resolve_approval(
                        payload.get("id", ""), payload.get("decision", "no")))
                elif self.path == "/api/reset":
                    self._json(200, service.reset(payload.get("conv", "default")))
                elif self.path == "/api/conversation/close":
                    self._json(200, service.close_conversation(payload.get("conv", "")))
                elif self.path == "/api/mcp/add":
                    args = payload.get("args")
                    if isinstance(args, str):
                        import shlex
                        args = shlex.split(args)
                    self._json(200, service.mcp_add(
                        payload.get("name", ""), payload.get("command", ""),
                        args=args, env=payload.get("env")))
                elif self.path == "/api/mcp/start":
                    self._json(200, service.mcp_start(payload.get("name", "")))
                elif self.path == "/api/mcp/remove":
                    self._json(200, service.mcp_remove(payload.get("name", "")))
                elif self.path == "/api/mcp/restart":
                    self._json(200, service.mcp_restart())
                elif self.path == "/api/config":
                    self._json(200, service.configure(payload.get("provider"), payload.get("model")))
                elif self.path == "/api/plan":
                    self._json(200, service.set_plan_mode(bool(payload.get("on"))))
                elif self.path == "/api/permission-mode":
                    self._json(200, service.set_permission_mode(payload.get("mode", "default")))
                elif self.path == "/api/rewind":
                    self._json(200, service.rewind(payload.get("id")))
                elif self.path == "/api/thinking":
                    self._json(200, service.set_thinking(payload.get("tokens", 0)))
                elif self.path == "/api/style":
                    self._json(200, service.set_output_style(payload.get("style", "default")))
                elif self.path == "/api/providers":
                    self._json(200, service.set_provider_key(
                        payload.get("provider", ""),
                        api_key=payload.get("api_key"),
                        model=payload.get("model"),
                        base_url=payload.get("base_url"),
                        make_active=bool(payload.get("make_active")),
                        budget=payload.get("budget"),
                    ))
                elif self.path == "/api/providers/test":
                    self._json(200, service.test_provider(payload.get("provider", "")))
                elif self.path == "/api/sessions/save":
                    self._json(200, service.save_session(
                        payload.get("title"), payload.get("id"), conv_id=payload.get("conv", "default")))
                elif self.path == "/api/sessions/load":
                    self._json(200, service.load_session(
                        payload.get("id", ""), conv_id=payload.get("conv", "default")))
                elif self.path == "/api/sessions/delete":
                    self._json(200, service.delete_session(payload.get("id", "")))
                elif self.path == "/api/sessions/export":
                    self._json(200, service.export_session(
                        payload.get("conv", "default"), payload.get("format", "md")))
                elif self.path == "/api/sessions/import":
                    self._json(200, service.import_session(
                        payload.get("data"), conv_id=payload.get("conv")))
                elif self.path == "/api/sessions/search":
                    self._json(200, service.search_sessions(payload.get("query", "")))
                elif self.path == "/api/exec":
                    self._json(200, service.exec_command(
                        payload.get("command", ""), payload.get("shell", "bash")))
                elif self.path == "/api/git":
                    self._json(200, service.git_action(
                        payload.get("action", "status"),
                        payload.get("message", ""),
                        payload.get("pathspec", "-A"),
                        name=payload.get("name", "")))
                elif self.path == "/api/server":
                    action = payload.get("action", "status")
                    if action == "start":
                        self._json(200, service.server_start(payload.get("port", 8080)))
                    elif action == "stop":
                        self._json(200, service.server_stop())
                    else:
                        self._json(200, service.server_status())
                elif self.path == "/api/fs/tree":
                    self._json(200, service.fs_tree())
                elif self.path == "/api/fs/read":
                    self._json(200, service.fs_read(payload.get("path", "")))
                elif self.path == "/api/fs/write":
                    self._json(200, service.fs_write(payload.get("path", ""), payload.get("content", "")))
                else:
                    self._json(404, {"error": "not found"})
            except ProviderError as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def _wrap_tls(httpd: ThreadingHTTPServer, certfile: str, keyfile: str | None) -> None:
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)


def serve(
    config: Config, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False,
    token: str | None = None, require_auth: bool = True, auto_approve: bool = False,
    tls_cert: str | None = None, tls_key: str | None = None,
) -> None:
    service = AgentService(config, gated=not auto_approve)
    if not require_auth:
        token = None
    elif token is None:
        token = new_token()
    # Restrict the Host header to loopback unless explicitly bound to all
    # interfaces (an explicit "expose me" choice); the token still guards it.
    exposed = host in ("0.0.0.0", "::", "")
    allowed = None if exposed else loopback_allowlist() | {host.lower()}
    guard = WebGuard(token, allowed)
    metrics = Metrics()
    httpd = ThreadingHTTPServer((host, port), _make_handler(service, guard, metrics))
    scheme = "http"
    if tls_cert:
        _wrap_tls(httpd, tls_cert, tls_key)
        scheme = "https"

    browse_host = "localhost" if exposed else host
    url = f"{scheme}://{browse_host}:{port}"
    open_url = f"{url}/?token={token}" if token else url
    print(f"AIO web dashboard running at {url}")
    if token:
        print(f"open this URL (it carries your one-time access token):\n  {open_url}")
    else:
        print("WARNING: authentication is DISABLED (--web-no-auth). Anyone who can "
              "reach this port can run shell and file tools.")
    print(f"provider={config.provider}  model={config.active.model}  workdir={config.workdir}")
    if auto_approve:
        print("WARNING: tool calls are AUTO-APPROVED (--web-auto-approve). Ctrl+C to stop.")
    else:
        print("side-effecting tool calls require approval in the dashboard. Ctrl+C to stop.")
    if open_browser:
        import threading
        import webbrowser

        # Open shortly after the server starts listening.
        threading.Timer(0.8, lambda: webbrowser.open(open_url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        service.server_stop()
        service.stop_mcp()
        httpd.server_close()


