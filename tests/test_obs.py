"""Tests for observability: metrics, structured logs, /health and /metrics."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from aio.obs import API_VERSION, Metrics, log_event


# -- Metrics ----------------------------------------------------------------

def test_metrics_counts_and_prometheus():
    m = Metrics()
    m.record(200)
    m.record(200)
    m.record(404)
    assert m.requests_total == 3 and m.errors_total == 1
    text = m.prometheus({"conversations": 2.0})
    assert "aio_http_requests_total 3" in text
    assert "aio_http_errors_total 1" in text
    assert 'aio_http_responses_total{status="200"} 2' in text
    assert "aio_conversations 2.0" in text
    assert "aio_uptime_seconds" in text


def test_log_event_writes_json_line(capsys):
    log_event("warn", event="test", path="/x", status=503)
    err = capsys.readouterr().err.strip()
    record = json.loads(err)
    assert record["level"] == "warn" and record["event"] == "test"
    assert record["status"] == 503 and "ts" in record


# -- live endpoints ---------------------------------------------------------

def _service(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config
    from aio.service import AgentService

    return AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))


def _serve(tmp_path, monkeypatch):
    from aio.obs import Metrics
    from aio.security import WebGuard, loopback_allowlist
    from aio.web import _make_handler

    guard = WebGuard(token="secret", allowed_hosts=loopback_allowlist())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_service(tmp_path, monkeypatch), guard, Metrics()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode(), r.headers


def test_health_is_unauthenticated(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        # no token, yet health must answer (monitoring needs it)
        code, body, headers = _get(f"{base}/health")
        assert code == 200
        d = json.loads(body)
        assert d["status"] == "ok" and d["api_version"] == API_VERSION
        assert "version" in d
        # every response advertises the API version
        assert headers.get("X-AIO-API-Version") == API_VERSION
    finally:
        httpd.shutdown()


def test_metrics_endpoint_unauthenticated_prometheus(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        code, body, headers = _get(f"{base}/metrics")
        assert code == 200
        assert "text/plain" in headers.get("Content-Type", "")
        assert "aio_http_requests_total" in body and "aio_conversations" in body
    finally:
        httpd.shutdown()


def test_protected_routes_still_need_auth(tmp_path, monkeypatch):
    import urllib.error
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        try:
            _get(f"{base}/api/info")
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 401  # operational endpoints are open; the API is not
    finally:
        httpd.shutdown()
