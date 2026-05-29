"""Tests for the web dashboard service layer (no socket binding)."""

from __future__ import annotations

from pathlib import Path

from aio.providers import AssistantTurn, ToolCall
from aio.web import AgentService, EventUI


def _service(tmp_path: Path, monkeypatch) -> AgentService:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from aio.config import load_config

    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic"})
    return AgentService(cfg)


class FakeProvider:
    """Reads a file then finishes."""

    def __init__(self):
        self.step = 0

    def chat(self, messages, tools=None, system=None):
        self.step += 1
        if self.step == 1:
            return AssistantTurn(
                content="reading",
                tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "f.txt"})],
            )
        return AssistantTurn(content="all done", tool_calls=[])


def test_event_ui_collects_events():
    ui = EventUI()
    ui.assistant("hi")
    ui.tool_call("read_file", {"path": "x"})
    ui.tool_result("contents")
    ui.show_diff("a", "b", "f.txt")
    types = [e["type"] for e in ui.drain()]
    assert types == ["assistant", "tool_call", "tool_result", "diff"]
    assert ui.events == []  # drained


def test_service_info(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    info = svc.info()
    assert info["provider"] == "anthropic"
    assert any(t["name"] == "read_file" for t in info["tools"])


def test_service_chat_collects_events(tmp_path, monkeypatch):
    (tmp_path / "f.txt").write_text("hello world")
    svc = _service(tmp_path, monkeypatch)
    svc.agent.provider = FakeProvider()  # swap in offline provider

    result = svc.chat("read f.txt")
    types = [e["type"] for e in result["events"]]
    assert "tool_call" in types
    assert "tool_result" in types
    assert result["final"] == "all done"
    # tool actually ran against the workdir
    tr = [e for e in result["events"] if e["type"] == "tool_result"][0]
    assert "hello world" in tr["text"]


def test_service_configure_switches_provider(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    info = svc.configure(provider="ollama", model="qwen2.5-coder")
    assert info["provider"] == "ollama"
    assert info["model"] == "qwen2.5-coder"


def test_exec_command(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    res = svc.exec_command("echo hello-from-terminal")
    assert res["exit_code"] == 0
    assert "hello-from-terminal" in res["output"]
    # workdir is respected
    (tmp_path / "marker.txt").write_text("x")
    listing = svc.exec_command("ls")
    assert "marker.txt" in listing["output"]


def test_exec_nonzero_exit(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    res = svc.exec_command("exit 3")
    assert res["exit_code"] == 3


def test_git_actions(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.exec_command(
        "git init -q && git config user.email a@b.c && git config user.name t "
        "&& git config commit.gpgsign false"
    )
    (tmp_path / "x.txt").write_text("content")
    status = svc.git_action("status")
    assert "x.txt" in status["output"]
    commit = svc.git_action("commit", message="add x")
    assert "add x" in commit["output"] or "master" in commit["output"]
    log = svc.git_action("log")
    assert "add x" in log["output"]


def test_static_server_lifecycle(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    assert svc.server_status()["running"] is False
    started = svc.server_start(port=0)  # port 0 -> OS picks a free port
    assert started["running"] is True
    stopped = svc.server_stop()
    assert stopped["running"] is False


def test_editor_tree_read_write(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("// ignored")
    svc = _service(tmp_path, monkeypatch)

    tree = svc.fs_tree()
    assert "src/app.py" in tree["files"]
    assert all("node_modules" not in f for f in tree["files"])  # ignored dirs excluded

    read = svc.fs_read("src/app.py")
    assert read["content"] == "x = 1\n"

    written = svc.fs_write("src/app.py", "x = 2\n")
    assert written["ok"] is True
    assert (tmp_path / "src" / "app.py").read_text() == "x = 2\n"
    # and new files can be created
    svc.fs_write("docs/new.md", "# hi")
    assert (tmp_path / "docs" / "new.md").read_text() == "# hi"


def test_editor_sandboxed(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    res = svc.fs_read("../../etc/passwd")
    assert "error" in res  # escaping the workdir is refused
