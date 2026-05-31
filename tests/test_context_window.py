"""Tests for per-model context-window sizing (the '1M token' capability).

The agent used to compact at a hardcoded 120k regardless of model, so a 1M-token
model's window was never used. Now the window is detected from the model name
(or set explicitly), and flows into the agent's compaction threshold.
"""

from __future__ import annotations

from aio.config import (
    DEFAULT_CONTEXT_WINDOW,
    context_window_for,
    load_config,
)


def test_window_detected_per_model():
    assert context_window_for("claude-sonnet-4-6") == 1_000_000
    assert context_window_for("claude-opus-4-8") == 200_000
    assert context_window_for("gpt-4.1") == 1_000_000
    assert context_window_for("gpt-4o") == 128_000
    assert context_window_for("gemini-1.5-pro") == 1_000_000
    assert context_window_for("qwen2.5-coder") == 128_000


def test_unknown_model_falls_back():
    assert context_window_for("some-random-model") == DEFAULT_CONTEXT_WINDOW
    assert context_window_for("") == DEFAULT_CONTEXT_WINDOW


def test_longest_match_wins():
    # 'claude-sonnet-4' (1M) must beat the generic 'claude' (200k) substring
    assert context_window_for("claude-sonnet-4-6") == 1_000_000
    assert context_window_for("claude-opus-4-8") == 200_000


def test_effective_window_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.delenv("AIO_CONTEXT_WINDOW", raising=False)
    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic", "model": "claude-sonnet-4-6"})
    assert cfg.effective_context_window == 1_000_000     # auto from model


def test_explicit_override_beats_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    cfg = load_config(workdir=tmp_path, overrides={
        "provider": "anthropic", "model": "claude-sonnet-4-6", "context_window": 300_000})
    assert cfg.effective_context_window == 300_000


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("AIO_CONTEXT_WINDOW", "750000")
    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic", "model": "x"})
    assert cfg.effective_context_window == 750_000


def test_window_flows_into_agent(tmp_path, monkeypatch):
    # the service builds agents with context_limit = effective window
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "k.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    monkeypatch.delenv("AIO_CONTEXT_WINDOW", raising=False)
    from aio.service import AgentService
    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic", "model": "claude-sonnet-4-6"})
    svc = AgentService(cfg)
    assert svc.agent.context_limit == 1_000_000          # not the old hardcoded 120k
