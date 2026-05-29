"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from aio.config import load_config


def test_defaults_without_keys(tmp_path: Path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(workdir=tmp_path)
    # With no keys, auto-detect falls back to local ollama.
    assert cfg.provider == "ollama"
    assert cfg.providers["ollama"].base_url.endswith("11434/v1")


def test_auto_detect_anthropic(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg = load_config(workdir=tmp_path)
    assert cfg.provider == "anthropic"
    assert cfg.providers["anthropic"].api_key == "sk-test"


def test_overrides_win(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    cfg = load_config(workdir=tmp_path, overrides={"provider": "openai", "model": "gpt-test"})
    assert cfg.provider == "openai"
    assert cfg.providers["openai"].model == "gpt-test"


def test_project_toml_loaded(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / ".aio.toml").write_text(
        'provider = "anthropic"\n'
        "max_steps = 7\n"
        "[providers.anthropic]\n"
        'model = "custom-model"\n'
        'api_key = "from-file"\n'
    )
    cfg = load_config(workdir=tmp_path)
    assert cfg.provider == "anthropic"
    assert cfg.max_steps == 7
    assert cfg.providers["anthropic"].model == "custom-model"
    assert cfg.providers["anthropic"].api_key == "from-file"


def test_mcp_servers_parsed(tmp_path: Path):
    (tmp_path / ".aio.toml").write_text(
        "[[mcp.servers]]\n"
        'name = "fs"\n'
        'command = "npx"\n'
        'args = ["-y", "server"]\n'
    )
    cfg = load_config(workdir=tmp_path)
    assert cfg.mcp_servers == [{"name": "fs", "command": "npx", "args": ["-y", "server"]}]


def test_unknown_provider_raises(tmp_path: Path):
    with pytest.raises(ValueError):
        load_config(workdir=tmp_path, overrides={"provider": "nope"})
