"""Tests for dependency-free OpenTelemetry-style telemetry (#8)."""

from __future__ import annotations

import json

from aio.telemetry import Telemetry


def test_disabled_by_default_no_file(tmp_path):
    t = Telemetry()
    span = t.record_turn(provider="anthropic", model="m", usage={}, duration_s=0.1)
    assert span == {}  # disabled -> nothing emitted


def test_jsonl_span_has_genai_attributes(tmp_path):
    log = tmp_path / "otel.jsonl"
    t = Telemetry(enabled=True, file=str(log))
    t.record_turn(
        provider="anthropic", model="claude-sonnet-4-6",
        usage={"input_tokens": 120, "output_tokens": 30, "cache_read": 100,
               "cache_write": 20, "requests": 2},
        duration_s=0.5, ok=True,
    )
    rows = log.read_text().strip().splitlines()
    assert len(rows) == 1
    span = json.loads(rows[0])
    a = span["attributes"]
    assert a["gen_ai.system"] == "anthropic"
    assert a["gen_ai.request.model"] == "claude-sonnet-4-6"
    assert a["gen_ai.usage.input_tokens"] == 120
    assert a["gen_ai.usage.output_tokens"] == 30
    assert a["aio.usage.cache_read_tokens"] == 100
    assert span["status"]["code"] == "OK"
    assert span["end_time_unix_nano"] > span["start_time_unix_nano"]


def test_otlp_payload_shape():
    t = Telemetry(enabled=True, service_name="aio")
    span = {
        "name": "chat m", "start_time_unix_nano": 1, "end_time_unix_nano": 2,
        "status": {"code": "OK"},
        "attributes": {"gen_ai.system": "openai", "gen_ai.usage.input_tokens": 5},
    }
    p = t._otlp_payload(span)
    rs = p["resourceSpans"][0]
    assert rs["resource"]["attributes"][0]["value"]["stringValue"] == "aio"
    sp = rs["scopeSpans"][0]["spans"][0]
    assert sp["name"] == "chat m"
    keys = {a["key"] for a in sp["attributes"]}
    assert "gen_ai.system" in keys and "gen_ai.usage.input_tokens" in keys
    # int attribute encoded as intValue string
    intattr = next(a for a in sp["attributes"] if a["key"] == "gen_ai.usage.input_tokens")
    assert intattr["value"] == {"intValue": "5"}


def test_from_config():
    t = Telemetry.from_config({"enabled": True, "file": "x.jsonl",
                               "otlp_endpoint": "http://h/v1/traces"})
    assert t.enabled and t.file == "x.jsonl" and t.otlp_endpoint.endswith("/v1/traces")


def test_service_records_turn(tmp_path, monkeypatch):
    from tests.test_web import _service
    from aio.providers import AssistantTurn

    log = tmp_path / "svc-otel.jsonl"
    svc = _service(tmp_path, monkeypatch)
    svc.telemetry = Telemetry(enabled=True, file=str(log))

    class P:
        def chat(self, messages, tools=None, system=None):
            return AssistantTurn(content="ok", usage={"input_tokens": 7, "output_tokens": 3})

    svc.agent.provider = P()
    svc.chat("hi", conv_id="t1")
    span = json.loads(log.read_text().strip().splitlines()[-1])
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 7
