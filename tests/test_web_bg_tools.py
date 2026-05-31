"""Tests for the web_fetch (#3) and background-command (#4) tools."""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.background import CheckBackgroundTool, RunBackgroundTool
from aio.tools.web import WebFetchTool, _html_to_text


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><head><style>x{}</style></head><body><h1>Hi</h1><script>bad()</script><p>Para&amp;more</p></body></html>"
    txt = _html_to_text(html)
    assert "Hi" in txt and "Para&more" in txt
    assert "bad()" not in txt and "<" not in txt


def test_web_fetch_rejects_non_http(tmp_path):
    with pytest.raises(ToolError):
        WebFetchTool().run({"url": "ftp://x"}, ToolContext(workdir=tmp_path))


def test_web_fetch_live_local_server(tmp_path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            body = b"<html><body><h1>Hello</h1><p>World</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        out = WebFetchTool().run({"url": f"http://127.0.0.1:{port}/"}, ToolContext(workdir=tmp_path))
        assert "Hello" in out and "World" in out
    finally:
        srv.shutdown()


def test_background_run_and_check(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    started = RunBackgroundTool().run({"command": "echo hello-bg; sleep 0.3"}, ctx)
    assert "bg1" in started
    time.sleep(0.6)
    out = CheckBackgroundTool().run({"id": "bg1"}, ctx)
    assert "hello-bg" in out and "exited" in out
    # listing
    assert "bg1" in CheckBackgroundTool().run({}, ctx)
    # unknown id
    with pytest.raises(ToolError):
        CheckBackgroundTool().run({"id": "bgX"}, ctx)


def test_background_stop(tmp_path):
    ctx = ToolContext(workdir=tmp_path)
    RunBackgroundTool().run({"command": "sleep 30"}, ctx)
    out = CheckBackgroundTool().run({"id": "bg1", "stop": True}, ctx)
    assert "exited" in out


def test_new_tools_registered():
    names = set(default_registry().names())
    assert {"web_fetch", "run_background", "check_background"} <= names
