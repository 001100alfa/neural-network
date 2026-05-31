"""Test concurrent request handling across conversations (#5)."""

from __future__ import annotations

import threading
import time

from aio.providers import AssistantTurn


def _service(tmp_path, monkeypatch):
    from tests.test_web import _service as svc
    return svc(tmp_path, monkeypatch)


def test_two_conversations_run_concurrently(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)

    class SlowEcho:
        def chat(self, messages, tools=None, system=None):
            time.sleep(0.4)
            return AssistantTurn(content="ok", usage={"input_tokens": 1, "output_tokens": 1})

    svc.agent.provider = SlowEcho()  # propagates to lazily-built conv agents

    results = {}

    def run(cid):
        results[cid] = svc.chat("hi", conv_id=cid)["final"]

    t0 = time.time()
    threads = [threading.Thread(target=run, args=(c,)) for c in ("a", "b", "c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0

    assert results == {"a": "ok", "b": "ok", "c": "ok"}
    # three 0.4s turns in distinct conversations overlap -> well under 1.2s
    assert elapsed < 1.0


def test_same_conversation_is_serialized(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    order = []

    class Tracking:
        def chat(self, messages, tools=None, system=None):
            order.append("start")
            time.sleep(0.2)
            order.append("end")
            return AssistantTurn(content="ok")

    svc.agent.provider = Tracking()
    threads = [threading.Thread(target=lambda: svc.chat("x", conv_id="same")) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # same conversation: the two turns must not interleave (start,end,start,end)
    assert order == ["start", "end", "start", "end"]
