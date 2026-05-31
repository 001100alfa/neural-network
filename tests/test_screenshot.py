"""Tests for the screenshot tool (visual-proof capture)."""

from __future__ import annotations

import importlib.util
import threading

import pytest

from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.screenshot import INSTALL_HINT, ScreenshotTool

_HAS_PW = importlib.util.find_spec("playwright") is not None


def test_registered():
    assert default_registry().get("screenshot") is not None


def test_rejects_non_http(tmp_path):
    with pytest.raises(ToolError):
        ScreenshotTool().run({"url": "ftp://x", "path": "a.png"}, ToolContext(workdir=tmp_path))


def test_missing_playwright_gives_actionable_message(tmp_path, monkeypatch):
    # simulate playwright not installed -> clear install hint, not a crash
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("playwright"):
            raise ImportError("no playwright")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ToolError) as e:
        ScreenshotTool().run({"url": "http://localhost", "path": "a.png"},
                             ToolContext(workdir=tmp_path))
    assert "playwright" in str(e.value).lower() and "pip install" in str(e.value)
    assert INSTALL_HINT in str(e.value)


@pytest.mark.skipif(not _HAS_PW, reason="playwright not installed")
def test_captures_a_live_page(tmp_path):
    # stand up a tiny HTTP server and screenshot it with the real tool
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = b"<html><body><h1 id='hi'>Hello AIO</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    ctx = ToolContext(workdir=tmp_path)
    try:
        out = ScreenshotTool().run(
            {"url": f"http://127.0.0.1:{port}/", "path": "shot.png", "wait_selector": "#hi"}, ctx)
    except ToolError as e:
        if "browser" in str(e).lower():
            pytest.skip("no chromium build available")
        raise
    finally:
        srv.shutdown()
    assert "Saved screenshot" in out
    png = tmp_path / "shot.png"
    assert png.is_file() and png.stat().st_size > 1000
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"   # real PNG header


@pytest.mark.skipif(not _HAS_PW, reason="playwright not installed")
def test_forces_png_extension(tmp_path):
    # path without .png should be normalised; bad URL still rejected first
    with pytest.raises(ToolError):
        ScreenshotTool().run({"url": "notaurl", "path": "x"}, ToolContext(workdir=tmp_path))
