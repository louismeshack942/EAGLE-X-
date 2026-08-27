"""Unit tests: tick normalization + last-digit extraction (deterministic, transparent)."""

import pytest

from app.core.ticks import (
    last_digit_from_quote,
    last_digits_from_quotes,
    normalize_tick,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (5000.0, 0),
        (5000.1, 1),
        (5000.25, 5),
        (5400.9, 9),
        (5000.2751, 1),
        (4999.7365, 5),
        (123.4, 4),
        (3.0, 3),
        (1_000_000.0, 0),
        (-123.7, 7),
        (0.0, 0),
    ],
)
def test_last_digit_from_quote(value, expected):
    assert last_digit_from_quote(value) == expected


def test_last_digits_from_quotes():
    assert last_digits_from_quotes([1.2, 3.4, 5.6]) == [2, 4, 6]


def test_normalize_tick_ok():
    t = normalize_tick(symbol="R_10", epoch_ms=1000, quote=5000.25)
    assert t.symbol == "R_10"
    assert t.epoch_ms == 1000
    assert t.last_digit == 5
    assert t.provider == "deriv_live"


def test_normalize_tick_provider_harness():
    t = normalize_tick(symbol="R_10", epoch_ms=1, quote=2.3, provider="harness")
    assert t.provider == "harness"
    assert t.last_digit == 3


@pytest.mark.parametrize(
    "quote,exc",
    [(None, ValueError), (float("nan"), ValueError), (float("inf"), ValueError)],
)
def test_normalize_tick_invalid_quote(quote, exc):
    with pytest.raises(exc):
        normalize_tick(symbol="R_10", epoch_ms=1, quote=quote)


def test_normalize_tick_bad_epoch():
    with pytest.raises(ValueError):
        normalize_tick(symbol="R_10", epoch_ms=0, quote=1.0)


def test_last_digit_nan_returns_minus_one():
    assert last_digit_from_quote(float("nan")) == -1