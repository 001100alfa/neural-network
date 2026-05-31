"""Tests for the LLM evaluation harness (offline / reference-solver mode)."""

from __future__ import annotations

from aio.eval import (
    EvalCase,
    Scorecard,
    golden_cases,
    reference_agent_factory,
    run_case,
    run_suite,
)
from aio.providers import AssistantTurn, ToolCall


def test_golden_suite_passes_offline():
    cases = golden_cases()
    assert len(cases) >= 5                          # broadened beyond the first two
    card = run_suite(cases, reference_agent_factory, live=False)
    assert card.total == len(cases)
    assert card.passed == card.total, card.format()
    assert card.pass_rate == 1.0
    # results carry observability: tool calls actually happened
    for r in card.results:
        assert r.passed and r.tools and "run_shell" in r.tools


def test_navigation_case_uses_codebase_tools():
    cases = {c.name: c for c in golden_cases()}
    assert "navigate-then-fix" in cases
    card = run_suite([cases["navigate-then-fix"]], reference_agent_factory, live=False)
    r = card.results[0]
    assert r.passed and "find_symbol" in r.tools and "edit_file" in r.tools


def test_scorecard_format_reports_mode_and_counts():
    card = run_suite(golden_cases(), reference_agent_factory, live=False)
    text = card.format()
    assert "AIO eval" in text and f"{card.passed}/{card.total}" in text
    assert "offline" in text                       # honest about the mode
    assert all(("PASS  " + r.name) in text for r in card.results)


def test_failing_solver_is_reported_not_swallowed(tmp_path):
    # a "solver" that does nothing -> the bug remains -> the check must fail
    def noop_provider():
        class P:
            def chat(self, messages, tools=None, system=None):
                return AssistantTurn(content="I did nothing.", tool_calls=[])
        return P()

    case = golden_cases()[0]
    case = EvalCase(name=case.name, prompt=case.prompt, files=case.files,
                    check=case.check, reference=noop_provider)
    result = run_case(case, reference_agent_factory)
    assert result.passed is False
    assert result.error  # failure detail surfaced


def test_run_case_records_real_edits(tmp_path):
    """A solver that edits the file but never verifies still passes the check
    because the harness independently runs the tests."""
    def edit_only():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="fix", tool_calls=[ToolCall(
                        "1", "edit_file", {"path": "m.py",
                        "old_string": "return n / 0", "new_string": "return n / 2"})])
                return AssistantTurn(content="done", tool_calls=[])
        return P()

    base = golden_cases()[0]
    case = EvalCase(name="edit-only", prompt=base.prompt, files=base.files,
                    check=base.check, reference=edit_only)
    result = run_case(case, reference_agent_factory)
    assert result.passed is True            # the independent pytest check confirms it
    assert "edit_file" in result.tools


def test_scorecard_dataclass_math():
    results = [
        type("R", (), {"passed": True, "name": "a", "tools": [], "tokens": 0,
                       "elapsed_s": 0.0, "error": None, "detail": ""})(),
        type("R", (), {"passed": False, "name": "b", "tools": [], "tokens": 0,
                       "elapsed_s": 0.0, "error": "x", "detail": "x"})(),
    ]
    card = Scorecard(results, live=True)  # type: ignore[arg-type]
    assert card.total == 2 and card.passed == 1 and card.pass_rate == 0.5
    assert "LIVE model" in card.format()
