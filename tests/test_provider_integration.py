"""End-to-end provider integration tests over a REAL local HTTP server.

The other provider tests only call ``_build_payload`` / ``_parse_response``
directly — the actual send path (``chat`` -> ``_post`` -> ``_send`` -> urllib
-> socket, and ``stream_chat`` -> ``_stream_lines`` -> SSE parsing, plus the
429/Retry-After backoff) was never exercised. Here we stand up a tiny server
that speaks the genuine Anthropic and OpenAI wire formats, point the real
provider classes at it, and assert the whole round trip — including the agent
loop driving a real provider through a tool call. No API key, deterministic,
runs in CI.
"""

from __future__ import annotations

import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aio.agent import Agent
from aio.providers import Message, ProviderError
from aio.providers.anthropic import AnthropicProvider
from aio.providers.openai_compat import OpenAICompatProvider
from aio.tools import ToolContext, default_registry
from aio.web import EventUI

# -- response fixtures speaking the genuine wire formats --------------------

ANTHROPIC_TOOL_TURN = {
    "id": "msg_1", "type": "message", "role": "assistant",
    "content": [
        {"type": "text", "text": "Reading the file."},
        {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "a.py"}},
    ],
    "usage": {"input_tokens": 12, "output_tokens": 7},
}
ANTHROPIC_TEXT_TURN = {
    "id": "msg_2", "type": "message", "role": "assistant",
    "content": [{"type": "text", "text": "All done."}],
    "usage": {"input_tokens": 20, "output_tokens": 3},
}
ANTHROPIC_SSE = (
    'event: message_start\n'
    'data: {"type":"message_start","message":{"usage":{"input_tokens":9,"output_tokens":0}}}\n\n'
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hel"}}\n\n'
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"lo"}}\n\n'
    'data: {"type":"content_block_start","index":1,'
    '"content_block":{"type":"tool_use","id":"toolu_9","name":"read_file","input":{}}}\n\n'
    'data: {"type":"content_block_delta","index":1,'
    '"delta":{"type":"input_json_delta","partial_json":"{\\"path\\":"}}\n\n'
    'data: {"type":"content_block_delta","index":1,'
    '"delta":{"type":"input_json_delta","partial_json":"\\"a.py\\"}"}}\n\n'
    'data: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
)

OPENAI_TOOL_TURN = {
    "id": "chatcmpl-1", "choices": [{
        "message": {
            "role": "assistant", "content": "Reading the file.",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
            }],
        }, "finish_reason": "tool_calls"}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 7},
}
OPENAI_TEXT_TURN = {
    "id": "chatcmpl-2",
    "choices": [{"message": {"role": "assistant", "content": "All done."}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 3},
}
OPENAI_SSE = (
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_9",'
    '"function":{"name":"read_file","arguments":"{\\"path\\":"}}]}}]}\n\n'
    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
    '"function":{"arguments":"\\"a.py\\"}"}}]}}]}\n\n'
    'data: {"usage":{"prompt_tokens":9,"completion_tokens":7}}\n\n'
    'data: [DONE]\n\n'
)


class _MockHandler(BaseHTTPRequestHandler):
    """Serves canned Anthropic/OpenAI responses; records each request."""

    def log_message(self, *_a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _json(self, code, obj, extra_headers=None):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _sse(self, text):
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - model listing
        srv = self.server
        # store the raw (case-insensitive) header object for assertions
        srv.requests.append(("GET", self.path, self.headers, {}))
        if self.path.startswith("/v1/models") or self.path.endswith("/models"):
            if "anthropic" in srv.flavor:
                self._json(200, {"data": [{"id": "claude-x"}, {"id": "claude-y"}]})
            else:
                self._json(200, {"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        srv = self.server
        body = self._body()
        srv.requests.append(("POST", self.path, self.headers, body))

        # retry scenario: fail with 429 (+Retry-After) until enough attempts
        if srv.fail_times > 0:
            srv.fail_times -= 1
            self._json(429, {"error": {"message": "slow down"}}, {"Retry-After": "0"})
            return

        streaming = bool(body.get("stream"))
        srv.served += 1                       # only successful chat turns
        first = srv.served == 1
        if "anthropic" in srv.flavor:
            if streaming:
                self._sse(ANTHROPIC_SSE)
            else:
                self._json(200, ANTHROPIC_TOOL_TURN if first else ANTHROPIC_TEXT_TURN)
        else:
            if streaming:
                self._sse(OPENAI_SSE)
            else:
                self._json(200, OPENAI_TOOL_TURN if first else OPENAI_TEXT_TURN)


@contextlib.contextmanager
def mock_server(flavor: str, fail_times: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    srv.flavor = flavor
    srv.fail_times = fail_times
    srv.served = 0
    srv.requests = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        host, port = srv.server_address
        yield srv, f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()
        t.join(timeout=2)


TOOLS = [{"name": "read_file", "description": "read a file",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]


# -- Anthropic --------------------------------------------------------------

def test_anthropic_chat_roundtrip_over_http():
    with mock_server("anthropic") as (srv, base):
        p = AnthropicProvider(model="claude-x", api_key="sk-123", base_url=base, max_retries=0)
        turn = p.chat([Message(role="user", content="read a.py")], tools=TOOLS, system="be good")
    # response parsed from a real HTTP round trip
    assert turn.content == "Reading the file."
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    assert turn.usage["input_tokens"] == 12
    # request was serialised correctly onto the wire
    method, path, headers, body = srv.requests[0]
    assert path == "/v1/messages"
    assert headers["x-api-key"] == "sk-123"
    assert headers["anthropic-version"]
    assert body["model"] == "claude-x"
    assert body["tools"][0]["name"] == "read_file"


def test_anthropic_stream_over_http():
    deltas = []
    with mock_server("anthropic") as (srv, base):
        p = AnthropicProvider(model="claude-x", api_key="k", base_url=base)
        turn = p.stream_chat([Message(role="user", content="hi")], tools=TOOLS,
                             on_delta=deltas.append)
    assert deltas == ["Hel", "lo"]
    assert turn.content == "Hello"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "a.py"}   # reassembled from input_json_delta
    assert turn.usage["output_tokens"] == 7
    assert srv.requests[0][3]["stream"] is True


def test_anthropic_list_models_over_http():
    with mock_server("anthropic") as (srv, base):
        p = AnthropicProvider(model="claude-x", api_key="k", base_url=base)
        assert p.list_models() == ["claude-x", "claude-y"]


# -- OpenAI-compatible ------------------------------------------------------

def test_openai_chat_roundtrip_over_http():
    with mock_server("openai") as (srv, base):
        p = OpenAICompatProvider(model="gpt-x", api_key="sk-9", base_url=base + "/v1", max_retries=0)
        turn = p.chat([Message(role="user", content="read a.py")], tools=TOOLS, system="sys")
    assert turn.content == "Reading the file."
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    method, path, headers, body = srv.requests[0]
    assert path == "/v1/chat/completions"
    assert headers["authorization"] == "Bearer sk-9"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["function"]["name"] == "read_file"


def test_openai_stream_over_http():
    deltas = []
    with mock_server("openai") as (srv, base):
        p = OpenAICompatProvider(model="gpt-x", api_key="k", base_url=base + "/v1")
        turn = p.stream_chat([Message(role="user", content="hi")], tools=TOOLS,
                             on_delta=deltas.append)
    assert deltas == ["Hel", "lo"]
    assert turn.content == "Hello"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "a.py"}
    assert turn.usage["completion_tokens"] == 7


# -- retry / backoff (was entirely uncovered) -------------------------------

def test_retry_on_429_then_success(monkeypatch):
    # don't actually sleep during backoff
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with mock_server("anthropic", fail_times=2) as (srv, base):
        p = AnthropicProvider(model="claude-x", api_key="k", base_url=base, max_retries=3)
        turn = p.chat([Message(role="user", content="hi")], tools=TOOLS)
    # two 429s then a 200 -> three POSTs total, and we got the real answer
    posts = [r for r in srv.requests if r[0] == "POST"]
    assert len(posts) == 3
    assert turn.tool_calls[0].name == "read_file"


def test_retry_exhausted_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    with mock_server("anthropic", fail_times=99) as (srv, base):
        p = AnthropicProvider(model="claude-x", api_key="k", base_url=base, max_retries=2)
        with pytest.raises(ProviderError) as ei:
            p.chat([Message(role="user", content="hi")], tools=TOOLS)
    assert "HTTP 429" in str(ei.value)
    posts = [r for r in srv.requests if r[0] == "POST"]
    assert len(posts) == 3                    # initial attempt + 2 retries


def test_connection_error_retries_then_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    # nothing is listening on this port -> urllib raises URLError
    p = AnthropicProvider(model="claude-x", api_key="k",
                          base_url="http://127.0.0.1:9", max_retries=1, timeout=1.0)
    with pytest.raises(ProviderError) as ei:
        p.chat([Message(role="user", content="hi")], tools=TOOLS)
    assert "reachable" in str(ei.value).lower() or "failed" in str(ei.value).lower()


# -- the agent loop driving a REAL provider over HTTP -----------------------

def test_agent_loop_drives_real_provider_through_a_tool_call(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    with mock_server("anthropic") as (srv, base):
        provider = AnthropicProvider(model="claude-x", api_key="k", base_url=base, max_retries=0)
        ui = EventUI()
        ctx = ToolContext(workdir=tmp_path, ui=ui, auto_approve=True)
        agent = Agent(provider, default_registry(), ctx, ui, "sys", max_steps=5)
        final = agent.run("read a.py and report")
    # the model (served over HTTP) asked to read_file; the real tool ran; the
    # result was fed back and the model produced a final answer — full loop.
    assert final == "All done."
    used = [tc.name for m in agent.messages if m.role == "assistant" for tc in m.tool_calls]
    assert used == ["read_file"]
    tool_out = [m.content for m in agent.messages if m.role == "tool"]
    assert tool_out and "x = 1" in tool_out[0]
