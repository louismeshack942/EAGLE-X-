"""FBI (Final Bureau of Intelligence) verdict tests.

The user's ask: when a coarse prediction exists (e.g. "OVER 5"), the FBI
space should conclude with per-digit confidence -- "OVER 3 at 80%, UNDER 7
at 96%, entry point at digit 4" -- plus final evidence. These tests pin the
math: per-barrier Wilson lower-bound confidence, breakeven-aware edge and
EV, and the strongest OVER / UNDER / ENTRY picks.
"""
from datetime import datetime, timezone

import pytest

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.cockpit import CockpitEngine

SYM = "FBI_TEST_R_100"


def _push(digits, symbol=SYM):
    """Replace the queue tape for `symbol` with one cut of controlled digits."""
    tick_queue.clear(symbol)
    for d in digits:
        tick_queue.push(Tick(symbol=symbol, quote=float(d),
                             raw={"digit": d},
                             timestamp=datetime.now(timezone.utc)))


def _fbi(digits, window=100):
    _push(digits)
    return CockpitEngine().fbi(SYM, window=window)


class TestFbiHonestOnNoTape:
    def test_no_tape_is_fair_with_no_entry(self):
        tick_queue.clear(SYM)
        out = CockpitEngine().fbi(SYM, window=100)
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False
        assert out["ranked"] == []


class TestFbiSkewedTape:
    def test_strong_high_bias_yields_confident_over_and_over_entry(self):
        # 90% of digits are 7+ -> OVER 1 (next > 1) is ~90% observed; the
        # strongest *coarse* read is OVER with significant confidence. The
        # strongest single play (max EV) stays OVER because the high-bias
        # gives every low barrier a fat OVER edge.
        digits = [7] * 90 + [0] * 10
        out = _fbi(digits)
        assert out["verdict"] == "EDGE"
        assert out["confidence_over"]["digit"] == 1
        assert out["confidence_over"]["confidence"] > 80.0
        assert out["confidence_over"]["ev"] > 0
        assert out["entry"]["available"] is True
        assert out["entry"]["side"] == "OVER"
        assert out["entry"]["ev"] > 0
        # the entry digit is the precise single digit for the trade, and the
        # ranked table must be non-empty
        assert out["entry"]["digit"] is not None
        assert len(out["ranked"]) > 0

    def test_strong_low_bias_yields_confident_under(self):
        # 90% of digits are 0 -> UNDER 8 (next < 8) is ~90% observed and is
        # the strongest UNDER; the strongest single play (max EV) is UNDER.
        digits = [0] * 90 + [7] * 10
        out = _fbi(digits)
        assert out["verdict"] == "EDGE"
        assert out["confidence_under"]["digit"] in (8, 9)
        assert out["confidence_under"]["confidence"] > 90.0
        assert out["entry"]["available"] is True
        assert out["entry"]["side"] == "UNDER"
        assert out["entry"]["ev"] > 0

    def test_reason_includes_evidence(self):
        digits = [7] * 90 + [0] * 10
        out = _fbi(digits)
        assert "ENTRY digit" in out["reason"]


class TestFbiFlatTape:
    def test_uniform_digits_give_no_edge(self):
        # perfectly uniform 0..9 -> no barrier should clear its breakeven
        digits = [d for d in range(10)] * 10  # 100 uniform
        out = _fbi(digits)
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False
        assert len(out["ranked"]) == 0


class TestFbiBarrierMath:
    def test_all_zeroes_inverts_to_under_1_not_over(self):
        _push([0] * 100)  # all digits 0
        out = CockpitEngine().fbi(SYM, window=100)
        # With all zeroes: OVER d always loses (P(digit>d)=0), so no OVER edge.
        # UNDER d wins whenever next digit < d (always true for d>=1) => the
        # tightest barrier UNDER 1 (next digit < 1 == next is 0) has the best
        # payoff/edge: 100% observed vs 10% breakeven = 96% Wilson LB.
        assert out["confidence_over"]["playable"] is False
        assert out["confidence_under"]["digit"] == 1
        assert out["confidence_under"]["confidence"] > 90.0
        assert out["entry"]["side"] == "UNDER"

    def test_evidence_uses_wilson_lb_not_raw(self):
        # on thin tape, a 100% raw share should shrink under Wilson
        tick_queue.clear(SYM)
        for _ in range(5):
            tick_queue.push(Tick(symbol=SYM, quote=7.0, raw={"digit": 7}))
        out = CockpitEngine().fbi(SYM, window=20)  # window floor kicks in
        # raw P(digit>0) = 1.0 => wilson LB on n=5 for barrier 0
        # (1.0 -> LB ~ 0.478). So even a "perfect" 5-tick tape can't claim >90%.
        assert out["confidence_over"]["confidence"] < 90.0