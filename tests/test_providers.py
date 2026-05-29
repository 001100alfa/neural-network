"""Tests for provider request building and response parsing (no network)."""

from __future__ import annotations

from aio.providers import Message, ToolCall
from aio.providers.anthropic import AnthropicProvider
from aio.providers.openai_compat import OllamaProvider, OpenAICompatProvider

TOOLS = [
    {
        "name": "read_file",
        "description": "read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
]


def _conversation():
    return [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="reading",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})],
        ),
        Message(role="tool", content="file contents", tool_call_id="c1", name="read_file"),
    ]


def test_anthropic_payload_shape():
    p = AnthropicProvider(model="claude-x", api_key="k")
    url, headers, body = p._build_payload(_conversation(), TOOLS, system="be good")
    assert url.endswith("/v1/messages")
    assert headers["x-api-key"] == "k"
    assert body["system"] == "be good"
    assert body["tools"][0]["input_schema"]["properties"]["path"]["type"] == "string"
    # tool_use block carried on the assistant message
    assistant = [m for m in body["messages"] if m["role"] == "assistant"][0]
    assert any(b["type"] == "tool_use" for b in assistant["content"])
    # tool result becomes a user message with tool_result block
    tool_msg = body["messages"][-1]
    assert tool_msg["role"] == "user"
    assert tool_msg["content"][0]["type"] == "tool_result"
    assert tool_msg["content"][0]["tool_use_id"] == "c1"


def test_anthropic_merges_parallel_tool_results():
    """Anthropic requires alternating roles: multiple tool results following one
    assistant turn must merge into a SINGLE user message."""
    convo = [
        Message(role="user", content="do two things"),
        Message(
            role="assistant",
            content="working",
            tool_calls=[
                ToolCall(id="a", name="read_file", arguments={"path": "x"}),
                ToolCall(id="b", name="read_file", arguments={"path": "y"}),
            ],
        ),
        Message(role="tool", content="content x", tool_call_id="a", name="read_file"),
        Message(role="tool", content="content y", tool_call_id="b", name="read_file"),
    ]
    p = AnthropicProvider(model="claude-x", api_key="k")
    _, _, body = p._build_payload(convo, TOOLS, system=None)
    roles = [m["role"] for m in body["messages"]]
    # roles must strictly alternate
    assert roles == ["user", "assistant", "user"], roles
    # the final user message carries BOTH tool_result blocks
    results = body["messages"][-1]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"]
    assert {b["tool_use_id"] for b in results} == {"a", "b"}


def test_anthropic_parse():
    p = AnthropicProvider(model="claude-x", api_key="k")
    data = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x"}},
        ],
        "usage": {"input_tokens": 5},
    }
    turn = p._parse_response(data)
    assert turn.content == "hello"
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].arguments == {"path": "x"}


def test_openai_payload_shape():
    p = OpenAICompatProvider(model="gpt-x", api_key="k")
    url, headers, body = p._build_payload(_conversation(), TOOLS, system="sys")
    assert url.endswith("/chat/completions")
    assert headers["authorization"] == "Bearer k"
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["tools"][0]["type"] == "function"
    # assistant tool_calls serialised with JSON string arguments
    assistant = [m for m in body["messages"] if m["role"] == "assistant"][0]
    assert assistant["tool_calls"][0]["function"]["name"] == "read_file"
    assert isinstance(assistant["tool_calls"][0]["function"]["arguments"], str)
    # tool result message
    tool_msg = [m for m in body["messages"] if m["role"] == "tool"][0]
    assert tool_msg["tool_call_id"] == "c1"


def test_openai_parse():
    p = OpenAICompatProvider(model="gpt-x", api_key="k")
    data = {
        "choices": [
            {
                "message": {
                    "content": "done",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {"name": "read_file", "arguments": '{"path": "y"}'},
                        }
                    ],
                }
            }
        ]
    }
    turn = p._parse_response(data)
    assert turn.content == "done"
    assert turn.tool_calls[0].arguments == {"path": "y"}


def test_anthropic_models_request_and_parse():
    p = AnthropicProvider(model="x", api_key="k")
    url, headers = p._models_request()
    assert url.endswith("/v1/models?limit=1000")
    assert headers["x-api-key"] == "k" and "anthropic-version" in headers
    ids = p._parse_models({"data": [{"id": "claude-a"}, {"id": "claude-b"}, {"x": 1}]})
    assert ids == ["claude-a", "claude-b"]


def test_openai_models_request_and_parse():
    p = OpenAICompatProvider(model="x", api_key="k", base_url="https://api.groq.com/openai/v1")
    url, headers = p._models_request()
    assert url == "https://api.groq.com/openai/v1/models"
    assert headers["authorization"] == "Bearer k"
    # ids come back sorted
    assert p._parse_models({"data": [{"id": "b"}, {"id": "a"}]}) == ["a", "b"]


def test_new_providers_use_openai_compatible_endpoints():
    """The 6 added global providers build as OpenAI-compatible with their URL."""
    from aio.config import load_config
    from aio.providers import build_provider
    from aio.providers.openai_compat import OpenAICompatProvider

    cfg = load_config()
    for name, host in [
        ("google", "generativelanguage.googleapis.com"),
        ("groq", "api.groq.com"),
        ("mistral", "api.mistral.ai"),
        ("deepseek", "api.deepseek.com"),
        ("xai", "api.x.ai"),
        ("together", "api.together.xyz"),
    ]:
        cfg.provider = name
        p = build_provider(cfg)
        assert isinstance(p, OpenAICompatProvider)
        url, _, _ = p._build_payload([Message(role="user", content="hi")], [], None)
        assert host in url and url.endswith("/chat/completions")


def test_image_attachments_encode_for_both_styles():
    img = {"media_type": "image/png", "data": "B64DATA"}
    convo = [Message(role="user", content="what is this?", images=[img])]

    _, _, abody = AnthropicProvider(model="m", api_key="k")._build_payload(convo, [], None)
    ablocks = abody["messages"][0]["content"]
    assert any(b.get("type") == "image" and b["source"]["data"] == "B64DATA" for b in ablocks)
    assert any(b.get("type") == "text" for b in ablocks)

    _, _, obody = OpenAICompatProvider(model="m", api_key="k")._build_payload(convo, [], None)
    oparts = obody["messages"][0]["content"]
    assert any(p.get("type") == "image_url" and "B64DATA" in p["image_url"]["url"] for p in oparts)


def test_ollama_default_base_url():
    p = OllamaProvider(model="qwen")
    url, headers, _ = p._build_payload([Message(role="user", content="hi")], [], None)
    assert "11434" in url
    # no api key -> no auth header
    assert "authorization" not in headers
