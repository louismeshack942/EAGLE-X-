"""Hypothesis property tests — invariant stress checks."""
from hypothesis import given, strategies as st

from app.core.queue import BoundedTickQueue
from app.models.tick import Tick
from app.services.analytics_advanced import AdvancedAnalytics


# Every digit distribution must sum to the window size
@given(st.lists(st.integers(min_value=0, max_value=9), min_size=10, max_size=200))
def test_digit_frequency_integrity(digits):
    q = BoundedTickQueue()
    for d in digits:
        # build quotes where the last digit of integer part is 'd'
        q.push(Tick(symbol="R_", quote=float(d) + 0.1, provider="demo"))
    engine = AdvancedAnalytics(queue=q)
    out = engine.get_digit_analysis("R_", window=len(digits))
    total = sum(out["frequency"][str(d)]["count"] for d in range(10))
    assert total == len(digits)


# Most-likely digit must always be a valid digit
@given(st.lists(st.floats(min_value=1.0, max_value=1000.0), min_size=10, max_size=300))
def test_predictor_is_valid_digit(quotes):
    q = BoundedTickQueue()
    for price in quotes:
        q.push(Tick(symbol="R_", quote=price, provider="demo"))
    engine = AdvancedAnalytics(queue=q)
    out = engine.get_predictor("R_", window=len(quotes))
    assert out["candidate"] in range(10)
    assert 0.0 <= out["confidence"] <= 100.0
