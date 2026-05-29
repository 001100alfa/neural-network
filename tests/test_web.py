"""Tests for the web dashboard service layer (no socket binding)."""

from __future__ import annotations

from pathlib import Path

from aio.providers import AssistantTurn, Message, ToolCall
from aio.web import AgentService, EventUI


def _service(tmp_path: Path, monkeypatch) -> AgentService:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # Isolate the key store and sessions dir so tests never touch ~/.config.
    monkeypatch.setenv("AIO_KEYS_FILE", str(tmp_path / "keys.json"))
    monkeypatch.setenv("AIO_SESSIONS_DIR", str(tmp_path / "sessions"))
    from aio.config import load_config

    cfg = load_config(workdir=tmp_path, overrides={"provider": "anthropic"})
    return AgentService(cfg)


class _Tok:
    """Streaming fake provider that yields tokens then a final turn."""

    def stream_chat(self, messages, tools=None, system=None, on_delta=None):
        for t in ["Hel", "lo ", "world"]:
            on_delta(t)
        return AssistantTurn(content="Hello world", usage={"input_tokens": 3, "output_tokens": 2})


def test_chat_stream_emits_tokens_not_full_text(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.agent.provider = _Tok()  # web agent has stream=True
    events = []
    svc.chat_stream("hi", events.append)
    toks = [e["text"] for e in events if e["type"] == "token"]
    assert toks == ["Hel", "lo ", "world"]
    assert not any(e["type"] == "assistant" for e in events)  # no duplicate full bubble
    assert events[-1]["type"] == "done"


def test_sessions_save_list_load_delete(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.agent.messages = [
        Message(role="user", content="hello there"),
        Message(role="assistant", content="hi!"),
    ]
    saved = svc.save_session()
    assert saved["ok"] and saved["count"] == 2 and "hello there" in saved["title"]

    assert any(s["id"] == saved["id"] for s in svc.list_sessions()["sessions"])

    svc.agent.messages = []  # wipe, then restore from disk
    loaded = svc.load_session(saved["id"])
    assert loaded["ok"] and len(loaded["messages"]) == 2
    assert len(svc.agent.messages) == 2 and svc.agent.messages[0].content == "hello there"

    svc.delete_session(saved["id"])
    assert all(s["id"] != saved["id"] for s in svc.list_sessions()["sessions"])


def test_monthly_budget_warning(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)  # active anthropic, sonnet (priced)
    svc.set_provider_key("anthropic", budget=0.001)  # tiny monthly cap

    class BigUsage:
        def chat(self, messages, tools=None, system=None):
            return AssistantTurn(content="x", usage={"input_tokens": 100_000, "output_tokens": 100_000})

    svc.agent.provider = BigUsage()
    out = svc.chat("hi")  # ~ $1.80 spend >> $0.001 budget
    assert out["usage"]["budget_warning"]
    a = next(p for p in svc.providers_info()["providers"] if p["name"] == "anthropic")
    assert a["budget_usd"] == 0.001 and a["over_budget"] is True and a["month_spent_usd"] > 0


def test_import_message_tool_call():
    # the Message import is wired (used by sessions)
    assert Message and ToolCall


def test_providers_panel_lists_ten(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    info = svc.providers_info()
    assert len(info["providers"]) == 10
    names = {p["name"] for p in info["providers"]}
    assert {"anthropic", "openai", "google", "groq", "mistral",
            "deepseek", "xai", "together", "openrouter", "ollama"} == names
    # ollama needs no key and is always "configured"
    ollama = next(p for p in info["providers"] if p["name"] == "ollama")
    assert ollama["needs_key"] is False and ollama["configured"] is True


def test_set_provider_key_persists_masks_and_activates(tmp_path, monkeypatch):
    import json

    svc = _service(tmp_path, monkeypatch)
    info = svc.set_provider_key("groq", api_key="gsk_secret_ABCD1234", make_active=True)

    assert info["active"] == "groq"
    groq = next(p for p in info["providers"] if p["name"] == "groq")
    assert groq["active"] is True and groq["configured"] is True
    assert groq["key_masked"].endswith("1234")
    # the raw key must never appear in the API payload
    assert "gsk_secret_ABCD1234" not in json.dumps(info)
    # but it IS persisted to the (isolated) key store on disk
    saved = json.loads((tmp_path / "keys.json").read_text())
    assert saved["providers"]["groq"]["api_key"] == "gsk_secret_ABCD1234"
    assert saved["active"] == "groq"


def test_usage_tracking_after_chat(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)  # active=anthropic, model claude-sonnet-4-6 (priced)

    class UsageProvider:
        def chat(self, messages, tools=None, system=None):
            return AssistantTurn(
                content="hello", tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
            )

    svc.agent.provider = UsageProvider()
    out = svc.chat("hi")
    u = out["usage"]
    assert u["requests"] == 1
    assert u["input_tokens"] == 100 and u["output_tokens"] == 50
    assert u["est_cost_usd"] > 0  # sonnet is in the price table
    assert u["by_provider"]["anthropic"]["input_tokens"] == 100


def test_test_provider_ok_and_error(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)

    class OkProv:
        def list_models(self):
            return ["m-a", "m-b", "m-c"]

    class BadProv:
        def list_models(self):
            from aio.providers import ProviderError
            raise ProviderError("invalid api key")

    monkeypatch.setattr("aio.providers.build_provider", lambda cfg: OkProv())
    ok = svc.test_provider("groq")
    assert ok["ok"] is True and ok["count"] == 3 and "m-a" in ok["models"]

    monkeypatch.setattr("aio.providers.build_provider", lambda cfg: BadProv())
    bad = svc.test_provider("groq")
    assert bad["ok"] is False and "invalid api key" in bad["error"]
    # the active provider is restored after a test
    assert svc.config.provider == "anthropic"


def test_keys_survive_reload(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    svc.set_provider_key("deepseek", api_key="ds_key_9999", model="deepseek-reasoner", make_active=True)
    # a fresh service (new process simulation) should pick the key + active up
    from aio.config import load_config

    cfg2 = load_config(workdir=tmp_path)
    svc2 = AgentService(cfg2)
    assert svc2.config.provider == "deepseek"
    assert svc2.config.providers["deepseek"].api_key == "ds_key_9999"
    assert svc2.config.providers["deepseek"].model == "deepseek-reasoner"


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


def test_service_chat_stream_emits_live(tmp_path, monkeypatch):
    (tmp_path / "f.txt").write_text("hello world")
    svc = _service(tmp_path, monkeypatch)
    svc.agent.provider = FakeProvider()  # read_file then finish

    events = []
    svc.chat_stream("read f.txt", events.append)

    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    # the stream is terminated by a single 'done' event carrying the final text
    assert types[-1] == "done"
    assert events[-1]["final"] == "all done"


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
