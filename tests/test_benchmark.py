"""Tests for the benchmark runner (offline; the live path needs an API key)."""

from __future__ import annotations

import json

from aio.benchmark import compare, run, summary_line


def test_run_offline_produces_scorecard():
    card = run(live=False)
    assert card["mode"] == "reference"
    assert card["total"] >= 6 and card["passed"] == card["total"]
    assert card["pass_rate"] == 1.0
    assert all("name" in c and "passed" in c for c in card["cases"])


def test_summary_line():
    card = {"mode": "live", "passed": 5, "total": 6, "pass_rate": 0.8333}
    line = summary_line(card)
    assert "5/6" in line and "0.8333" in line and "live" in line


def test_compare_detects_regression():
    base = {"pass_rate": 1.0, "cases": [{"name": "a", "passed": True},
                                        {"name": "b", "passed": True}]}
    cur = {"pass_rate": 0.5, "cases": [{"name": "a", "passed": True},
                                       {"name": "b", "passed": False}]}
    d = compare(base, cur)
    assert d["regressed"] == ["b"] and d["fixed"] == []
    assert d["delta"] == -0.5 and d["ok"] is False


def test_compare_detects_fix_and_new_cases():
    base = {"pass_rate": 0.5, "cases": [{"name": "a", "passed": False}]}
    cur = {"pass_rate": 1.0, "cases": [{"name": "a", "passed": True},
                                       {"name": "b", "passed": True}]}
    d = compare(base, cur)
    # 'a' existed and flipped False->True -> fixed; 'b' is brand-new -> added only
    assert d["fixed"] == ["a"] and d["added"] == ["b"]
    assert "b" not in d["fixed"]                    # a new case is not a "fix"
    assert d["delta"] == 0.5 and d["ok"] is True   # no regression -> ok


def test_compare_new_failing_case_is_not_a_regression():
    # a brand-new case that fails must NOT be flagged as a regression
    base = {"pass_rate": 1.0, "cases": [{"name": "a", "passed": True}]}
    cur = {"pass_rate": 0.5, "cases": [{"name": "a", "passed": True},
                                       {"name": "b", "passed": False}]}
    d = compare(base, cur)
    assert d["regressed"] == [] and d["added"] == ["b"] and d["ok"] is True


def test_compare_clean_run_is_ok():
    card = run(live=False)
    d = compare(card, card)            # a run vs itself: no change, no regression
    assert d["delta"] == 0.0 and d["ok"] is True and d["regressed"] == []


def test_main_save_and_compare_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AIO_EVAL_LIVE", raising=False)
    from aio.benchmark import main

    base = tmp_path / "baseline.json"
    assert main(["--save", str(base)]) == 0
    saved = json.loads(base.read_text())
    assert saved["total"] >= 6
    # compare a fresh run to the saved baseline -> no regression -> exit 0
    assert main(["--compare", str(base)]) == 0
    out = capsys.readouterr().out
    assert "delta" in out and "reference mode" in out
