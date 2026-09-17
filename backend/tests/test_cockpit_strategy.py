"""Cockpit strategy() composer tests.

The user's example bundle - UNDER 6 / OVER 8 / PRED 4 with a x2 martingale -
must compose into a card whose legs carry the right winning-digit bands, an
entry when a digit is playable, and the ladder [1.0, 2.0] when the budget
allows. Advisory only: composing never places a trade.
"""
from datetime import datetime, timezone

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.cockpit import CockpitEngine
from app.services.tick_recorder import tick_recorder

SYM = "STRATEGY_TEST_R_100"

USER_BUNDLE = [
    {"side": "UNDER", "barrier": 6},
    {"side": "OVER", "barrier": 8},
    {"side": "PRED", "barrier": 4},
]


def _push(digits, symbol=SYM, provider="deriv_live"):
    """Replace this symbol's tape with one controlled cut of digits."""
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for d in digits:
        tick_queue.push(Tick(symbol=symbol, quote=float(d), raw={"digit": d},
                             provider=provider,
                             timestamp=datetime.now(timezone.utc)))


def _compose(digits, predictions=None, provider="deriv_live", **kw):
    _push(digits, provider=provider)
    # `is None` (not `or`) so an explicitly empty bundle stays empty.
    bundle = USER_BUNDLE if predictions is None else predictions
    return CockpitEngine().strategy(SYM, predictions=bundle, **kw)


# 90% sevens: a strongly high-biased tape. The user's bundle (UNDER 6 /
# OVER 8) still has NO playable leg here - UNDER 6 needs low digits, OVER 8
# needs nines - so the composer honestly says FAIR.
HIGH = [7] * 90 + [0] * 10
# 70 low digits (UNDER 6 bait) + 20 nines (OVER 8 bait): the bundle has a leg.
MIXED = [0] * 35 + [3] * 35 + [9] * 20 + [7] * 10
# perfectly uniform: nothing clears its own breakeven.
FLAT = [d for d in range(10)] * 10


class TestStrategyBands:
    def test_user_bundle_gets_correct_winning_bands(self):
        out = _compose(MIXED)
        legs = {leg["leg"]: leg for leg in out["legs"]}
        assert set(legs) == {"UNDER 6", "OVER 8", "PRED 4"}
        # OVER b -> b+1..9 ; UNDER b -> 0..b-1 ; PRED b -> exactly {b}
        assert legs["OVER 8"]["band"] == [9]
        assert legs["UNDER 6"]["band"] == [0, 1, 2, 3, 4, 5]
        assert legs["PRED 4"]["band"] == [4]

    def test_band_union_is_the_sorted_union_of_legs(self):
        out = _compose(MIXED)
        assert out["band_union"] == [0, 1, 2, 3, 4, 5, 9]
        assert out["cover_all"] is False

    def test_all_ten_digits_covered_sets_cover_all(self):
        preds = [{"side": "UNDER", "barrier": 5}, {"side": "OVER", "barrier": 4}]
        out = _compose(FLAT, predictions=preds)
        assert out["cover_all"] is True
        assert out["band_union"] == list(range(10))

    def test_exact_digit_leg_is_never_playable(self):
        # a PRED/MATCHES ticket is a 10% lottery: no FBI barrier row prices it.
        out = _compose(MIXED)
        pred = next(leg for leg in out["legs"] if leg["side"] == "PRED")
        assert pred["playable"] is False
        assert pred["digit"] == 4
        assert pred["payout"] == 10.0
        assert "no playable" in pred["evidence"]

    def test_every_valid_leg_is_reported_even_when_unplayable(self):
        # a leg with no playable candidate must still appear, so band_union
        # and cover_all cannot lie about what the bundle covers.
        out = _compose(HIGH)
        assert [leg["leg"] for leg in out["legs"]] == ["UNDER 6", "OVER 8", "PRED 4"]
        assert all(leg["playable"] is False for leg in out["legs"])
        assert out["band_union"] == [0, 1, 2, 3, 4, 5, 9]

    def test_junk_legs_are_dropped_not_fatal(self):
        preds = USER_BUNDLE + [{"side": "OVER", "barrier": 9},
                               {"side": "UNDER", "barrier": 0},
                               {"side": "OVER", "barrier": 42},
                               {"side": "OVER", "barrier": "nope"},
                               "not-a-dict"]
        out = _compose(MIXED, predictions=preds)
        # OVER 9 / UNDER 0 / out-of-range / non-numeric / non-dict all skipped
        assert [leg["leg"] for leg in out["legs"]] == ["UNDER 6", "OVER 8", "PRED 4"]

    def test_unknown_side_is_treated_as_exact_digit(self):
        out = _compose(MIXED, predictions=[{"side": "WEIRD", "barrier": 3}])
        assert out["legs"][0]["side"] == "PRED"
        assert out["legs"][0]["band"] == [3]


class TestStrategyPricing:
    def test_payout_matches_the_priced_band(self):
        out = _compose(MIXED)
        legs = {leg["leg"]: leg for leg in out["legs"]}
        # UNDER 6 tightens to UNDER 1 (digits {0} -> 10/1 = 10).
        assert legs["UNDER 6"]["digit"] == 1
        assert legs["UNDER 6"]["payout"] == 10.0
        # OVER 8's only sub-band is the digit 9 -> 10/1 = 10.
        assert legs["OVER 8"]["digit"] == 8
        assert legs["OVER 8"]["payout"] == 10.0
        assert legs["PRED 4"]["payout"] == 10.0

    def test_leg_only_tightens_never_widens(self):
        out = _compose(MIXED)
        for leg in out["legs"]:
            if leg["side"] == "OVER" and leg["playable"]:
                assert leg["digit"] >= leg["barrier"]
            if leg["side"] == "UNDER" and leg["playable"]:
                assert leg["digit"] <= leg["barrier"]

    def test_entry_present_when_a_digit_is_playable(self):
        out = _compose(MIXED)
        assert out["verdict"] == "EDGE"
        assert out["entry"]["available"] is True
        assert out["entry"]["leg"] in {leg["leg"] for leg in out["legs"]}
        assert out["entry"]["digit"] is not None
        assert out["entry"]["ev"] > 0
        assert out["entry"]["payout"] > 1
        # the entry is the best playable leg overall: max EV, tie-break edge_pp
        playable = [leg for leg in out["legs"] if leg["playable"]]
        expected = max(playable, key=lambda leg: (leg["ev"], leg["edge_pp"]))
        assert out["entry"]["leg"] == expected["leg"]
        assert out["entry"]["digit"] == expected["digit"]

    def test_entry_is_absent_when_the_bundle_has_no_edge(self):
        # the honest case: a strongly high-biased tape is still FAIR for a
        # bundle that bets on low digits and nines.
        out = _compose(HIGH)
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False
        assert out["entry"]["digit"] is None

    def test_no_entry_on_a_flat_tape(self):
        out = _compose(FLAT)
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False

    def test_reason_is_plain_english_and_mentions_the_ladder(self):
        out = _compose(MIXED)
        assert "Tape n=100" in out["reason"]
        assert "Ladder $1.00, $2.00" in out["reason"]

    def test_reason_names_the_winning_leg(self):
        out = _compose(MIXED)
        assert out["entry"]["leg"] in out["reason"]


class TestStrategyHonesty:
    def test_no_tape_is_fair_with_no_entry(self):
        tick_queue.clear(SYM)
        tick_recorder.purge(SYM)
        out = CockpitEngine().strategy(SYM, predictions=USER_BUNDLE, window=100)
        assert out["n"] == 0
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False
        assert "refuses to price" in out["reason"]

    def test_demo_ticks_alone_never_produce_an_edge(self):
        # only deriv_live drives the read: a demo tape is not evidence.
        out = _compose([9] * 100, provider="demo")
        assert out["n"] == 0
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False

    def test_verdict_trap_when_the_whole_board_is_covered_but_unplayable(self):
        preds = [{"side": "UNDER", "barrier": 5}, {"side": "OVER", "barrier": 4}]
        out = _compose(FLAT, predictions=preds)
        assert out["cover_all"] is True
        assert out["entry"]["available"] is False
        assert out["verdict"] == "TRAP"

    def test_trap_is_not_claimed_without_a_live_tape(self):
        tick_queue.clear(SYM)
        tick_recorder.purge(SYM)
        preds = [{"side": "UNDER", "barrier": 5}, {"side": "OVER", "barrier": 4}]
        out = CockpitEngine().strategy(SYM, predictions=preds, window=100)
        assert out["n"] == 0
        assert out["cover_all"] is True
        assert out["verdict"] == "FAIR"

    def test_no_legs_supplied_is_a_clean_fair_card(self):
        out = _compose(MIXED, predictions=[])
        assert out["legs"] == []
        assert out["verdict"] == "FAIR"
        assert out["entry"]["available"] is False
        assert out["band_union"] == []


class TestStrategyMartingale:
    def test_user_bundle_ladder_is_one_and_two_when_budget_allows(self):
        out = _compose(MIXED, martingale_steps=2, budget=100.0)
        assert out["martingale"]["stakes"] == [1.0, 2.0]
        assert out["martingale"]["steps"] == 2
        assert out["martingale"]["capped"] is False
        assert out["martingale"]["strategy"] == "doubling"

    def test_ladder_doubles_and_is_hard_capped_at_five_steps(self):
        out = _compose(MIXED, martingale_steps=9, budget=1e6)
        assert out["martingale"]["stakes"] == [1.0, 2.0, 4.0, 8.0, 16.0]
        assert out["martingale"]["requested_steps"] == 9
        assert out["martingale"]["steps_capped"] is True

    def test_budget_truncates_the_ladder(self):
        out = _compose(MIXED, martingale_steps=4, budget=3.0)
        assert out["martingale"]["stakes"] == [1.0, 2.0]
        assert out["martingale"]["capped"] is True

    def test_ladder_always_keeps_at_least_the_base_stake(self):
        out = _compose(MIXED, martingale_steps=3, budget=0.0)
        assert out["martingale"]["stakes"] == [1.0]
        assert out["martingale"]["capped"] is True

    def test_zero_steps_floors_at_one(self):
        out = _compose(MIXED, martingale_steps=0, budget=100.0)
        assert out["martingale"]["stakes"] == [1.0]

    def test_ladder_scales_with_the_base_stake(self):
        out = _compose(MIXED, base_stake=2.5, martingale_steps=3, budget=100.0)
        assert out["martingale"]["stakes"] == [2.5, 5.0, 10.0]

    def test_recovery_step2_recovers_one_step1_loss(self):
        out = _compose(MIXED, base_stake=1.0)
        payout = out["entry"]["payout"]
        assert payout > 1.0
        # recover the $1 lost on step 1, then clear the step-2 stake
        assert out["martingale"]["recovery_step2"] == round(1.0 * payout / (payout - 1.0), 2)
        assert out["martingale"]["payout"] == payout

    def test_recovery_step2_is_none_when_there_is_no_entry(self):
        out = _compose(HIGH)
        assert out["entry"]["available"] is False
        assert out["martingale"]["recovery_step2"] is None
        assert out["martingale"]["payout"] is None


class TestStrategySchema:
    def test_keys_match_fbi_naming(self):
        out = _compose(MIXED)
        for key in ("symbol", "window", "n", "provider", "verdict", "reason",
                    "legs", "band_union", "cover_all", "entry", "martingale"):
            assert key in out, key
        assert out["provider"] == "deriv_live"
        # leg rows reuse the FBI field names, so the panels need no changes.
        for leg in out["legs"]:
            for key in ("leg", "side", "barrier", "band", "digit", "confidence",
                        "observed_pct", "breakeven_pct", "edge_pp", "ev", "playable"):
                assert key in leg, (leg["leg"], key)

    def test_window_floor_is_inherited_from_fbi(self):
        out = _compose(MIXED, window=5)
        assert out["window"] == 20  # fbi floors the window for thin tape

    def test_composer_is_advisory_only(self):
        # no trade/place/execute side effects anywhere in the card
        out = _compose(MIXED)
        assert "place_payload" not in out
        assert "scheme_entry" not in out


class TestStrategyRoute:
    def test_post_route_serves_the_card(self):
        from fastapi.testclient import TestClient

        from app.main import app

        _push(MIXED)
        client = TestClient(app)
        res = client.post(f"/cockpit/strategy/{SYM}", json={
            "predictions": USER_BUNDLE, "window": 100,
            "martingale_steps": 2, "base_stake": 1.0, "budget": 100.0,
        })
        assert res.status_code == 200
        body = res.json()
        assert body["verdict"] == "EDGE"
        assert [leg["leg"] for leg in body["legs"]] == ["UNDER 6", "OVER 8", "PRED 4"]
        assert body["entry"]["available"] is True
        assert body["martingale"]["stakes"] == [1.0, 2.0]

    def test_post_route_tolerates_an_empty_body(self):
        from fastapi.testclient import TestClient

        from app.main import app

        _push(MIXED)
        res = TestClient(app).post(f"/cockpit/strategy/{SYM}", json={})
        assert res.status_code == 200
        body = res.json()
        assert body["legs"] == []
        assert body["verdict"] == "FAIR"
        assert body["martingale"]["stakes"] == [1.0, 2.0]

    def test_post_route_rejects_unknown_symbol_gracefully(self):
        from fastapi.testclient import TestClient

        from app.main import app

        res = TestClient(app).post("/cockpit/strategy/NO_SUCH_SYMBOL_XYZ",
                                   json={"predictions": USER_BUNDLE})
        assert res.status_code == 200
        body = res.json()
        assert body["n"] == 0
        assert body["verdict"] == "FAIR"
        assert body["entry"]["available"] is False