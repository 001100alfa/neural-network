"""Tests for the layered token-counting backend."""

from __future__ import annotations

import pytest

from aio import tokens
from aio.tokens import _heuristic_count, active_backend, count_text


def test_active_backend_reports_a_known_value():
    assert active_backend() in {"heuristic"} or active_backend().startswith("tiktoken:")


def test_empty_is_zero():
    assert count_text("") == 0


def test_heuristic_properties():
    # punctuation counts as its own token
    assert _heuristic_count("a, b") >= 3
    # long words split into ~4-char subwords -> more than one token
    assert _heuristic_count("supercalifragilistic") > 1
    # monotonic-ish: more text is never fewer tokens
    short = _heuristic_count("hello world")
    long = _heuristic_count("hello world " * 10)
    assert long > short


def test_count_text_uses_heuristic_when_no_tiktoken(monkeypatch):
    # Force the no-tiktoken path regardless of environment.
    monkeypatch.setattr(tokens, "_ENC", None)
    text = "def parse_http_request(req): return req.headers"
    assert count_text(text) == _heuristic_count(text)


def test_count_text_uses_tiktoken_when_available():
    if tokens._ENC is None:
        pytest.skip("tiktoken not installed (CI runs the heuristic path)")
    # Real BPE tokenizer path: exact, deterministic count.
    assert active_backend().startswith("tiktoken:")
    n = count_text("def parseHTTPRequest(): return 42")
    assert n > 0
    # tiktoken must agree with the underlying encoder.
    assert n == len(tokens._ENC.encode("def parseHTTPRequest(): return 42"))
