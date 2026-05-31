"""Tests for read_file symbol= navigation and the verify-guidance prompt."""

from __future__ import annotations

import pytest

from aio.config import DEFAULT_SYSTEM_PROMPT
from aio.tools import ToolContext, ToolError
from aio.tools.files import ReadFileTool, _symbol_span

PY = (
    "import os\n"
    "\n"
    "def alpha():\n"
    "    return 1\n"
    "\n"
    "class Widget:\n"
    "    def render(self):\n"
    "        return 'x'\n"
    "\n"
    "    def hide(self):\n"
    "        return None\n"
    "\n"
    "def omega():\n"
    "    return 2\n"
)


def test_symbol_span_function():
    s = _symbol_span(PY, "alpha")
    assert s == (3, 4)


def test_symbol_span_method():
    s = _symbol_span(PY, "Widget.render")
    assert s and s[0] == 7  # def render line


def test_symbol_span_class_spans_body():
    s = _symbol_span(PY, "Widget")
    assert s and s[0] == 6 and s[1] >= 11


def test_symbol_span_missing():
    assert _symbol_span(PY, "nope") is None


def test_read_file_symbol(tmp_path):
    (tmp_path / "m.py").write_text(PY)
    ctx = ToolContext(workdir=tmp_path)
    out = ReadFileTool().run({"path": "m.py", "symbol": "omega"}, ctx)
    assert "def omega" in out and "alpha" not in out


def test_read_file_symbol_not_found(tmp_path):
    (tmp_path / "m.py").write_text(PY)
    with pytest.raises(ToolError):
        ReadFileTool().run({"path": "m.py", "symbol": "ghost"}, ToolContext(workdir=tmp_path))


def test_symbol_span_braces_js():
    js = "function start() {\n  doThing();\n}\nfunction other() {}\n"
    s = _symbol_span(js, "start")
    assert s and s[0] == 1 and s[1] == 3


def test_verify_guidance_in_prompt():
    assert "ALWAYS verify" in DEFAULT_SYSTEM_PROMPT
    assert "find_symbol" in DEFAULT_SYSTEM_PROMPT
    assert "retry" in DEFAULT_SYSTEM_PROMPT
