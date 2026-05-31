"""Tests for the CLI: arg parsing, slash commands, REPL, headless JSON, sessions."""

from __future__ import annotations

import builtins
import json

from aio import cli
from aio.ui import UI


def _config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config

    return load_config(workdir=tmp_path, overrides={"provider": "anthropic"})


class _FakeAgent:
    def __init__(self):
        self.messages = []
        self.run_usage = {"requests": 1, "input_tokens": 5, "output_tokens": 2}
        self.ctx = type("C", (), {"auto_approve": False, "ui": None})()
        self.tools = [type("T", (), {"name": "read_file", "description": "Read a file\nmore"})()]
        self.provider = None
        self.stream = True
        self.ran: list[str] = []
        self.reset_called = False

    def reset(self):
        self.reset_called = True

    def run(self, prompt, images=None):
        self.ran.append(prompt)
        return "ok"


# -- argument parsing -------------------------------------------------------

def test_parser_reads_flags():
    args = cli.build_parser().parse_args(
        ["do a thing", "--provider", "groq", "--reflect", "2", "--web-tls-cert", "c.pem"])
    assert args.prompt == ["do a thing"]
    assert args.provider == "groq" and args.reflect == 2 and args.web_tls_cert == "c.pem"


def test_parser_defaults():
    args = cli.build_parser().parse_args([])
    assert args.prompt == [] and args.reflect == 0 and not args.web


def test_web_token_defaults_to_env(monkeypatch):
    monkeypatch.setenv("AIO_WEB_TOKEN", "from-env-123")
    assert cli.build_parser().parse_args([]).web_token == "from-env-123"
    # an explicit flag still wins over the env default
    assert cli.build_parser().parse_args(["--web-token", "explicit"]).web_token == "explicit"


def test_web_token_none_without_env(monkeypatch):
    monkeypatch.delenv("AIO_WEB_TOKEN", raising=False)
    assert cli.build_parser().parse_args([]).web_token is None


# -- slash commands ---------------------------------------------------------

def test_slash_commands(tmp_path, monkeypatch, capsys):
    cfg = _config(tmp_path, monkeypatch)
    agent = _FakeAgent()
    ui = UI(color=False)

    assert cli._handle_slash("/exit", agent, cfg, ui) is False
    assert cli._handle_slash("/quit", agent, cfg, ui) is False
    assert cli._handle_slash("/help", agent, cfg, ui) is True
    cli._handle_slash("/tools", agent, cfg, ui)
    assert "read_file" in capsys.readouterr().out

    cli._handle_slash("/clear", agent, cfg, ui)
    assert agent.reset_called

    cli._handle_slash("/yes", agent, cfg, ui)
    assert agent.ctx.auto_approve is True
    cli._handle_slash("/yes", agent, cfg, ui)
    assert agent.ctx.auto_approve is False

    cli._handle_slash("/provider bogus", agent, cfg, ui)
    assert "unknown provider" in capsys.readouterr().out
    cli._handle_slash("/model", agent, cfg, ui)
    assert "current model" in capsys.readouterr().out
    cli._handle_slash("/frobnicate", agent, cfg, ui)
    assert "unknown command" in capsys.readouterr().out


def test_slash_provider_switch(tmp_path, monkeypatch, capsys):
    cfg = _config(tmp_path, monkeypatch)
    agent = _FakeAgent()
    cli._handle_slash("/provider ollama", agent, cfg, UI(color=False))
    assert cfg.provider == "ollama" and agent.provider is not None


# -- REPL -------------------------------------------------------------------

def test_repl_runs_then_exits(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    agent = _FakeAgent()
    lines = iter(["hello there", "/exit"])
    monkeypatch.setattr(builtins, "input", lambda *_: next(lines))
    rc = cli.repl(agent, cfg, UI(color=False, quiet=True))
    assert rc == 0 and agent.ran == ["hello there"]


def test_repl_eof_breaks(tmp_path, monkeypatch):
    cfg = _config(tmp_path, monkeypatch)
    agent = _FakeAgent()

    def raise_eof(*_):
        raise EOFError
    monkeypatch.setattr(builtins, "input", raise_eof)
    assert cli.repl(agent, cfg, UI(color=False, quiet=True)) == 0


# -- headless JSON ----------------------------------------------------------

def test_headless_json_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    agent = _FakeAgent()
    rc = cli._run_headless_json(agent, "hi")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["result"] == "ok"
    assert payload["usage"]["requests"] == 1 and "events" in payload


# -- main: list-tools (no provider needed) ----------------------------------

def test_main_list_tools(tmp_path, monkeypatch, capsys):
    _config(tmp_path, monkeypatch)
    rc = cli.main(["--list-tools", "--workdir", str(tmp_path)])
    assert rc == 0
    assert "read_file" in capsys.readouterr().out


# -- session save / load roundtrip ------------------------------------------

def test_cli_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "s"))
    from aio.providers import Message

    agent = _FakeAgent()
    agent.messages = [Message(role="user", content="remember"),
                      Message(role="assistant", content="ok")]
    cli._save_cli_session(agent)

    fresh = _FakeAgent()
    n = cli._load_cli_session(fresh)
    assert n == 2 and len(fresh.messages) == 2 and fresh.messages[0].content == "remember"
