"""Frontend health tests for the `aio --web` dashboard.

The dashboard used to be one ~1200-line inline HTML/JS string with zero
automated coverage: if a button broke or the JS referenced a dead endpoint,
nothing caught it. These tests treat the split-out static assets
(``aio/static/{index.html,app.css,app.js}``) as a contract:

* the JavaScript actually parses (``node --check`` when available);
* the CSS braces balance;
* every tab button has a matching panel and vice versa;
* every ``/api/...`` endpoint the JS calls is handled by a route in web.py
  (catches frontend/backend drift), and every backend route is reachable.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest

_STATIC = files("aio") / "static"


def _asset(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _asset_path(name: str) -> str:
    import aio

    return os.path.join(os.path.dirname(aio.__file__), "static", name)


def _web_source() -> str:
    from aio import web

    return Path(web.__file__).read_text(encoding="utf-8")


# -- asset integrity --------------------------------------------------------

def test_js_parses_with_node():
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on environment
        pytest.skip("node not available to syntax-check the dashboard JS")
    for name in ("app.js", "util.js"):
        result = subprocess.run(
            [node, "--check", _asset_path(name)], capture_output=True, text=True,
        )
        assert result.returncode == 0, f"{name} has a syntax error:\n{result.stderr}"


def test_js_unit_tests_pass():
    """Run the node:test behavioural unit tests for the pure JS logic (util.js)."""
    node = shutil.which("node")
    if not node:  # pragma: no cover - depends on environment
        pytest.skip("node not available to run the JS unit tests")
    test_file = Path(__file__).parent / "frontend" / "util.test.mjs"
    result = subprocess.run(
        [node, "--test", str(test_file)], capture_output=True, text=True,
    )
    assert result.returncode == 0, f"JS unit tests failed:\n{result.stdout}\n{result.stderr}"


def test_css_braces_balanced():
    css = _asset("app.css")
    assert css.count("{") == css.count("}"), "unbalanced { } in app.css"


def test_index_links_external_assets_and_has_no_inline_blob():
    html = _asset("index.html")
    assert 'href="app.css"' in html, "index.html must link app.css"
    assert 'src="app.js"' in html, "index.html must link app.js"
    # the whole point of the split: no inline style/script blob remains
    assert "<style>" not in html
    assert "<script>" not in html


def test_static_asset_dispatch():
    from aio.web_ui import static_asset

    assert static_asset("/")[1].startswith("text/html")
    assert static_asset("/index.html")[1].startswith("text/html")
    assert static_asset("/app.css")[1].startswith("text/css")
    assert static_asset("/app.js")[1].startswith("application/javascript")
    assert static_asset("/nope") is None
    # query stripping is the caller's (web.py) job, so a raw query misses here
    assert static_asset("/app.js?v=2") is None
    # path traversal never resolves to a known asset name
    assert static_asset("/../etc/passwd") is None


# -- tab/panel pairing ------------------------------------------------------

def test_every_tab_button_has_a_panel_and_vice_versa():
    html = _asset("index.html")
    tabs = set(re.findall(r'data-tab="([^"]+)"', html))
    panels = set(re.findall(r'id="tab-([^"]+)"', html))
    assert tabs, "no data-tab buttons found in index.html"
    assert tabs == panels, (
        f"tab buttons and panels disagree: "
        f"buttons-without-panels={tabs - panels}, panels-without-buttons={panels - tabs}"
    )


# -- frontend <-> backend route contract -----------------------------------

def _backend_routes() -> tuple[set[str], set[str]]:
    src = _web_source()
    exact = set(re.findall(r'self\.path == "([^"]+)"', src))
    for tup in re.findall(r'self\.path in \(([^)]*)\)', src):
        exact |= set(re.findall(r'"([^"]+)"', tup))
    prefixes = set(re.findall(r'self\.path\.startswith\("([^"]+)"\)', src))
    return exact, prefixes


def _js_modules() -> list[str]:
    import os
    static = os.path.join(os.path.dirname(__import__("aio").__file__), "static")
    return [f for f in os.listdir(static) if f.endswith(".js")]


def _js_endpoints() -> set[str]:
    js = "\n".join(_asset(name) for name in _js_modules())  # scan every ES module
    eps: set[str] = set()
    for raw in re.findall(r"""(?:fetch|EventSource)\(\s*[`'"]([^`'"]+)""", js):
        path = raw.split("?")[0].split("${")[0].rstrip("/")
        if path.startswith("/api"):
            eps.add(path)
    return eps


def test_every_js_endpoint_has_a_backend_route():
    exact, prefixes = _backend_routes()
    endpoints = _js_endpoints()
    assert endpoints, "no /api endpoints discovered in app.js"
    missing = [
        ep for ep in sorted(endpoints)
        if ep not in exact and not any(ep.startswith(p) for p in prefixes)
    ]
    assert not missing, f"app.js calls endpoints with no backend route: {missing}"


def test_no_orphan_backend_api_routes():
    """Every JSON /api route the server handles is actually called by the UI.

    Guards against dead server code drifting away from the dashboard. A small
    allow-list covers routes the UI reaches indirectly or that are intentional
    extras.
    """
    exact, prefixes = _backend_routes()
    endpoints = _js_endpoints()
    allow = {"/api/chat", "/api/sessions/delete"}  # POST fallback + delete-by-id
    backend_api = {r for r in exact if r.startswith("/api")} | {
        p for p in prefixes if p.startswith("/api")
    }
    orphans = [
        r for r in sorted(backend_api)
        if r not in allow and not any(ep.startswith(r) or r.startswith(ep) for ep in endpoints)
    ]
    assert not orphans, f"backend /api routes never called by the UI: {orphans}"
