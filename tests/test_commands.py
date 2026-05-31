"""Tests for custom slash commands, @file mentions, and CLI resume."""

from __future__ import annotations

from aio.commands import expand_command, expand_mentions, list_commands


def test_custom_command_listed_and_expanded(tmp_path):
    cmds = tmp_path / ".aio" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "review.md").write_text("Please review $ARGUMENTS and report issues.")
    assert "review" in list_commands(tmp_path)
    out = expand_command(tmp_path, "review", "src/app.py")
    assert out == "Please review src/app.py and report issues."
    # positional args
    (cmds / "greet.md").write_text("Hi $1 from $2")
    assert expand_command(tmp_path, "greet", "alice bob") == "Hi alice from bob"
    # unknown command
    assert expand_command(tmp_path, "nope", "") is None


def test_mention_expands_file(tmp_path):
    (tmp_path / "a.txt").write_text("FILE BODY")
    out = expand_mentions(tmp_path, "look at @a.txt please")
    assert "FILE BODY" in out and "contents of a.txt" in out
    # missing file is left untouched (no crash)
    out2 = expand_mentions(tmp_path, "see @missing.txt")
    assert "missing.txt" in out2 and "contents of" not in out2


def test_mention_sandboxed(tmp_path):
    out = expand_mentions(tmp_path, "@../secret.txt", allow_outside=False)
    assert "contents of" not in out  # escape refused


def test_web_preprocess_command_and_mention(tmp_path, monkeypatch):
    from tests.test_web import _service

    cmds = tmp_path / ".aio" / "commands"
    cmds.mkdir(parents=True)
    (cmds / "sum.md").write_text("Summarise $ARGUMENTS")
    (tmp_path / "f.txt").write_text("hello body")
    svc = _service(tmp_path, monkeypatch)
    assert "sum" in svc.list_commands()["commands"]
    assert svc._preprocess("/sum the repo") == "Summarise the repo"
    assert "hello body" in svc._preprocess("read @f.txt")


def test_cli_session_save_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sess"))
    from aio.cli import _load_cli_session, _save_cli_session
    from aio.providers import Message

    class FakeAgent:
        def __init__(self): self.messages = []

    a = FakeAgent()
    a.messages = [Message(role="user", content="remember this"),
                  Message(role="assistant", content="ok")]
    _save_cli_session(a)
    b = FakeAgent()
    n = _load_cli_session(b)
    assert n == 2 and b.messages[0].content == "remember this"
