"""Tests for ops hardening: versioned API alias, metrics protection, TLS."""

from __future__ import annotations

import ssl
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from aio.obs import Metrics
from aio.security import WebGuard, loopback_allowlist
from aio.web import _make_handler, _wrap_tls


def _service(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config
    from aio.service import AgentService

    return AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))


def _serve(tmp_path, monkeypatch, token="secret"):
    guard = WebGuard(token=token, allowed_hosts=loopback_allowlist())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_service(tmp_path, monkeypatch), guard, Metrics()))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(url, headers=None, context=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5, context=context) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# -- versioned API alias ----------------------------------------------------

def test_api_v1_alias_matches_unversioned(tmp_path, monkeypatch):
    httpd, base = _serve(tmp_path, monkeypatch)
    auth = {"Authorization": "Bearer secret"}
    try:
        a = _get(f"{base}/api/info", auth)
        b = _get(f"{base}/api/v1/info", auth)
        assert a[0] == 200 and b[0] == 200
        assert b[1] == a[1]                       # identical payload via the alias
        # operational endpoint also versionable
        assert _get(f"{base}/api/v1/version")[0] == 200
    finally:
        httpd.shutdown()


# -- metrics protection toggle ----------------------------------------------

def test_metrics_open_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AIO_METRICS_PROTECTED", raising=False)
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        assert _get(f"{base}/metrics")[0] == 200          # no token needed
    finally:
        httpd.shutdown()


def test_metrics_protected_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AIO_METRICS_PROTECTED", "1")
    httpd, base = _serve(tmp_path, monkeypatch)
    try:
        assert _get(f"{base}/metrics")[0] == 401                              # blocked
        assert _get(f"{base}/metrics", {"Authorization": "Bearer secret"})[0] == 200
    finally:
        httpd.shutdown()


# -- TLS --------------------------------------------------------------------

def _self_signed(tmp_path: Path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    proc = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=localhost"],
        capture_output=True,
    )
    if proc.returncode != 0:  # pragma: no cover - depends on environment
        pytest.skip("openssl could not generate a test certificate")
    return str(cert), str(key)


def test_tls_serves_https(tmp_path, monkeypatch):
    if not __import__("shutil").which("openssl"):  # pragma: no cover
        pytest.skip("openssl not available")
    cert, key = _self_signed(tmp_path)
    guard = WebGuard(token=None, allowed_hosts=loopback_allowlist())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(_service(tmp_path, monkeypatch), guard, Metrics()))
    _wrap_tls(httpd, cert, key)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # self-signed: don't verify in the test
    try:
        status, body = _get(f"https://127.0.0.1:{port}/health", context=ctx)
        assert status == 200 and '"status": "ok"' in body
    finally:
        httpd.shutdown()
