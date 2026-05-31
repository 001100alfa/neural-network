"""End-to-end behavioural test of the (now modular) dashboard in a real browser.

Skipped unless Playwright + a Chromium build are available (so it runs locally
and wherever a browser is installed, and quietly skips in a bare CI runner). It
loads the real ES-module frontend, drives the approval flow, and asserts the
side-effect actually happened — catching breakage that `node --check` and the
route-contract test can't.
"""

from __future__ import annotations

import threading
import time

import pytest


def _playwright_or_skip():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - depends on environment
        pytest.skip("playwright not installed")
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception:  # pragma: no cover - no browser binary
        pytest.skip("no Chromium build available for Playwright")
    return pw, browser


def _serve_with_provider(tmp_path, provider):
    from http.server import ThreadingHTTPServer

    from aio.config import load_config
    from aio.service import AgentService
    from aio.web import _make_handler

    svc = AgentService(load_config(workdir=tmp_path, overrides={"provider": "anthropic"}))
    svc.agent.provider = provider
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(svc))  # no guard: local test
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def test_dashboard_loads_modules_and_runs_approval_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.providers import AssistantTurn, ToolCall

    class _Stub:
        def __init__(self): self.n = 0
        def stream_chat(self, messages, tools=None, system=None, on_delta=None):
            self.n += 1
            if self.n == 1:
                return AssistantTurn(content="creating", tool_calls=[ToolCall(
                    "1", "write_file", {"path": "hello.txt", "content": "hi"})])
            return AssistantTurn(content="done", tool_calls=[])

    pw, browser = _playwright_or_skip()
    httpd, base = _serve_with_provider(tmp_path, _Stub())
    errors: list[str] = []
    try:
        page = browser.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base, wait_until="networkidle")
        time.sleep(0.3)

        # the ES modules (app.js + util.js + editor.js) loaded without error
        assert errors == [], f"console/page errors on load: {errors}"
        # editor.js self-initialised (its file <select> exists and is populated)
        assert page.eval_on_selector("#edFile", "el => el.tagName") == "SELECT"

        # drive the approval flow: send a message, approve the write_file
        page.fill("#input", "make hello.txt")
        page.click("#send")
        page.wait_for_selector(".event.appr", timeout=8000)
        assert not (tmp_path / "hello.txt").exists()      # gated: not written yet
        page.click(".appr-actions button:has-text('Approve')")
        for _ in range(50):
            if (tmp_path / "hello.txt").exists():
                break
            time.sleep(0.1)
        assert (tmp_path / "hello.txt").read_text() == "hi"
        assert errors == [], f"console/page errors during flow: {errors}"
    finally:
        browser.close()
        pw.stop()
        httpd.shutdown()
