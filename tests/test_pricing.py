"""Tests for the cost-estimation helper."""

from aio.pricing import estimate_cost


def test_priced_model():
    # claude-sonnet-4 -> (3, 15) USD per 1M tokens
    cost, known = estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert known is True
    assert abs(cost - (3.0 + 15.0)) < 1e-9


def test_longest_substring_wins():
    # "gpt-4o-mini" must win over "gpt-4o"
    cost, known = estimate_cost("gpt-4o-mini", 1_000_000, 0)
    assert known is True
    assert abs(cost - 0.15) < 1e-9


def test_unknown_model_is_zero():
    cost, known = estimate_cost("some-unknown-model-xyz", 1000, 1000)
    assert known is False and cost == 0.0


def test_local_model_is_free():
    cost, known = estimate_cost("qwen2.5-coder", 5_000_000, 5_000_000)
    assert known is True and cost == 0.0
