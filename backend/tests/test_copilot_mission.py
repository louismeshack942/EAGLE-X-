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
    _qualifies,
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
        assert plan["markets"], plan["answer"]
        for m in plan["markets"]:
            wanted = {(b["side"], b["barrier"]) for b in m["all_barriers"]}
            assert ("OVER", 4) in wanted and ("UNDER", 7) in wanted, wanted

    def test_never_reports_a_barrier_the_user_did_not_ask_for(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan(
            "over 4 under 7, 3 runs", ["M_LOW"], predictions=[
                {"side": "OVER", "barrier": 4}, {"side": "UNDER", "barrier": 7}])
        # Explicit predictions pin the plan path; it must still only ever
        # mention the two barriers that were asked for.
        for c in plan["candidates"]:
            for lg in c["legs"]:
                assert (lg["side"], lg["barrier"]) in {("OVER", 4), ("UNDER", 7)}
        for r in plan["probabilities"]:
            assert (r["side"], r["barrier"]) in {("OVER", 4), ("UNDER", 7)}

    def test_entry_digit_is_reported_at_the_requested_prediction(self):
        _push("M_LOW", _LOW_TAPE)
        plan = MissionPlanner().plan(OWNER_QUESTION, ["M_LOW"])
        checked = 0
        for m in plan["markets"]:
            for b in m["all_barriers"]:
                assert b["entry_digit"] is not None
                if b["side"] == "OVER":
                    assert b["entry_digit"] == b["barrier"] + 1
                else:
                    assert b["entry_digit"] == b["barrier"] - 1
                checked += 1
        assert checked > 0

    def test_flat_tape_qualifies_nothing(self):
        _push("M_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan("over 4 under 7, 5 runs", ["M_FLAT"])
        assert plan["verdict"] == "NO_EDGE_FOUND"
        assert plan["candidates"] == []
        # No edge, but the analysis is still published - the owner asked to see
        # the markets, so a refusal is not an acceptable answer.
        assert plan["board"]
        assert "no edge to take" in plan["answer"]
        assert plan["probabilities"]

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
        assert plan["verdict"] == "NO_EDGE_FOUND"

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
        assert plan["verdict"] == "NO_EDGE_FOUND"


class TestScanReporting:
    """A scan REPORTS; it does not advise. The owner's directive.

    "scan best over 4 markets entry points" must return the OVER 4 probability
    per market and their entry digits - never a refusal, never a lecture about
    whether to trade.
    """

    def test_over_four_scan_reports_probability_and_entry_digit(self):
        _push("S_A", ["7", "8", "9"] * 30 + ["0"] * 10)
        _push("S_B", _FLAT_TAPE)
        plan = MissionPlanner().plan(
            "scan best over 4 markets entry points", ["S_A", "S_B"])
        assert plan["verdict"] == "SCAN"
        rows = plan["rows"]
        assert {r["symbol"] for r in rows} == {"S_A", "S_B"}
        for r in rows:
            assert r["side"] == "OVER" and r["barrier"] == 4
            assert r["observed_pct"] is not None      # the probability
            assert r["entry"]["digit"] == 5           # the entry digit
        # Both markets appear even though only one is playable.
        assert "S_B" in plan["answer"]

    def test_scan_never_refuses_on_a_flat_tape(self):
        _push("S_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan("scan all markets over 4", ["S_FLAT"])
        assert plan["verdict"] == "SCAN"
        assert plan["rows"]
        for phrase in ("No trade is the correct answer", "Standing down",
                       "none supports it", "no edge to take"):
            assert phrase not in plan["answer"], phrase

    def test_scan_reports_all_ten_barriers_when_asked_for_all(self):
        _push("S_ALL", _FLAT_TAPE)
        card = MissionPlanner().scan(["S_ALL"], [])
        assert card["probabilities"] == []      # no barrier named -> nothing

    def test_scan_lists_every_market_not_only_playable_ones(self):
        """The whole point: no filtering of the board."""
        tape = ["7", "8", "9"] * 30 + ["0"] * 10
        _push("S_GOOD", tape)
        _push("S_BAD", ["0"] * 125 + ["9"] * 125)
        card = MissionPlanner().scan(
            ["S_GOOD", "S_BAD"], [{"side": "OVER", "barrier": 4}])
        syms = {r["symbol"] for r in card["probabilities"]}
        assert syms == {"S_GOOD", "S_BAD"}
        playable = {r["symbol"] for r in card["probabilities"] if r["playable"]}
        assert "S_GOOD" in playable
        assert "S_BAD" not in playable

    def test_scan_kind_is_reported_as_scan_intent(self):
        from app.services.ai_copilot import ai_copilot
        _push("S_CHAT", _FLAT_TAPE)
        out = ai_copilot.ask("scan all markets over 4")
        assert out["intent"] in ("SCAN", "MISSION_PLAN")


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

    def test_owner_question_returns_a_scan_listing(self):
        _push("R_100", _LOW_TAPE)
        out = ai_copilot.ask(OWNER_QUESTION)
        assert out.get("intent") == "SCAN"
        # Both asked barriers are carried; the prose lists each market's best.
        got = {(p["side"], p["barrier"]) for p in out["data"]["requested_predictions"]}
        assert got == {("OVER", 4), ("UNDER", 7)}
        assert out["data"]["markets"]

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


class TestOverFourScan:
    """The owner's OVER 4 focus: scan every market, report its best entry point.

    The regression these lock down: an absolute 68% floor was applied to a
    Wilson LOWER BOUND, which forced every barrier to the same 73.8% observed
    rate. On OVER 4 (payout 2.0, breakeven 50%) that is a 7.5-sigma demand, so
    the contract could never be called playable - a genuine +10pp edge was
    refused. The gate is now breakeven-relative.
    """

    def test_over_4_with_a_real_edge_is_playable(self):
        from app.services.cockpit import barrier_row
        # 60% of the tape on digits 5..9: +10pp over OVER 4's 50% breakeven.
        counts = [20] * 5 + [30] * 5
        row = barrier_row(counts, 250, "OVER", 4)
        assert row["breakeven_pct"] == pytest.approx(50.0)
        assert row["edge_pp"] == pytest.approx(10.0)
        assert row["playable"] is True, row

    def test_over_4_on_a_fair_tape_is_not_playable(self):
        from app.services.cockpit import barrier_row
        row = barrier_row([25] * 10, 250, "OVER", 4)
        assert row["observed_pct"] == pytest.approx(50.0)
        assert row["playable"] is False

    def test_the_same_tape_is_judged_per_contract_not_globally(self):
        """Each contract is judged against ITS OWN breakeven, not one flat bar.

        A tape skewed low is good for UNDER 8 (breakeven 80%) and bad for
        OVER 4 (breakeven 50%). Under the old absolute floor both were compared
        to the same 68%, which is meaningless across different payouts.
        """
        from app.services.cockpit import barrier_row
        counts = [30] * 8 + [5] * 2           # 96% on digits 0..7
        under8 = barrier_row(counts, 250, "UNDER", 8)
        over4 = barrier_row(counts, 250, "OVER", 4)
        assert under8["breakeven_pct"] == pytest.approx(80.0)
        assert over4["breakeven_pct"] == pytest.approx(50.0)
        assert under8["playable"] is True
        assert over4["playable"] is False

    def test_a_high_reading_below_breakeven_is_still_refused(self):
        """The failure the absolute floor could NOT catch: a wide barrier can
        read 76% and look 'confident' while still losing at an 80% breakeven."""
        from app.services.cockpit import barrier_row
        counts = [20] * 5 + [30] * 5          # 76% on 0..7
        under8 = barrier_row(counts, 250, "UNDER", 8)
        assert under8["observed_pct"] == pytest.approx(76.0)
        assert under8["breakeven_pct"] == pytest.approx(80.0)
        assert under8["confidence"] > 68.0     # would have cleared the old floor
        assert under8["edge_pp"] < 0
        assert under8["playable"] is False

    def test_scan_all_reports_an_entry_point_on_every_market(self):
        """A "best entry point" question must be answerable even where nothing
        is playable - the point exists, it just is not an edge."""
        _push("R_100", ["5"] * 40 + list(range(10)) * 4)
        _push("R_50", _FLAT_TAPE)
        card = MissionPlanner().scan(["R_100", "R_50"],
                                     [{"side": "OVER", "barrier": 4}])
        assert len(card["scanned"]) == 2
        for row in card["scanned"]:
            assert row["best_entry"] is not None, row
            assert row["best_entry"]["digit"] == 5      # OVER 4 -> entry 5
            assert row["best_entry"]["side"] == "OVER"

    def test_scan_all_over_4_finds_the_favourable_market(self):
        _push("R_100", ["7", "8", "9"] * 30 + ["0"] * 10)   # OVER 4 wins big
        _push("R_50", _FLAT_TAPE)                           # fair
        card = MissionPlanner().scan(["R_100", "R_50"],
                                     [{"side": "OVER", "barrier": 4}])
        assert [c["symbol"] for c in card["candidates"]] == ["R_100"]
        play = card["candidates"][0]["play"]
        assert play["side"] == "OVER" and play["barrier"] == 4
        assert play["entry_digit"] == 5
        assert play["edge_pp"] > 0

    def test_entry_point_is_the_digit_adjacent_to_the_barrier(self):
        _push("R_100", ["5"] * 40 + list(range(10)) * 4)
        card = MissionPlanner().scan(["R_100"],
                                     [{"side": "OVER", "barrier": 4}])
        assert card["scanned"][0]["best_entry"]["digit"] == 5
        assert card["scanned"][0]["best_entry"]["inside"] is True

    def test_plan_answers_over_4_scan_in_plain_english(self):
        _push("R_100", ["7", "8", "9"] * 30 + ["0"] * 10)
        plan = MissionPlanner().plan(
            "scan all over 4 markets and their best entry point",
            ["R_100"])
        assert plan["requested_predictions"] == [{"side": "OVER", "barrier": 4}]
        assert "OVER 4" in plan["answer"]
        assert "entry digit" in plan["answer"]
        # The gate is described honestly - no phantom 68% floor.
        assert "68%" not in plan["answer"]

    def test_scan_reports_numbers_instead_of_refusing(self):
        """A scan request reports the board - it does not advise or refuse.

        The owner's directive: "scan the market as the instruction is given".
        A flat tape has no edge, but a scan still returns every measurement
        rather than answering a question about whether to trade.
        """
        _push("R_100", _FLAT_TAPE)
        plan = MissionPlanner().plan("scan all over 4 markets", ["R_100"])
        assert plan["verdict"] == "SCAN"
        assert plan["rows"]
        assert plan["board"]
        # The numbers are present and ungated: rate, breakeven, margin, payout.
        row = plan["rows"][0]
        assert row["observed_pct"] is not None
        assert row["breakeven_pct"] is not None
        assert row["payout"] is not None
        assert "win rate" in plan["answer"]
        assert "breakeven" in plan["answer"]
        # No refusal language.
        assert "No trade is the correct answer" not in plan["answer"]

    def test_scan_ranks_entry_points_when_asked(self):
        _push("R_100", ["7", "8", "9"] * 30 + ["0"] * 10)
        plan = MissionPlanner().plan(
            "scan all over 4 markets and their entry points", ["R_100"])
        assert plan["verdict"] == "SCAN"
        assert plan["markets"]
        top = plan["best_entry"]
        assert top["digit"] == 5            # OVER 4 -> entry digit 5
        assert top["digit_pct"] is not None

    def test_scan_lists_every_market_with_entry_point_and_runs(self):
        """The directive: after a scan, list ALL markets, their best entry
        point, and the run projections - not a statement about them."""
        _push("S_GOOD", ["7", "8", "9"] * 30 + ["0"] * 10)
        _push("S_FLAT", _FLAT_TAPE)
        plan = MissionPlanner().plan(
            "scan all markets over 4 entry point for 5 or 10 runs",
            ["S_GOOD", "S_FLAT"])
        assert plan["verdict"] == "SCAN"
        assert plan["target_runs_options"] == [5, 10]
        # Every market is listed, playable or not.
        assert {m["symbol"] for m in plan["markets"]} == {"S_GOOD", "S_FLAT"}
        for m in plan["markets"]:
            e = m["best_entry"]
            assert e["side"] == "OVER" and e["barrier"] == 4
            assert e["digit"] == 5                     # entry point
            assert e["band_observed_pct"] is not None  # probability
            assert [r["target"] for r in m["runs"]] == [5, 10]
            for r in m["runs"]:
                assert 0.0 < r["p_all_runs"] <= 1.0
        # Ranked by measured win rate, best market first.
        rates = [m["best_entry"]["band_observed_pct"] for m in plan["markets"]]
        assert rates == sorted(rates, reverse=True)

    def test_scan_answer_is_a_listing_not_advice(self):
        _push("S_ONE", _FLAT_TAPE)
        plan = MissionPlanner().plan(
            "scan all markets over 4 entry point 5 runs", ["S_ONE"])
        for phrase in ("No trade is the correct answer", "no edge to take",
                       "I scanned", "Nothing cleared", "Standing down"):
            assert phrase not in plan["answer"], phrase

    def test_explicit_floor_still_bites_when_asked_for(self):
        """The opt-in floor must remain honoured - it is not simply deleted."""
        counts = [20] * 5 + [30] * 5     # OVER 4 confidence ~53.8%
        from app.services.cockpit import barrier_row
        row = barrier_row(counts, 250, "OVER", 4)
        assert row["confidence"] < 68.0
        assert _qualifies(row, None) is True
        assert _qualifies(row, 68.0) is False
        assert _qualifies(row, 50.0) is True


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
        # No floor was named, so none is imposed - the rows are judged by their
        # own breakeven gate. An absolute floor is opt-in.
        assert body["min_confidence_pct"] is None

    def test_route_honours_an_explicitly_named_floor(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _LOW_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "question": "over 4 under 7, 3 runs, 70% confidence",
                "symbols": ["R_100"],
            })
        assert res.status_code == 200
        assert res.json()["min_confidence_pct"] == 70.0

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


class TestFindSymbol:
    """A question that names a market must be answered for THAT market."""

    def test_code_is_matched(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("probability of over 5 on R_100") == "R_100"

    def test_code_without_underscore(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("chance of under 3 on r50") == "R_50"

    def test_one_second_code(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("odds on 1HZ25V") == "1HZ25V"

    def test_spoken_volatility_name(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("over 6 on volatility 75") == "R_75"

    def test_spoken_one_second_name(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("over 3 on volatility 100 1s") == "1HZ100V"

    def test_jump_name(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("over 4 on jump 50") == "JD50"

    def test_bear_and_bull(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("under 4 on bear market") == "RDBEAR"
        assert _find_symbol("under 4 on bull market") == "RDBULL"

    def test_no_market_named_returns_none(self):
        from app.services.mission import _find_symbol
        assert _find_symbol("what is the probability of over 5") is None


class TestProbabilityIntent:
    def test_plain_probability_question_is_detected(self):
        p = parse_request("what is the probability of over 5")
        assert p["wants_probability"] is True
        assert p["explicit_runs"] is False
        assert p["wants_scan_all"] is False

    def test_chance_and_odds_words(self):
        assert parse_request("chance of over 4")["wants_probability"] is True
        assert parse_request("odds of under 3")["wants_probability"] is True

    def test_run_request_is_not_a_probability_question(self):
        p = parse_request("over 4 under 7, 5 runs")
        assert p["wants_probability"] is False
        assert p["explicit_runs"] is True

    def test_scan_request_is_not_a_probability_question(self):
        p = parse_request("scan all markets over 4 under 7")
        assert p["wants_probability"] is False
        assert p["wants_scan_all"] is True


class TestProbabilityAnswer:
    def test_reports_the_measured_rate_for_one_market(self):
        _push("R_100", _HIGH_TAPE * 30)   # deep tape so every window has data
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100"], symbol="R_100")
        assert card["probability"] > 80
        assert card["best"]["symbol"] == "R_100"

    def test_a_persistent_skew_is_confirmed_as_an_edge(self):
        """The skew must survive every window before it is called an EDGE."""
        _push("R_100", _HIGH_TAPE * 30)
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100"], symbol="R_100")
        assert card["verdict"] == "EDGE"
        assert len(card["windows"]) >= 2
        assert all(w["edge_pp"] > 0 for w in card["windows"])

    def test_one_window_alone_is_never_called_an_edge(self):
        """Regression guard for the phantom-edge session: a single thin window
        must not be reported as an edge."""
        _push("R_100", _HIGH_TAPE)   # 36 ticks - only the smallest window
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100"], symbol="R_100")
        assert card["verdict"] != "EDGE"

    def test_unconfirmed_is_reported_when_windows_disagree(self):
        """A rate that inverts on a longer window is not an edge."""
        _push("R_100", _HIGH_TAPE * 4 + _FLAT_TAPE * 40)
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100"], symbol="R_100")
        if card["verdict"] == "UNCONFIRMED":
            assert "does NOT hold" in card["answer"]

    def test_reports_no_edge_when_rate_is_below_breakeven(self):
        _push("R_100", _FLAT_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100"], symbol="R_100")
        assert card["verdict"] == "NO_EDGE"
        assert "no edge" in card["answer"].lower()

    def test_measured_rate_is_not_gated_by_the_confidence_floor(self):
        """A probability question is a measurement - it must answer even when
        nothing clears the 68% trade floor."""
        _push("R_100", _FLAT_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100"], symbol="R_100")
        assert card["best"] is not None
        assert card["probability"] is not None
        assert card["best"]["observed_pct"] is not None

    def test_breakeven_is_reported_alongside_the_rate(self):
        _push("R_100", _HIGH_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100"], symbol="R_100")
        # OVER 4 wins on digits 5..9 = 5 digits, so payout 10/5 = 2.00x and
        # breakeven is 50%. The rate must be reported against that.
        assert card["best"]["winning_digits"] == 5
        assert card["best"]["payout"] == 2.0
        assert card["best"]["breakeven_pct"] == 50.0

    def test_named_market_wins_over_the_highest_market(self):
        """R_100 must be answered even if another market reads higher."""
        _push("R_100", _FLAT_TAPE)
        _push("R_50", _HIGH_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4 on R_100", ["R_100", "R_50"], symbol="R_100")
        assert card["best"]["symbol"] == "R_100"

    def test_all_markets_scanned_when_none_is_named(self):
        _push("R_100", _FLAT_TAPE)
        _push("R_50", _HIGH_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100", "R_50"])
        assert card["best"]["symbol"] == "R_50"   # highest rate wins

    def test_no_predictions_asks_for_a_barrier(self):
        card = MissionPlanner().probability("what is the probability", ["R_100"])
        assert card["verdict"] == "NEED_PREDICTIONS"

    def test_no_tape_is_reported_honestly(self):
        tick_queue.clear("R_100")
        tick_recorder.purge("R_100")
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100"], symbol="R_100")
        assert card["verdict"] == "NO_TAPE"
        assert card["probabilities"] == []

    def test_never_emits_an_order_payload(self):
        _push("R_100", _HIGH_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100"], symbol="R_100")
        assert "place_payload" not in card
        assert "scheme_entry" not in card

    def test_kind_is_marked_probability(self):
        _push("R_100", _HIGH_TAPE)
        card = MissionPlanner().probability(
            "probability of over 4", ["R_100"], symbol="R_100")
        assert card["kind"] == "PROBABILITY"


class TestProbabilityRouting:
    def test_typed_question_returns_a_probability_card(self):
        _push("R_100", _HIGH_TAPE)
        res = ai_copilot.ask("what is the probability of over 4 on R_100")
        assert res["intent"] == "PROBABILITY"
        assert res["symbol"] == "R_100"
        assert res["data"]["probability"] is not None

    def test_routing_does_not_raise_on_the_probability_shape(self):
        """Regression: the router read plan["selected"] unconditionally and
        raised KeyError on probability cards, which have no run ladder."""
        _push("R_100", _HIGH_TAPE)
        res = ai_copilot.ask("what is the probability of over 4 on R_100")
        assert "selected" not in res["data"] or res["data"]["selected"] == []
        assert res["answer"]

    def test_run_request_still_returns_a_plan(self):
        _push("R_100", _LOW_TAPE)
        res = ai_copilot.ask("over 4 under 7, 2 runs")
        assert res["intent"] == "MISSION_PLAN"
        assert "selected" in res["data"]

    def test_route_accepts_probability_kind(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _HIGH_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "question": "probability of over 4",
                "kind": "probability",
                "symbols": ["R_100"],
            })
        assert res.status_code == 200
        body = res.json()
        assert body["kind"] == "PROBABILITY"
        assert body["probability"] is not None

    def test_explicit_plan_kind_overrides_probability_wording(self):
        """Clicking "Trade plan" must win over wording that reads like a
        probability question - the user's explicit choice is authoritative."""
        _push("R_100", _LOW_TAPE)
        card = MissionPlanner().plan(
            "what is the probability of over 4 and under 7", ["R_100"],
            force="plan")
        assert card.get("kind") != "PROBABILITY"
        assert "selected" in card

    def test_route_plan_kind_still_returns_a_plan(self):
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _LOW_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "question": "what is the probability of over 4 and under 7",
                "kind": "plan",
                "symbols": ["R_100"],
            })
        assert res.status_code == 200
        assert res.json().get("kind") != "PROBABILITY"

    def test_explicit_probability_kind_honours_a_named_market(self):
        """Regression: the explicit probability route ignored the market named
        in the question, so "over 4 on R_100" was answered with whichever
        other market happened to read highest."""
        from fastapi.testclient import TestClient
        from app.main import app

        _push("R_100", _FLAT_TAPE)
        _push("R_50", _HIGH_TAPE)
        with TestClient(app) as c:
            res = c.post("/ai-copilot/mission", json={
                "question": "what is the probability of over 4 on R_100",
                "kind": "probability",
                "symbols": ["R_100", "R_50"],
            })
        assert res.status_code == 200
        assert res.json()["best"]["symbol"] == "R_100"