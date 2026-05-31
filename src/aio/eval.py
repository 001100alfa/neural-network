"""LLM evaluation harness — measure the agent's *outcomes*, not just mechanics.

The rest of the test suite checks that the loop drives tools correctly. This
measures whether the agent actually SOLVES realistic coding tasks: each case
lays down a workspace, hands the agent a prompt, lets it work with the real
tools, then checks the result (usually by running the project's own tests).

Run it against a real model to gauge quality:

    AIO_EVAL_LIVE=1 ANTHROPIC_API_KEY=... python -m aio.eval

Without ``AIO_EVAL_LIVE`` the same cases run with each case's deterministic
*reference solver* — that proves the harness, the tool loop and the checks
work, but NOT model quality (be honest: only a live run measures reasoning).
The scorecard reports which mode produced it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent import Agent
from .providers import Provider
from .tools import ToolContext, default_registry

CheckFn = Callable[[Path], "tuple[bool, str]"]
ProviderFactory = Callable[[], Provider]


@dataclass
class EvalCase:
    """One golden task: a workspace fixture, a prompt, and an outcome check."""

    name: str
    prompt: str
    files: dict[str, str]
    check: CheckFn
    #: deterministic provider that solves THIS case (for CI / offline runs)
    reference: ProviderFactory | None = None


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    tools: list[str] = field(default_factory=list)
    tokens: int = 0
    elapsed_s: float = 0.0
    error: str | None = None


@dataclass
class Scorecard:
    results: list[CaseResult]
    live: bool

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.results else 0.0

    def format(self) -> str:
        mode = "LIVE model" if self.live else "reference solvers (offline; not a quality measure)"
        lines = [f"AIO eval — {self.passed}/{self.total} passed  ({mode})", ""]
        for r in self.results:
            mark = "PASS" if r.passed else "FAIL"
            extra = f"  [{r.elapsed_s:.1f}s, {len(r.tools)} tool calls"
            extra += f", {r.tokens} tok]" if r.tokens else "]"
            lines.append(f"  {mark}  {r.name}{extra}")
            if not r.passed:
                lines.append(f"        {r.error or r.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Machine-readable scorecard (for committing benchmark results)."""
        return {
            "mode": "live" if self.live else "reference",
            "passed": self.passed, "total": self.total,
            "pass_rate": round(self.pass_rate, 4),
            "cases": [
                {"name": r.name, "passed": r.passed, "tools": r.tools,
                 "tokens": r.tokens, "elapsed_s": round(r.elapsed_s, 3),
                 "error": r.error}
                for r in self.results
            ],
        }


# -- runner -----------------------------------------------------------------

def run_case(case: EvalCase, make_agent: Callable[[EvalCase, Path], Agent]) -> CaseResult:
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp)
        for rel, content in case.files.items():
            p = wd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        agent = make_agent(case, wd)
        t0 = time.time()
        try:
            agent.run(case.prompt)
        except Exception as exc:  # pragma: no cover - depends on provider
            return CaseResult(case.name, False, "agent raised", error=f"{type(exc).__name__}: {exc}",
                              elapsed_s=time.time() - t0)
        elapsed = time.time() - t0
        try:
            passed, detail = case.check(wd)
        except Exception as exc:  # pragma: no cover - defensive
            passed, detail = False, f"check raised: {exc}"
        tools = [tc.name for m in agent.messages if m.role == "assistant" for tc in m.tool_calls]
        usage = agent.run_usage or {}
        tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        return CaseResult(case.name, passed, detail, tools=tools, tokens=tokens, elapsed_s=elapsed,
                          error=None if passed else detail)


def run_suite(cases: list[EvalCase], make_agent: Callable[[EvalCase, Path], Agent],
              live: bool) -> Scorecard:
    return Scorecard([run_case(c, make_agent) for c in cases], live=live)


# -- agent factories --------------------------------------------------------

def build_eval_agent(provider: Provider, workdir: Path, max_steps: int = 12) -> Agent:
    from .service import EventUI

    ui: Any = EventUI()  # records events instead of printing — keeps the scorecard clean
    ctx = ToolContext(workdir=workdir, ui=ui, auto_approve=True)
    return Agent(provider, default_registry(), ctx, ui,
                 "You are a coding agent. Use the tools to complete the task, then stop.",
                 max_steps=max_steps)


def live_agent_factory(case: EvalCase, workdir: Path) -> Agent:  # pragma: no cover - needs a key
    from .config import load_config
    from .providers import build_provider

    cfg = load_config(workdir=workdir)
    return build_eval_agent(build_provider(cfg), workdir)


def reference_agent_factory(case: EvalCase, workdir: Path) -> Agent:
    if case.reference is None:
        raise ValueError(f"case {case.name!r} has no reference solver for offline eval")
    return build_eval_agent(case.reference(), workdir)


# -- checks -----------------------------------------------------------------

def pytest_passes(workdir: Path) -> tuple[bool, str]:
    """Run the workspace's own tests; pass iff they all do."""
    proc = subprocess.run(
        ["python3", "-m", "pytest", "-q"], cwd=workdir,
        capture_output=True, text=True, timeout=120,
    )
    ok = proc.returncode == 0
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return ok, (tail[-1] if tail else f"exit {proc.returncode}")


# -- golden suite -----------------------------------------------------------

def golden_cases() -> list[EvalCase]:
    from .providers import AssistantTurn, ToolCall

    # Each reference solver scripts the exact tool sequence a competent model
    # would use; the LIVE model gets the same prompt with no script.
    def fix_div_zero_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Fixing.", tool_calls=[ToolCall(
                        "1", "edit_file", {"path": "m.py",
                        "old_string": "return n / 0", "new_string": "return n / 2"})])
                if self.n == 2:
                    return AssistantTurn(content="Verifying.", tool_calls=[ToolCall(
                        "2", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Done.", tool_calls=[])
        return P()

    def add_multiply_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Adding multiply.", tool_calls=[ToolCall(
                        "1", "edit_file", {"path": "calc.py",
                        "old_string": "def subtract(a, b):\n    return a - b",
                        "new_string": "def subtract(a, b):\n    return a - b\n\n"
                                      "def multiply(a, b):\n    return a * b"})])
                if self.n == 2:
                    return AssistantTurn(content="Verifying.", tool_calls=[ToolCall(
                        "2", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Done.", tool_calls=[])
        return P()

    def fix_logic_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Off-by-one in the average.", tool_calls=[ToolCall(
                        "1", "edit_file", {"path": "stats.py",
                        "old_string": "sum(xs) / (len(xs) + 1)", "new_string": "sum(xs) / len(xs)"})])
                if self.n == 2:
                    return AssistantTurn(content="Verifying.", tool_calls=[ToolCall(
                        "2", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Done.", tool_calls=[])
        return P()

    def cross_file_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Implementing slugify.", tool_calls=[ToolCall(
                        "1", "write_file", {"path": "strutil.py",
                        "content": "def slugify(s):\n    return s.lower().replace(' ', '-')\n"})])
                if self.n == 2:
                    return AssistantTurn(content="Verifying.", tool_calls=[ToolCall(
                        "2", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Done.", tool_calls=[])
        return P()

    def navigate_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Locating compute().", tool_calls=[ToolCall(
                        "1", "find_symbol", {"name": "compute"})])
                if self.n == 2:
                    return AssistantTurn(content="Reading it.", tool_calls=[ToolCall(
                        "2", "read_file", {"path": "m.py", "symbol": "compute"})])
                if self.n == 3:
                    return AssistantTurn(content="Fixing the operator.", tool_calls=[ToolCall(
                        "3", "edit_file", {"path": "m.py",
                        "old_string": "return n + 2", "new_string": "return n * 2"})])
                if self.n == 4:
                    return AssistantTurn(content="Verifying.", tool_calls=[ToolCall(
                        "4", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Done.", tool_calls=[])
        return P()

    def two_bugs_ref():
        class P:
            def __init__(self): self.n = 0
            def chat(self, messages, tools=None, system=None):
                self.n += 1
                if self.n == 1:
                    return AssistantTurn(content="Fix mathutil.", tool_calls=[ToolCall(
                        "1", "edit_file", {"path": "mathutil.py",
                        "old_string": "return a - b", "new_string": "return a + b"})])
                if self.n == 2:
                    return AssistantTurn(content="Fix strutil.", tool_calls=[ToolCall(
                        "2", "edit_file", {"path": "strutil.py",
                        "old_string": "return s", "new_string": "return s.upper()"})])
                if self.n == 3:
                    return AssistantTurn(content="Verify.", tool_calls=[ToolCall(
                        "3", "run_shell", {"command": "python3 -m pytest -q"})])
                return AssistantTurn(content="Both fixed.", tool_calls=[])
        return P()

    return [
        EvalCase(
            name="fix-divide-by-zero",
            prompt="The test suite fails. Find and fix the bug so the tests pass.",
            files={
                "m.py": "def half(n):\n    return n / 0\n",
                "test_m.py": "from m import half\n\ndef test_half():\n    assert half(10) == 5\n",
            },
            check=pytest_passes,
            reference=fix_div_zero_ref,
        ),
        EvalCase(
            name="implement-missing-function",
            prompt="multiply() is missing from calc.py; implement it so the tests pass.",
            files={
                "calc.py": "def add(a, b):\n    return a + b\n\ndef subtract(a, b):\n    return a - b\n",
                "test_calc.py": "from calc import add, subtract, multiply\n\n"
                                "def test_mul():\n    assert multiply(4, 3) == 12\n",
            },
            check=pytest_passes,
            reference=add_multiply_ref,
        ),
        EvalCase(
            name="fix-logic-bug",
            prompt="average() returns the wrong number; fix the logic so the tests pass.",
            files={
                "stats.py": "def average(xs):\n    return sum(xs) / (len(xs) + 1)\n",
                "test_stats.py": "from stats import average\n\n"
                                 "def test_avg():\n    assert average([2, 4, 6]) == 4\n",
            },
            check=pytest_passes,
            reference=fix_logic_ref,
        ),
        EvalCase(
            name="implement-across-two-files",
            prompt="strutil.slugify is imported by app.py but doesn't exist; implement it.",
            files={
                "strutil.py": "",
                "app.py": "from strutil import slugify\n\n"
                          "def title_to_slug(t):\n    return slugify(t)\n",
                "test_app.py": "from app import title_to_slug\n\n"
                               "def test_slug():\n    assert title_to_slug('Hello World') == 'hello-world'\n",
            },
            check=pytest_passes,
            reference=cross_file_ref,
        ),
        EvalCase(
            name="navigate-then-fix",
            prompt="compute() has a bug. Locate it, read it, fix it, and verify.",
            files={
                "m.py": "def compute(n):\n    return n + 2\n\n"
                        "def helper():\n    return compute(0)\n",
                "test_m.py": "from m import compute\n\n"
                             "def test_compute():\n    assert compute(5) == 10\n",
            },
            check=pytest_passes,
            reference=navigate_ref,
        ),
        EvalCase(
            name="fix-two-bugs-two-files",
            prompt="Two functions are wrong (one per file). Fix both so all tests pass.",
            files={
                "mathutil.py": "def add(a, b):\n    return a - b\n",
                "strutil.py": "def shout(s):\n    return s\n",
                "test_both.py": "from mathutil import add\nfrom strutil import shout\n\n"
                                "def test_add():\n    assert add(2, 3) == 5\n\n"
                                "def test_shout():\n    assert shout('hi') == 'HI'\n",
            },
            check=pytest_passes,
            reference=two_bugs_ref,
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(prog="aio-eval", description="Run the AIO eval suite.")
    ap.add_argument("--json", metavar="PATH", help="Write the machine-readable scorecard here.")
    args = ap.parse_args(argv)

    live = os.environ.get("AIO_EVAL_LIVE") == "1"
    cases = golden_cases()
    factory = live_agent_factory if live else reference_agent_factory
    card = run_suite(cases, factory, live=live)
    print(card.format())
    if args.json:
        Path(args.json).write_text(_json.dumps(card.to_dict(), indent=2), encoding="utf-8")
        print(f"\nscorecard written to {args.json}")
    if not live:
        print("\nNOTE: live evaluation skipped. Set AIO_EVAL_LIVE=1 and a provider "
              "API key to measure a real model's reasoning.")
    return 0 if card.pass_rate == 1.0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
