"""Tests for the symbol index (#K) and find_symbol tool."""

from __future__ import annotations

from aio.codeindex import CodeIndex
from aio.tools import ToolContext, ToolError, default_registry
from aio.tools.symbols import FindSymbolTool


def _make_repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "def build_thing():\n    return 1\n\n"
        "class Widget:\n    def render(self):\n        return 'x'\n"
    )
    (tmp_path / "app.js").write_text(
        "export function startServer() {}\nclass Router {}\n"
    )
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("def secret(): pass\n")
    return tmp_path


def test_index_python_and_js(tmp_path):
    idx = CodeIndex(_make_repo(tmp_path)).build()
    assert idx.n_files >= 2 and idx.n_symbols >= 5
    assert "build_thing" in idx.symbols
    assert "Widget" in idx.symbols and "Widget.render" in idx.symbols
    assert "startServer" in idx.symbols and "Router" in idx.symbols
    # gitignored file's symbol is excluded
    assert "secret" not in idx.symbols


def test_lookup_exact_and_substring(tmp_path):
    idx = CodeIndex(_make_repo(tmp_path)).build()
    exact = idx.lookup("build_thing")
    assert exact and exact[0]["path"].endswith("core.py") and exact[0]["kind"] == "function"
    # substring / method-tail match
    by_method = idx.lookup("render")
    assert any(h["symbol"] == "Widget.render" for h in by_method)


def test_find_symbol_tool(tmp_path):
    repo = _make_repo(tmp_path)
    ctx = ToolContext(workdir=repo)
    out = FindSymbolTool().run({"name": "startServer"}, ctx)
    assert "app.js" in out and "startServer" in out
    # index is cached on the context
    assert ctx.code_index is not None
    # unknown symbol
    assert "No symbol" in FindSymbolTool().run({"name": "doesNotExist"}, ctx)


def test_find_symbol_requires_name(tmp_path):
    import pytest
    with pytest.raises(ToolError):
        FindSymbolTool().run({"name": ""}, ToolContext(workdir=tmp_path))


def test_find_symbol_registered_and_readonly():
    assert "find_symbol" in default_registry().names()
    from aio.agent import READONLY_TOOLS
    assert "find_symbol" in READONLY_TOOLS  # usable in plan mode
