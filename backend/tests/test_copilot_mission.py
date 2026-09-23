"""AI Copilot mission planning tests.

Covers the owner's request shape end to end:

    "scan all markets favourable, i want to trade over 4 prediction,
     under 7 prediction, and tell me the entry digit at that market,
     i want to make 5 profitable runs"

Two properties matter most and are tested hardest:
  1. the copilot answers about the USER'S barriers, not the engine's
     favourites (it must never quietly answer a different question);
  2. the run target is reported honestly - never promised.
"""
from datetime import datetime, timezone

import pytest

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.ai_copilot import ai_copilot
from app.services.cockpit import CockpitEngine
from app.services.mission import (
    DEFAULT_TARGET_RUNS,
    MissionPlanner,
    _runs_math,
    parse_request,
)
from app.services.tick_recorder import tick_recorder

OWNER_QUESTION = (
    "scan all markets favourable, i want to trade over 4 prediction, "
    "under 7 prediction, and tell me the entry digit at that market, "
    "i want to make 5 profitable runs"
)


def _push(symbol, digits, provider="deriv_live"):
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for d in digits:
        tick_queue.push(Tick(symbol=symbol, quote=float(d), raw={"digit": d},
                             provider=provider,
                             timestamp=datetime.now(timezone.utc)))


# OVER 4 wins on 5..9 and UNDER 7 wins on 0..6, so they overlap on digits 5-6.
# For BOTH to clear 68% the overlap must carry most of the mass: here 64% sits
# on 5-6, giving OVER 4 = 79% and UNDER 7 = 85% on one tape.
_LOW_TAPE = [5, 6] * 32 + [7, 8, 9] * 5 + [0, 1, 2, 3, 4] * 4 + [0]
# Concentrated high: OVER 4 wins outright, UNDER 7 does not.
_HIGH_TAPE = [7, 8, 9] * 32 + [5] * 4
# A fair tape - nothing should qualify.
_FLAT_TAPE = list(range(10)) * 10


class TestParseRequest:
    """Plain English in, barriers and run target out."""

    def test_owner_question_parses_both_barriers_and_runs(self):
        r = parse_request(OWNER_QUESTION)
        assert {"side": "OVER", "barrier": 4} in r["predictions"]
        assert {"side": "UNDER", "barrier": 7} in r["predictions"]
        assert r["target_runs"] == 5
        assert r["wants_entry_digit"] is True

    def test_shorthand_o_u_is_understood(self):
        r = parse_request("o4 u7, entry digit, 10 profitable runs")
        assert {"side": "OVER", "barrier": 4} in r["predictions"]
        assert {"side": "UNDER", "barrier": 7} in r["predictions"]
        assert r["target_runs"] == 10

    def test_above_and_below_synonyms(self):
        r = parse_request("above 4 and below 7")
        assert {"side": "OVER", "barrier": 4} in r["predictions"]
        assert {"side": "UNDER", "barrier": 7} in r["predictions"]

    def test_confidence_floor_is_read(self):
        r = parse_request("over 4 with 75% confidence, 3 runs")
        assert r["min_confidence"] == 75.0

    def test_run_target_defaults_without_a_number(self):
        r = parse_request("over 4 under 7")
        assert r["target_runs"] == DEFAULT_TARGET_RUNS

    def test_unpriced_barriers_are_refused(self):
        """OVER 9 and UNDER 0 win on nothing - they must never be planned."""
        assert parse_request("over 9 under 0")["predictions"] == []

    def test_no_barrier_yields_no_predictions(self):
        assert parse_request("what is my balance")["predictions"] == []

    def test_duplicate_barriers_collapse(self):
        r = parse_request("over 4 over 4 over 4")
        assert r["predictions"] == [{"side": "OVER", "barrier": 4}]

    def test_run_target_is_capped(self):
        assert parse_request("over 4, 9999 runs")["target_runs"] == 50


class TestRunsMath:
    """The run target must be answered with maths, never a promise."""

    def test_compound_probability_is_the_product(self):
        m = _runs_math(0.78, 5)
        assert m["p_all_runs"] == pytest.approx(0.78 ** 5, abs=1e-6)

    def test_expected_attempts_is_target_over_probability(self):
        m = _runs_math(0.80, 5)
        assert m["expected_attempts_to_target"] == 6  # ceil(5/0.8)

    def test_note_never_uses_promising_language(self):
        note = _runs_math(0.9, 5)["note"]
        assert "target, not a promise" in note
        for banned in ("guaranteed", "will make", "sure", "certain"):
            assert banned not in note.lower()

    def test_degenerate_probabilities_are_clamped(self):
        assert _runs_math(0.0, 3)["p_single_run"] == 0.001
        assert _runs_math(1.0, 3)["p_single_run"] == 0.999


class TestMissionScan:
    """Scanning must answer the requested barriers, not substitute others."""

    def test_finds_a_market_supporting_the_requested_barriers(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan(OWNER_QUESTION, ["M_LOW"])
        assert plan["candidates"], plan["answer"]
        legs = plan["candidates"][0]["legs"]
        for want in (("OVER", 4), ("UNDER", 7)):
            assert any(lg["side"] == want[0] and lg["barrier"] == want[1]
                       for lg in legs), legs

    def test_never_reports_a_barrier_the_user_did_not_ask_for(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan(
            "over 4 under 7, 3 runs", ["M_LOW"], predictions=[
                {"side": "OVER", "barrier": 4}, {"side": "UNDER", "barrier": 7}])
        for c in plan["candidates"]:
            for lg in c["legs"]:
                assert (lg["side"], lg["barrier"]) in {("OVER", 4), ("UNDER", 7)}

    def test_entry_digit_is_reported_at_the_requested_prediction(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan(OWNER_QUESTION, ["M_LOW"])
        for c in plan["candidates"]:
            for lg in c["legs"]:
                assert lg["entry"] is not None
                if lg["side"] == "OVER":
                    assert lg["entry"]["digit"] == lg["barrier"] + 1
                else:
                    assert lg["entry"]["digit"] == lg["barrier"] - 1

    def test_flat_tape_qualifies_nothing(self):
        _push("M_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 5 runs", ["M_FLAT"])
        assert plan["verdict"] == "NO_MARKET"
        assert plan["candidates"] == []
        assert "No trade is the correct answer" in plan["answer"]

    def test_a_direction_the_tape_contradicts_is_not_offered(self):
        """High tape: OVER 4 wins, UNDER 7 must not be reported as playable."""
        _push("M_HIGH", _HIGH_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["M_HIGH"])
        for c in plan["candidates"]:
            assert not any(lg["side"] == "UNDER" and lg["barrier"] == 7
                           for lg in c["legs"])

    def test_demo_markets_are_never_selected(self):
        _push("M_DEMO", _LOW_TAPE, provider="demo")
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["M_DEMO"])
        assert plan["verdict"] == "NO_MARKET"

    def test_selected_runs_are_live_only(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["M_LOW"])
        assert all(c["live"] for c in plan["selected"])

    def test_scan_survives_a_broken_market(self):
        """One bad symbol must never take down the whole scan."""
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs",
                                     ["!!not_a_symbol!!", "M_LOW"])
        assert plan["verdict"] in ("PARTIAL_LADDER", "FULL_LADDER")

    def test_scanned_list_covers_every_market(self):
        _push("M_LOW", _LOW_TAPE)
        _push("M_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["M_LOW", "M_FLAT"])
        syms = {s["symbol"] for s in plan["scanned"]}
        assert syms == {"M_LOW", "M_FLAT"}

    def test_no_predictions_asks_for_clarity(self):
        plan = MissionPlanner().plan("hello there", ["M_LOW"])
        assert plan["verdict"] == "NEED_PREDICTIONS"
        assert "barriers" in plan["answer"]

    def test_empty_market_list_is_safe(self):
        plan = MissionPlanner().plan("over 4 under 7", [])
        assert plan["verdict"] == "NO_MARKET"


class TestRunLadderHonesty:
    """The 5-run request must be answered with what is really available."""

    def test_ladder_is_short_marked_when_markets_run_out(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 5 runs", ["M_LOW"])
        if plan["verdict"] == "PARTIAL_LADDER":
            assert plan["runs_short_by"] > 0
            assert "not the 5" in plan["answer"]

    def test_full_ladder_reports_the_count(self):
        for s in ("A", "B", "C"):
            _push(s, _LOW_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["A", "B", "C"])
        assert plan["verdict"] == "FULL_LADDER"
        assert len(plan["selected"]) == 3

    def test_runs_math_present_whenever_a_run_is_selected(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 3 runs", ["M_LOW"])
        assert plan["runs"] is not None
        assert "target, not a promise" in plan["runs"]["note"]

    def test_no_market_means_no_runs_block(self):
        _push("M_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 5 runs", ["M_FLAT"])
        assert plan["runs"] is None

    def test_candidates_ranked_by_confidence_then_ev(self):
        _push("M_LOW", _LOW_TAPE)
        _push("M_HIGH", _HIGH_TAPE)
        plan = MissionPlanner().plan("over 4, 3 runs", ["M_LOW", "M_HIGH"])
        confs = [c["play"]["confidence"] for c in plan["candidates"]]
        assert confs == sorted(confs, reverse=True)


class TestCopilotRouting:
    """The chat entry point must route a trade request to the planner."""

    def test_owner_question_returns_a_plan(self):
        _push("R_100", _LOW_TAPE)
        out = ai_copilot.ask(OWNER_QUESTION)
        assert out.get("intent") == "MISSION_PLAN"
        assert "over 4" in out["answer"] or "OVER 4" in out["answer"]

    def test_non_trade_question_is_not_hijacked(self):
        out = ai_copilot.ask("what is my balance")
        assert out.get("intent") != "MISSION_PLAN"

    def test_trade_question_never_returns_a_generic_snapshot(self):
        _push("R_100", _LOW_TAPE)
        out = ai_copilot.ask("scan all markets for over 4, 3 runs")
        assert "Snapshot for" not in out["answer"]

    def test_planner_never_emits_an_order_payload(self):
        _push("R_100", _LOW_TAPE)
        out = ai_copilot.ask(OWNER_QUESTION)
        blob = repr(out).lower()
        for banned in ("place_payload", "scheme_entry", "buy", "contract_id"):
            assert banned not in blob


class TestMissionRoute:
    """HTTP surface."""

    def test_route_returns_a_plan(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _LOW_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "question": "over 4 under 7, 3 runs",
                "symbols": ["R_100"],
            })
        assert res.status_code == 200
        body = res.json()
        assert "candidates" in body and "answer" in body
        assert body["min_confidence_pct"] == 68.0

    def test_route_accepts_explicit_predictions(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _LOW_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "predictions": [{"side": "UNDER", "barrier": 7}],
                "target_runs": 2, "symbols": ["R_100"],
            })
        assert res.status_code == 200
        assert res.json()["requested_predictions"] == [
            {"side": "UNDER", "barrier": 7}]