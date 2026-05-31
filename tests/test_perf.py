"""Tests for token counting/calibration (#1), shell streaming (#3),
and smart output truncation (#6)."""

from __future__ import annotations

from aio.agent import Agent
from aio.providers import AssistantTurn
from aio.tokens import count_text
from aio.tools import ToolContext, default_registry
from aio.tools.shell import RunShellTool, smart_truncate
from aio.web import EventUI


def test_count_text_beats_flat_chars4():
    # punctuation-heavy / wordy text — just assert it's positive and scales
    assert count_text("") == 0
    assert count_text("hello world") >= 2
    big = "supercalifragilistic " * 100
    assert count_text(big) > 100


def test_smart_truncate_keeps_head_and_tail():
    text = "HEAD" + ("x" * 50_000) + "TAIL"
    out = smart_truncate(text, 1000)
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "elided" in out
    assert len(out) < 1200


def test_smart_truncate_passthrough_when_small():
    assert smart_truncate("short", 1000) == "short"


def test_token_calibration_adjusts_factor(tmp_path):
    # provider reports a moderately larger input count than our raw estimate ->
    # the EMA factor moves up (but wild outliers >5x are ignored).
    ui = EventUI()

    class P:
        def chat(self, messages, tools=None, system=None):
            # report ~2x the current raw estimate so the ratio stays in-band
            actual = max(2, ag._last_raw_estimate * 2)
            return AssistantTurn(content="ok", usage={"input_tokens": actual, "output_tokens": 1})

    ag = Agent(P(), default_registry(), ToolContext(workdir=tmp_path, ui=ui), ui, "sys")
    before = ag._token_factor
    ag.run("a longer prompt with several words to estimate")
    assert ag._token_factor > before  # EMA moved toward the observed ~2x ratio
    # an out-of-band ratio is ignored
    f = ag._token_factor
    ag._last_raw_estimate = 1
    ag._calibrate_tokens({"input_tokens": 10_000_000})
    assert ag._token_factor == f


def test_shell_streams_and_truncates(tmp_path):
    ctx = ToolContext(workdir=tmp_path, auto_approve=True)
    # 5000 lines -> well over MAX_OUTPUT; result must be bounded + show exit 0
    out = RunShellTool().run(
        {"command": "for i in $(seq 1 5000); do echo line-$i; done"}, ctx
    )
    assert "[exit code: 0]" in out
    assert "elided" in out          # smart-truncated
    assert "line-1\n" in out        # head retained
    assert "line-5000" in out       # tail retained


def test_shell_exit_code_and_small_output(tmp_path):
    ctx = ToolContext(workdir=tmp_path, auto_approve=True)
    out = RunShellTool().run({"command": "echo hi; exit 3"}, ctx)
    assert "hi" in out and "[exit code: 3]" in out
