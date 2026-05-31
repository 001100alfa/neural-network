"""Tests for the vim modal-editing engine behind the curses TUI."""

from __future__ import annotations

from aio.tui import ViBuffer


def _type(buf: ViBuffer, s: str):
    for ch in s:
        buf.feed(ch)


def test_insert_and_submit():
    b = ViBuffer(mode="insert")
    _type(b, "hello")
    assert b.text == "hello" and b.cursor == 5
    assert b.feed("\n") == "submit"
    assert b.take() == "hello" and b.text == ""


def test_backspace():
    b = ViBuffer(mode="insert")
    _type(b, "abc")
    b.feed("\x7f")
    assert b.text == "ab"


def test_esc_enters_normal_then_i_returns_to_insert():
    b = ViBuffer(mode="insert")
    _type(b, "abc")
    b.feed("\x1b")
    assert b.mode == "normal"
    b.feed("i")
    assert b.mode == "insert"


def test_normal_motions_h_l_0_dollar():
    b = ViBuffer(mode="insert")
    _type(b, "hello")
    b.feed("\x1b")             # normal; cursor at 4
    b.feed("0"); assert b.cursor == 0
    b.feed("$"); assert b.cursor == 4
    b.feed("h"); assert b.cursor == 3
    b.feed("l"); assert b.cursor == 4


def test_normal_x_deletes_char():
    b = ViBuffer(mode="insert")
    _type(b, "cat")
    b.feed("\x1b"); b.feed("0")
    b.feed("x")
    assert b.text == "at"


def test_dd_clears_line():
    b = ViBuffer(mode="insert")
    _type(b, "delete me")
    b.feed("\x1b")
    b.feed("d"); b.feed("d")
    assert b.text == ""


def test_dw_deletes_word():
    b = ViBuffer(mode="insert")
    _type(b, "one two three")
    b.feed("\x1b"); b.feed("0")
    b.feed("d"); b.feed("w")
    assert b.text == "two three"


def test_cc_clears_and_enters_insert():
    b = ViBuffer(mode="insert")
    _type(b, "redo this")
    b.feed("\x1b")
    b.feed("c"); b.feed("c")
    assert b.text == "" and b.mode == "insert"


def test_w_and_b_word_motion():
    b = ViBuffer(mode="insert")
    _type(b, "alpha beta gamma")
    b.feed("\x1b"); b.feed("0")
    b.feed("w"); assert b.cursor == 6      # start of "beta"
    b.feed("w"); assert b.cursor == 11     # start of "gamma"
    b.feed("b"); assert b.cursor == 6      # back to "beta"


def test_A_appends_at_end_in_insert():
    b = ViBuffer(mode="normal")
    _type(b, "")  # nothing
    b.text = "abc"; b.cursor = 0
    b.feed("A")
    assert b.mode == "insert" and b.cursor == 3


def test_submit_from_normal_mode():
    b = ViBuffer(mode="normal")
    b.text = "go"; b.cursor = 0
    assert b.feed("\n") == "submit"
