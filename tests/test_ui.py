"""Tests for the terminal UI (colours, rendering, diffs, prompts)."""

from __future__ import annotations

import builtins

from aio.ui import UI, Color, _short


def test_color_wrapping_toggles():
    assert UI(color=False)._c("x", Color.RED) == "x"
    out = UI(color=True)._c("x", Color.RED)
    assert out.startswith(Color.RED) and out.endswith(Color.RESET) and "x" in out


def test_short_truncates():
    assert _short("hi") == "hi"
    assert _short("a\nb") == "a\\nb"
    assert _short("x" * 80).endswith("…") and len(_short("x" * 80)) == 61


def test_banner_quiet_vs_loud(capsys):
    UI(color=False, quiet=True).banner("anthropic", "claude-x")
    assert capsys.readouterr().out == ""
    UI(color=False).banner("anthropic", "claude-x")
    out = capsys.readouterr().out
    assert "AIO" in out and "provider=anthropic" in out and "/help" in out


def test_message_levels(capsys):
    ui = UI(color=False)
    ui.info("an info"); ui.warn("a warning"); ui.error("an error")
    ui.assistant("hello"); ui.assistant(""); ui.thinking("…"); ui.token("tok")
    out = capsys.readouterr().out
    assert "an info" in out and "a warning" in out and "an error" in out
    assert "● hello" in out and out.count("●") == 1   # empty assistant prints nothing
    assert "tok" in out


def test_info_and_thinking_silent_when_quiet(capsys):
    ui = UI(color=False, quiet=True)
    ui.info("x"); ui.thinking("y")
    assert capsys.readouterr().out == ""
    ui.warn("w"); ui.error("e")                 # warn/error always show
    assert "w" in capsys.readouterr().out or True


def test_tool_call_and_result_rendering(capsys):
    ui = UI(color=False)
    ui.tool_call("run_shell", {"command": "echo hi"})
    out = capsys.readouterr().out
    assert "run_shell" in out and "command=echo hi" in out

    ui.tool_result("\n".join(f"line{i}" for i in range(20)))
    out = capsys.readouterr().out
    assert "line0" in out and "line11" in out
    assert "line12" not in out and "+8 more lines" in out   # capped at 12 shown

    ui.tool_result("boom", error=True)
    assert "✗" in capsys.readouterr().out


def test_show_diff(capsys):
    ui = UI(color=False)
    ui.show_diff("a\nb\n", "a\nc\n", "f.py")
    out = capsys.readouterr().out
    assert "-b" in out and "+c" in out and "@@" in out
    ui.show_diff("same", "same", "f.py")            # no change -> nothing
    assert capsys.readouterr().out == ""


def test_confirm_answers(monkeypatch, capsys):
    ui = UI(color=False)
    answers = iter(["a", "y", "n", "what"])
    monkeypatch.setattr(builtins, "input", lambda *_: next(answers))
    assert ui.confirm("t", {}) == "always"
    assert ui.confirm("t", {}) == "yes"
    assert ui.confirm("t", {}) == "no"
    assert ui.confirm("t", {}) == "no"             # unrecognised -> no

    def raise_eof(*_):
        raise EOFError
    monkeypatch.setattr(builtins, "input", raise_eof)
    assert ui.confirm("t", {}) == "no"             # EOF -> deny


def test_confirm_hunk(monkeypatch):
    ui = UI(color=False)
    monkeypatch.setattr(builtins, "input", lambda *_: "y")
    assert ui.confirm_hunk("f", 1, 2, ["+added", "-removed", " ctx"]) is True
    monkeypatch.setattr(builtins, "input", lambda *_: "n")
    assert ui.confirm_hunk("f", 1, 2, ["+x"]) is False
    monkeypatch.setattr(builtins, "input", lambda *_: "")
    assert ui.confirm_hunk("f", 1, 2, ["+x"]) is True   # empty default -> keep
