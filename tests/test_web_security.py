"""Security tests for the web dashboard: auth, host pinning, size & rate caps.

The dashboard runs arbitrary shell/file tools, so these guard against the
"any local process or tab can drive it" hole. WebGuard is unit-tested in
isolation, and the real HTTP handler is exercised end to end over a socket.
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from aio.security import COOKIE_NAME, WebGuard, loopback_allowlist, new_token


# -- WebGuard unit tests ----------------------------------------------------

def test_new_token_is_high_entropy_and_unique():
    a, b = new_token(), new_token()
    assert a != b and len(a) >= 32


def test_auth_disabled_allows_everything():
    g = WebGuard(token=None, allowed_hosts=None)
    assert g.authorized(None) is True
    assert g.authorized("anything") is True


def test_token_must_match_constant_time():
    g = WebGuard(token="secret", allowed_hosts=None)
    assert g.authorized("secret") is True
    assert g.authorized("wrong") is False
    assert g.authorized(None) is False


def test_extract_token_from_header_query_and_cookie():
    g = WebGuard(token="t", allowed_hosts=None)

    class H(dict):
        def get(self, k, d=None):
            return super().get(k, d)

    assert g.extract_token(H({"Authorization": "Bearer abc"}), "/x") == "abc"
    assert g.extract_token(H(), "/x?token=qq") == "qq"
    assert g.extract_token(H({"Cookie": f"{COOKIE_NAME}=ck; other=1"}), "/x") == "ck"
    assert g.extract_token(H(), "/x") is None


def test_host_allowlist():
    g = WebGuard(token=None, allowed_hosts=loopback_allowlist() | {"myhost"})
    assert g.host_ok("127.0.0.1:8765") is True
    assert g.host_ok("localhost") is True
    assert g.host_ok("myhost:9") is True
    assert g.host_ok("evil.example.com") is False        # DNS-rebinding attempt
    assert WebGuard(token=None, allowed_hosts=None).host_ok("anything") is True


def test_body_size_cap():
    g = WebGuard(token=None, allowed_hosts=None, max_body=100)
    assert g.body_too_large(50) is False
    assert g.body_too_large(101) is True
    assert g.body_too_large(None) is False


def test_rate_limit_window():
    g = WebGuard(token=None, allowed_hosts=None, rate=(2, 60))
    assert g.rate_ok("1.2.3.4") is True
    assert g.rate_ok("1.2.3.4") is True
    assert g.rate_ok("1.2.3.4") is False        # third in the window is blocked
    assert g.rate_ok("5.6.7.8") is True         # a different client is independent


# -- live HTTP integration --------------------------------------------------

def _service(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config
    from aio.service import AgentService

    return AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))


def _serve(service, guard):
    from aio.web import _make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(service, guard))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_unauthorized_requests_are_rejected(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    guard = WebGuard(token="topsecret", allowed_hosts=loopback_allowlist())
    httpd, base = _serve(svc, guard)
    try:
        code, _, _ = _get(f"{base}/api/info")                       # no token
        assert code == 401
        code, _, _ = _get(f"{base}/api/info",
                          {"Authorization": "Bearer wrong"})        # bad token
        assert code == 401
        code, body, _ = _get(f"{base}/api/info",
                            {"Authorization": "Bearer topsecret"})  # good token
        assert code == 200 and b"provider" in body
    finally:
        httpd.shutdown()


def test_query_token_handshake_sets_cookie_then_cookie_authorizes(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    guard = WebGuard(token="abc123", allowed_hosts=loopback_allowlist())
    httpd, base = _serve(svc, guard)
    try:
        # load the page with ?token= -> 200 and a Set-Cookie pinning the token
        code, _, headers = _get(f"{base}/?token=abc123")
        assert code == 200
        cookie = headers.get("Set-Cookie") or ""
        assert COOKIE_NAME in cookie and "SameSite=Strict" in cookie and "HttpOnly" in cookie
        # now the browser would send that cookie; it must authorize API calls
        code, _, _ = _get(f"{base}/api/info", {"Cookie": f"{COOKIE_NAME}=abc123"})
        assert code == 200
    finally:
        httpd.shutdown()


def test_forbidden_host_header_is_rejected(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    guard = WebGuard(token=None, allowed_hosts=loopback_allowlist())
    httpd, base = _serve(svc, guard)
    try:
        # spoof a foreign Host header (DNS-rebinding shape) -> 403
        code, _, _ = _get(f"{base}/api/info", {"Host": "evil.example.com"})
        assert code == 403
    finally:
        httpd.shutdown()


def test_oversized_body_is_rejected(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    guard = WebGuard(token=None, allowed_hosts=loopback_allowlist(), max_body=10)
    httpd, base = _serve(svc, guard)
    try:
        req = urllib.request.Request(f"{base}/api/chat", data=b'{"message":"way too long"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 413
    finally:
        httpd.shutdown()


def test_no_guard_handler_is_permissive(tmp_path, monkeypatch):
    """Backwards-compat: _make_handler without a guard requires no token."""
    svc = _service(tmp_path, monkeypatch)
    from aio.web import _make_handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(svc))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        code, body, _ = _get(f"{base}/api/info")
        assert code == 200 and b"provider" in body
    finally:
        httpd.shutdown()
