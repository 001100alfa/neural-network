"""A zero-dependency web server for the AIO dashboard.

Serves the single-page dashboard (see :mod:`aio.web_ui`) and a small JSON API
on top of :class:`aio.service.AgentService`. Built entirely on the Python
standard library (``http.server``). Tool approvals are auto-granted in web mode
(there is no interactive terminal), so it is intended for local/trusted use.

:class:`EventUI` and :class:`AgentService` are re-exported here for backwards
compatibility (``from aio.web import AgentService, EventUI``).
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import Config
from .providers import ProviderError
from .service import AgentService, EventUI, _extract_doc_text
from .web_ui import static_asset

# Service-layer symbols are re-exported for backwards compatibility
# (``from aio.web import AgentService, EventUI``).
__all__ = ["AgentService", "EventUI", "_extract_doc_text", "serve"]


def _make_handler(service: AgentService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # silence default stderr logging
            pass

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self):  # noqa: N802
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
            self.end_headers()

            def emit(event):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
                self.wfile.flush()

            try:
                service.chat_stream(message, emit, images=images, conv_id=conv, files=files)
            except (BrokenPipeError, ConnectionResetError):  # pragma: no cover
                pass

        def do_POST(self):  # noqa: N802
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
                        payload.get("pathspec", "-A")))
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


def serve(
    config: Config, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False
) -> None:
    service = AgentService(config)
    httpd = ThreadingHTTPServer((host, port), _make_handler(service))
    # When bound to 0.0.0.0, the browsable URL is localhost.
    browse_host = "localhost" if host in ("0.0.0.0", "") else host
    url = f"http://{browse_host}:{port}"
    print(f"AIO web dashboard running at {url}")
    print(f"provider={config.provider}  model={config.active.model}  workdir={config.workdir}")
    print("tool calls are auto-approved in web mode. Ctrl+C to stop.")
    if open_browser:
        import threading
        import webbrowser

        # Open shortly after the server starts listening.
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down…")
    finally:
        service.server_stop()
        service.stop_mcp()
        httpd.server_close()


