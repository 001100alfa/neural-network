"""Benchmark runner: produce, persist and compare AIO eval scorecards.

The eval harness (:mod:`aio.eval`) runs the agent on golden tasks and scores the
outcome. This wraps it so a real-model run is reproducible and *comparable over
time*: it writes a timestamped scorecard, can diff a new run against a saved
baseline (regressions/improvements per case), and prints a one-line summary
suitable for pasting into BENCHMARKS.md.

    # record a baseline against a real model
    AIO_EVAL_LIVE=1 ANTHROPIC_API_KEY=sk-... python -m aio.benchmark --save baseline.json

    # later, compare a fresh run to it
    AIO_EVAL_LIVE=1 ANTHROPIC_API_KEY=sk-... python -m aio.benchmark --compare baseline.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .eval import golden_cases, live_agent_factory, reference_agent_factory, run_suite


def run(live: bool) -> dict[str, Any]:
    factory = live_agent_factory if live else reference_agent_factory
    card = run_suite(golden_cases(), factory, live=live)
    return card.to_dict()


def _case_map(scorecard: dict[str, Any]) -> dict[str, bool]:
    return {c["name"]: bool(c["passed"]) for c in scorecard.get("cases", [])}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Diff two scorecards: per-case regressions / fixes and pass-rate delta."""
    base, cur = _case_map(baseline), _case_map(current)
    names = sorted(set(base) | set(cur))
    added = [n for n in names if n in cur and n not in base]
    removed = [n for n in names if n in base and n not in cur]
    # regressed/fixed only cover cases present in BOTH runs whose status flipped;
    # brand-new or dropped cases are reported separately (added/removed) so a new
    # failing case isn't mistaken for a regression.
    shared = [n for n in names if n in base and n in cur]
    regressed = [n for n in shared if base[n] and not cur[n]]
    fixed = [n for n in shared if cur[n] and not base[n]]
    return {
        "baseline_pass_rate": baseline.get("pass_rate"),
        "current_pass_rate": current.get("pass_rate"),
        "delta": round((current.get("pass_rate", 0.0) - baseline.get("pass_rate", 0.0)), 4),
        "regressed": regressed,
        "fixed": fixed,
        "added": added,
        "removed": removed,
        "ok": not regressed,   # a regression is a failure for CI purposes
    }


def summary_line(scorecard: dict[str, Any]) -> str:
    mode = scorecard.get("mode", "?")
    return (f"{scorecard.get('passed')}/{scorecard.get('total')} passed "
            f"(pass_rate {scorecard.get('pass_rate')}) — {mode} mode")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="aio-benchmark",
                                 description="Run/record/compare AIO eval scorecards.")
    ap.add_argument("--save", metavar="PATH", help="Write the scorecard JSON here.")
    ap.add_argument("--compare", metavar="BASELINE", help="Compare this run to a saved scorecard.")
    args = ap.parse_args(argv)

    live = os.environ.get("AIO_EVAL_LIVE") == "1"
    card = run(live)
    print(summary_line(card))
    if not live:
        print("NOTE: offline reference mode — proves the harness, NOT model quality. "
              "Set AIO_EVAL_LIVE=1 + a provider key for a real number.")

    if args.save:
        Path(args.save).write_text(json.dumps(card, indent=2), encoding="utf-8")
        print(f"scorecard written to {args.save}")

    if args.compare:
        baseline = json.loads(Path(args.compare).read_text(encoding="utf-8"))
        diff = compare(baseline, card)
        print(f"\nvs {args.compare}: delta {diff['delta']:+.4f}")
        if diff["regressed"]:
            print(f"  REGRESSED: {', '.join(diff['regressed'])}")
        if diff["fixed"]:
            print(f"  fixed:     {', '.join(diff['fixed'])}")
        if diff["added"]:
            print(f"  new cases: {', '.join(diff['added'])}")
        return 0 if diff["ok"] else 1

    return 0 if card["pass_rate"] == 1.0 else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
