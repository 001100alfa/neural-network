"""HTTP-level tests that drive the real web handler across many routes.

The service methods are unit-tested elsewhere; this exercises the do_GET/do_POST
dispatch itself (routing, JSON in/out, error envelope) over a socket.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer


def _serve(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    from aio.config import load_config
    from aio.service import AgentService
    from aio.web import _make_handler

    svc = AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(svc))  # no guard: local
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _req(url, obj=None):
    data = json.dumps(obj).encode() if obj is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def test_get_routes(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        for path in ("/api/info", "/api/providers", "/api/usage", "/api/sessions",
                     "/api/mcp", "/api/checkpoints", "/api/commands"):
            code, body = _req(base + path)
            assert code == 200 and isinstance(body, (dict, list)), path
    finally:
        httpd.shutdown()


def test_post_routes(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        assert _req(base + "/api/plan", {"on": True})[1]["plan_mode"] is True
        assert _req(base + "/api/plan", {"on": False})[1]["plan_mode"] is False
        assert _req(base + "/api/thinking", {"tokens": 4096})[0] == 200
        assert _req(base + "/api/style", {"style": "concise"})[0] == 200
        assert _req(base + "/api/reset", {"conv": "default"})[1]["ok"] is True
        # filesystem panel round-trip through the handler
        assert _req(base + "/api/fs/write", {"path": "x.txt", "content": "hi"})[1]["ok"] is True
        assert _req(base + "/api/fs/read", {"path": "x.txt"})[1]["content"] == "hi"
        assert "files" in _req(base + "/api/fs/tree", {})[1]
        # shell exec panel
        out = _req(base + "/api/exec", {"command": "echo hello", "shell": "bash"})[1]
        assert "hello" in out["output"]
    finally:
        httpd.shutdown()


def test_unknown_route_404(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        assert _req(base + "/api/nope")[0] == 404
        assert _req(base + "/api/nope", {"x": 1})[0] == 404
    finally:
        httpd.shutdown()


def test_approve_route(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        # resolving an unknown approval id is a clean no-op (ok: False)
        code, body = _req(base + "/api/approve", {"id": "nope", "decision": "yes"})
        assert code == 200 and body["ok"] is False
    finally:
        httpd.shutdown()
