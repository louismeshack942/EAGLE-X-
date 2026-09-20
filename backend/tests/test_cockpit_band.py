"""Band predictor tests -- OVER 3..UNDER 8, 68% floor, barrier-adjacent entry.

The owner's ask: restrict the OVER/UNDER predictions to the middle band
(OVER 3 .. UNDER 8), attach the market's ENTRY digit at that exact
prediction, and only publish a play whose confidence clears 68%. Worked
example given: "over 3, entry digit 4, confidence 78%".

These tests pin the arithmetic (payout 10/winning-digits, per-barrier
breakeven, Wilson lower bound), the band edges, the 68% gate, the entry
digit rule, and the demo-tick refusal.
"""
from datetime import datetime, timezone

import pytest

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.cockpit import CockpitEngine
from app.services.tick_recorder import tick_recorder

SYM = "BAND_TEST_R_100"


def _push(digits, symbol=SYM, provider="deriv_live"):
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for d in digits:
        tick_queue.push(Tick(symbol=symbol, quote=float(d), raw={"digit": d},
                             provider=provider,
                             timestamp=datetime.now(timezone.utc)))


def _band(digits, window=100, provider="deriv_live"):
    _push(digits, provider=provider)
    return CockpitEngine().band_predict(SYM, window=window)


def _row(rows, side, barrier):
    for r in rows:
        if r["side"] == side and r["barrier"] == barrier:
            return r
    raise AssertionError(f"no {side} {barrier} row")


def _repeat(bias, n=100):
    """A tape biased towards `bias`, with a thin spread over all 10 digits.

    Wider win-sets carry more of the tape, so confidence grows as the barrier
    falls - OVER 3 (wins 4..9) reads more confident than OVER 8 (wins 9).
    """
    base = [bias] * (n - 20)
    rest = (list(range(10)) * 3)[:20]
    return (base + rest)[:n]


# The owner's worked example, isolated. Mass sits on digits 4 and 9 (both
# inside OVER 3, only 4 inside UNDER 8), so OVER 3 wins on 85% of the tape and
# its Wilson lower bound lands near 78% - "OVER 3, ENTRY digit 4, ~78%".
_OVER_THREE_TAPE = [9] * 65 + [4] * 20 + [0] * 15
_UNDER_THREE_TAPE = [2] * 80 + [9] * 20


class TestBandRange:
    """Only barriers 3..8 are ranked - that is the owner's band."""

    def test_band_edges_are_three_and_eight(self):
        r = _band(list(range(10)) * 10)
        assert r["band_range"] == {"min_barrier": 3, "max_barrier": 8}

    def test_only_middle_barriers_are_offered(self):
        r = _band(list(range(10)) * 10)
        for side_key in ("over_bands", "under_bands"):
            barriers = sorted(x["barrier"] for x in r[side_key])
            assert barriers == [3, 4, 5, 6, 7, 8], side_key

    def test_no_row_for_barrier_two_or_nine(self):
        r = _band(list(range(10)) * 10)
        all_rows = r["over_bands"] + r["under_bands"]
        assert not any(x["barrier"] in (0, 1, 2, 9) for x in all_rows)


class TestPayoutAndBreakeven:
    """OVER 3 wins on 6 digits -> 10/6 payout -> 60% to break even."""

    def test_over_three_payout_is_ten_over_six(self):
        r = _band(_repeat(9))
        row = _row(r["over_bands"], "OVER", 3)
        assert row["winning_digits"] == 6
        assert row["payout"] == pytest.approx(1.6667, abs=1e-3)
        assert row["breakeven_pct"] == pytest.approx(60.0, abs=0.01)

    def test_under_three_payout_is_ten_over_three(self):
        r = _band(_repeat(1))
        row = _row(r["under_bands"], "UNDER", 3)
        assert row["winning_digits"] == 3
        assert row["payout"] == pytest.approx(3.3333, abs=1e-3)
        assert row["breakeven_pct"] == pytest.approx(30.0, abs=0.01)

    def test_payout_rises_as_the_band_narrows(self):
        """Fewer winning digits means a bigger payout: OVER 3 pays 10/6, OVER 8 pays 10/1."""
        r = _band(_repeat(9))
        pays = [x["payout"] for x in sorted(r["over_bands"], key=lambda x: x["barrier"])]
        assert pays == sorted(pays), "higher barrier must pay more"
        assert pays[0] == pytest.approx(1.6667, abs=1e-3)
        assert pays[-1] == pytest.approx(10.0, abs=1e-3)

    def test_ev_matches_probability_times_payout_minus_one(self):
        r = _band(_repeat(9))
        for row in r["over_bands"] + r["under_bands"]:
            expected = (row["observed_pct"] / 100.0) * row["payout"] - 1.0
            assert row["ev"] == pytest.approx(expected, abs=1e-3), row


class TestConfidenceAndGate:
    """A band is playable only when confidence clears 68% AND EV is positive."""

    def test_uniform_tape_produces_no_playable_band(self):
        r = _band(list(range(10)) * 10)
        assert all(not x["playable"] for x in r["over_bands"] + r["under_bands"])
        assert r["verdict"] == "FAIR"
        assert r["band"] is None

    def test_uniform_tape_entry_is_unavailable(self):
        r = _band(list(range(10)) * 10)
        assert r["entry"]["available"] is False
        assert r["entry"]["digit"] is None

    def test_playable_band_always_clears_the_floor(self):
        r = _band(_repeat(9))
        for row in [x for x in r["over_bands"] + r["under_bands"] if x["playable"]]:
            assert row["confidence"] >= 68.0
            assert row["ev"] > 0

    def test_confidence_is_below_observed_rate(self):
        """Wilson LB must be conservative - never above the raw rate."""
        r = _band(_repeat(9))
        for row in r["over_bands"] + r["under_bands"]:
            assert row["confidence"] <= row["observed_pct"] + 1e-6

    def test_gate_rejects_high_probability_but_negative_ev(self):
        """A wide band can look confident yet still pay less than breakeven."""
        r = _band(_repeat(9))
        over8 = _row(r["over_bands"], "OVER", 8)
        # 10% breakeven for a 9x payout: confident-looking digits still lose.
        assert over8["breakeven_pct"] == pytest.approx(10.0, abs=0.01)
        assert over8["playable"] is False or over8["ev"] > 0

    def test_strong_bias_yields_an_edge_verdict(self):
        r = _band(_repeat(9))
        assert r["verdict"] == "EDGE"
        assert r["band"] is not None

    def test_wide_bias_tape_finds_over_three_at_high_confidence(self):
        """Digit 9 biased with a thin spread: OVER 3 carries 4..9 and clears 68%."""
        r = _band(_repeat(9))
        row = _row(r["over_bands"], "OVER", 3)
        assert row["confidence"] >= 68.0
        assert row["playable"] is True

    def test_no_band_clears_floor_reports_best_reading(self):
        r = _band(list(range(10)) * 10)
        assert "no band" in r["reason"]
        assert "%" in r["reason"]


class TestEntryDigit:
    """ENTRY digit is the win adjacent to the barrier: OVER 3 -> digit 4."""

    def test_over_three_entry_digit_is_four(self):
        """The owner's exact example: OVER 3 -> ENTRY digit 4."""
        r = _band(_OVER_THREE_TAPE)
        assert r["band"]["side"] == "OVER"
        assert r["band"]["barrier"] == 3
        assert r["band"]["entry_digit"] == 4
        assert r["entry"]["digit"] == 4
        assert r["entry"]["available"] is True

    def test_over_three_example_reads_about_seventy_eight_percent(self):
        """Worked example: OVER 3 at ~78% confidence, payout 10/6."""
        r = _band(_OVER_THREE_TAPE)
        over3 = _row(r["over_bands"], "OVER", 3)
        assert over3["observed_pct"] == pytest.approx(85.0, abs=0.1)
        assert over3["confidence"] == pytest.approx(78.0, abs=2.5)
        assert over3["confidence"] >= 68.0
        assert over3["payout"] == pytest.approx(1.6667, abs=1e-3)
        assert over3["ev"] > 0

    def test_under_entry_digit_is_barrier_minus_one(self):
        r = _band(_repeat(1))
        assert r["band"]["side"] == "UNDER"
        assert r["entry"]["digit"] == r["band"]["barrier"] - 1

    def test_entry_digit_always_sits_inside_the_band(self):
        """The entry must be a winning digit of its own band, never outside."""
        for bias in range(10):
            r = _band(_repeat(bias))
            if not (r["band"] and r["entry"]["available"]):
                continue
            b = r["band"]["barrier"]
            d = r["entry"]["digit"]
            if r["band"]["side"] == "OVER":
                assert b + 1 <= d <= 9
            else:
                assert 0 <= d <= b - 1

    def test_entry_digit_is_none_without_a_band(self):
        r = _band(list(range(10)) * 10)
        assert r["entry"]["digit"] is None
        assert r["entry"]["pct"] == 0.0

    def test_entry_reports_its_own_tape_share(self):
        digits = [5] * 40 + list(range(10)) * 6
        r = _band(digits)
        e = r["entry"]
        assert e["available"] is True
        # The reported percentage must equal that digit's real share.
        share = digits.count(e["digit"]) / len(digits) * 100.0
        assert e["pct"] == pytest.approx(share, abs=0.5)

    def test_thin_single_digit_is_flagged_not_hidden(self):
        """A band can carry the edge even when its entry digit is not a favourite."""
        r = _band(_repeat(9))
        assert r["entry"]["available"] is True
        assert r["entry"]["inside"] in (True, False)
        if not r["entry"]["inside"]:
            assert "the band carries it" in r["reason"]

    def test_entry_digit_is_reported_even_when_unplayable(self):
        """No band -> entry is explicitly unavailable, never a silent zero."""
        r = _band(list(range(10)) * 10)
        assert r["band"] is None
        assert r["entry"]["available"] is False
        assert r["entry"]["inside"] is False
        assert r["entry"]["wilson_lb"] == 0.0


class TestHonestyGates:
    """The band card must never be driven by fabricated or stale tape."""

    def test_demo_ticks_never_produce_a_band(self):
        r = _band(_repeat(9), provider="demo")
        assert r["n"] == 0
        assert r["verdict"] == "FAIR"
        assert r["band"] is None

    def test_no_tape_is_fair_and_silent(self):
        tick_queue.clear(SYM)
        tick_recorder.purge(SYM)
        r = CockpitEngine().band_predict(SYM, window=100)
        assert r["n"] == 0
        assert r["verdict"] == "FAIR"
        assert r["band"] is None
        assert r["entry"]["available"] is False

    def test_provider_is_always_labelled_live(self):
        r = _band(_repeat(9))
        assert r["provider"] == "deriv_live"

    def test_never_emits_an_order_payload(self):
        """Advisory only - no trade/place payload may appear anywhere."""
        r = _band(_repeat(9))
        blob = repr(r).lower()
        for banned in ("place_payload", "scheme_entry", "buy", "contract_id"):
            assert banned not in blob

    def test_single_digit_bias_cannot_invent_an_over_band(self):
        """All zeroes: OVER is hopeless, UNDER 8 is the only honest read."""
        r = _band([0] * 100)
        assert all(not x["playable"] for x in r["over_bands"])
        if r["band"]:
            assert r["band"]["side"] == "UNDER"

    def test_sample_is_carried_on_every_row(self):
        r = _band(_repeat(9), window=50)
        for row in r["over_bands"] + r["under_bands"]:
            assert row["sample"] == r["n"]


class TestBandSurface:
    """Ranking, payload shape and the HTTP route."""

    def test_bands_are_ranked_by_confidence(self):
        r = _band(_repeat(9))
        confs = [x["confidence"] for x in r["bands"]]
        assert confs == sorted(confs, reverse=True)

    def test_band_is_the_strongest_playable_row(self):
        r = _band(_repeat(9))
        if r["band"] is None:
            pytest.skip("no playable band on this tape")
        best = max([x for x in r["over_bands"] + r["under_bands"] if x["playable"]],
                   key=lambda x: (x["confidence"], x["ev"]))
        assert r["band"]["side"] == best["side"]
        assert r["band"]["barrier"] == best["barrier"]

    def test_payload_exposes_the_spec(self):
        r = _band(_repeat(9))
        assert r["min_confidence_pct"] == 68.0
        assert r["band_range"]["min_barrier"] == 3
        assert r["band_range"]["max_barrier"] == 8
        for key in ("symbol", "n", "verdict", "reason", "band", "bands",
                    "over_bands", "under_bands", "entry", "ts"):
            assert key in r, key

    def test_route_returns_the_same_shape(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push(_repeat(9))
        with TestClient(app) as c:
            res = c.get(f"/cockpit/band/{SYM}?window=100")
        assert res.status_code == 200
        body = res.json()
        assert body["band_range"] == {"min_barrier": 3, "max_barrier": 8}
        assert body["min_confidence_pct"] == 68.0
        assert "band" in body and "entry" in body