"""Sniper department tests.

The department is defined by its RESTRAINT as much as its aim, so these tests
push hard on both: it must fire when a target is genuinely proven, and it must
hold fire - loudly, with numbers - on a fair tape, a thin tape, a one-window
fluke, and a negative-EV contract. One shot, one run, always.
"""
from datetime import datetime, timezone

import pytest

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.sniper import (
    CONFIRM_WINDOWS,
    MATCHES_MIN_EDGE_PP,
    MIN_SAMPLE,
    SHOT_RUNS,
    SHOT_STAKE,
    TARGETS,
    Sniper,
    _matches_row,
    _shot_math,
)
from app.services.tick_recorder import tick_recorder


def _push(symbol, digits, provider="deriv_live"):
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for d in digits:
        tick_queue.push(Tick(
            symbol=symbol, quote=float(d), raw={"digit": int(d)},
            provider=provider, timestamp=datetime.now(timezone.utc)))


# Uniform biases - the windows read the most RECENT ticks, so a bias that lives
# only in older ticks is correctly judged unstable.
_HOT7 = ["7", "7", "7", "0", "1", "2", "3", "4", "5", "6"] * 40
_OVER4 = ["5", "6", "7", "8", "9", "5", "6", "0", "1", "2"] * 40
_UNDER6 = ["0", "1", "2", "3", "4", "5", "0", "6", "7", "8"] * 40
_FLAT = [str(i) for i in range(10)] * 40
_THIN = ["7", "8", "9"] * 6


class TestTargets:
    """The department engages exactly three contracts - no more."""

    def test_mission_is_exactly_matches_over4_under6(self):
        assert [t["key"] for t in TARGETS] == ["MATCHES", "OVER_4", "UNDER_6"]
        pairs = {(t["side"], t["barrier"]) for t in TARGETS}
        assert pairs == {("MATCHES", None), ("OVER", 4), ("UNDER", 6)}

    def test_scan_reports_only_the_three_targets(self):
        _push("S_A", _FLAT)
        out = Sniper().scan(["S_A"])
        assert [t["target"] for t in out["targets"]] == [
            "MATCHES", "OVER_4", "UNDER_6"]

    def test_no_other_contract_family_is_ever_scanned(self):
        _push("S_A", _HOT7)
        out = Sniper().scan(["S_A"])
        blob = repr(out)
        for banned in ("DIFFERS", "DIGITDIFF", "ODD", "EVEN"):
            assert banned not in blob


class TestOneShotOneRun:
    """One pull of the trigger is one run. No ladder, ever."""

    def test_shot_is_always_a_single_run(self):
        assert SHOT_RUNS == 1
        assert SHOT_STAKE == 1.0

    def test_shot_math_states_both_outcomes(self):
        m = _shot_math(0.30, 10.0)
        assert m["runs"] == 1
        assert m["p_win"] == 0.30
        assert m["p_miss"] == 0.70          # the losing case is stated
        assert m["win_credit"] == 9.0       # $1 at 10x pays $9 profit
        assert m["loss_debit"] == 1.0
        assert m["ev_per_run"] == pytest.approx(2.0)

    def test_shot_math_on_a_losing_contract_is_negative(self):
        m = _shot_math(0.10, 2.0)           # 10% at 2x is a losing bet
        assert m["ev_per_run"] < 0

    def test_no_martingale_or_ladder_fields_in_the_card(self):
        """The doctrine MENTIONS "no ladder"; the data must not CONTAIN one."""
        _push("S_A", _OVER4)
        out = Sniper().scan(["S_A"])

        def keys(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield k
                    yield from keys(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    yield from keys(v)

        found = set(keys(out))
        for banned in ("martingale", "ladder", "recovery_step2", "steps",
                       "base_stake", "budget"):
            assert banned not in found
        # And no nested dict masquerading as a staking plan.
        for t in out["targets"]:
            if t["shot"]:
                assert set(t["shot"]["one_shot"]) >= {"runs", "stake", "p_win"}

    def test_shot_has_no_second_run_or_scale_in(self):
        _push("S_A", _OVER4)
        out = Sniper().scan(["S_A"])
        for t in out["targets"]:
            if t["shot"]:
                assert t["shot"]["one_shot"]["runs"] == 1
                assert t["shot"]["one_shot"]["stake"] == 1.0
                # There is exactly one shot per target - never a list of them.
                assert isinstance(t["shot"], dict)


class TestFiring:
    """It must fire when a target is genuinely proven on the tape."""

    def test_matches_fires_on_an_overweight_digit(self):
        _push("S_7", _HOT7)
        out = Sniper().scan(["S_7"])
        t = [x for x in out["targets"] if x["target"] == "MATCHES"][0]
        assert t["acquired"] is True
        shot = t["shot"]
        assert shot["side"] == "MATCHES"
        assert shot["entry"]["digit"] == 7
        assert shot["row"]["payout"] == 10.0
        assert shot["one_shot"]["runs"] == 1

    def test_over4_fires_on_a_high_digit_tape(self):
        _push("S_O", _OVER4)
        out = Sniper().scan(["S_O"])
        t = [x for x in out["targets"] if x["target"] == "OVER_4"][0]
        assert t["acquired"] is True
        shot = t["shot"]
        assert shot["barrier"] == 4
        assert shot["entry"]["digit"] == 5      # OVER 4 -> entry digit 5
        assert shot["row"]["payout"] == 2.0

    def test_under6_fires_on_a_low_digit_tape(self):
        _push("S_U", _UNDER6)
        out = Sniper().scan(["S_U"])
        t = [x for x in out["targets"] if x["target"] == "UNDER_6"][0]
        assert t["acquired"] is True
        shot = t["shot"]
        assert shot["barrier"] == 6
        assert shot["entry"]["digit"] == 5      # UNDER 6 -> entry digit 5
        assert shot["row"]["payout"] == pytest.approx(10.0 / 6, rel=1e-3)

    def test_verdict_is_target_acquired_when_a_target_fires(self):
        _push("S_7", _HOT7)
        out = Sniper().scan(["S_7"])
        assert out["verdict"] == "TARGET_ACQUIRED"
        assert "MATCHES" in out["acquired"]


class TestRestraint:
    """The department's real job: not firing when it should not."""

    def test_fair_tape_holds_fire(self):
        _push("S_F", _FLAT)
        out = Sniper().scan(["S_F"])
        assert out["verdict"] == "HOLDING_FIRE"
        assert out["acquired"] == []
        for t in out["targets"]:
            assert t["acquired"] is False

    def test_thin_tape_holds_fire_on_sample(self):
        _push("S_T", _THIN)
        out = Sniper().scan(["S_T"])
        assert out["verdict"] != "TARGET_ACQUIRED"
        for t in out["targets"]:
            if t["best"]:
                assert t["best"]["rules"]["sample_ok"] is False

    def test_a_bias_in_old_ticks_only_is_not_a_target(self):
        """The one-window fluke that caused the phantom-edge session.

        The bias lives in the FIRST 100 ticks; every confirm window reads the
        most recent ticks, so the target must be refused as unstable.
        """
        biased = ["7", "7", "7", "0", "1", "2", "3", "4", "5", "6"] * 10   # 100
        flat = [str(i) for i in range(10)] * 30                             # 300
        _push("S_OLD", biased + flat)
        out = Sniper().scan(["S_OLD"])
        t = [x for x in out["targets"] if x["target"] == "MATCHES"][0]
        assert t["acquired"] is False
        if t["best"]:
            assert t["best"]["rules"]["multi_window_stable"] is False

    def test_holding_fire_still_reports_every_target_with_numbers(self):
        _push("S_F", _FLAT)
        out = Sniper().scan(["S_F"])
        assert out["verdict"] == "HOLDING_FIRE"
        for t in out["targets"]:
            # Even with no shot, the measurement is published.
            assert t["best"] is not None
            assert t["best"]["row"]["observed_pct"] is not None
            assert t["best"]["row"]["edge_pp"] is not None
        assert out["note"]

    def test_no_tape_is_reported_not_invented(self):
        tick_queue.clear("S_NONE")
        tick_recorder.purge("S_NONE")
        out = Sniper().scan(["S_NONE"])
        assert out["verdict"] == "NO_TARGET"
        for t in out["targets"]:
            assert t["acquired"] is False
            assert t["markets_scanned"] == 0

    def test_empty_market_list_does_not_crash(self):
        out = Sniper().scan([])
        assert out["verdict"] == "NO_TARGET"
        assert out["acquired"] == []

    def test_one_broken_market_does_not_stop_the_scan(self):
        _push("S_OK", _HOT7)
        out = Sniper().scan(["S_OK", "S_DOES_NOT_EXIST"])
        assert out["verdict"] == "TARGET_ACQUIRED"


class TestGates:
    """Every rule is real, and a failed rule blocks the shot."""

    def test_confirm_windows_are_the_three_headline_windows(self):
        assert CONFIRM_WINDOWS == (100, 250, 1000)

    def test_min_sample_floor_is_real(self):
        assert MIN_SAMPLE >= 100

    def test_matches_is_judged_on_the_wilson_lower_bound(self):
        """A raw frequency a short tape can fluke must not be enough alone."""
        n = 110
        counts = [0] * 10
        counts[3] = 15          # 13.6% raw - above the 10% fair share
        row = _matches_row(counts, n, 3)
        assert row["observed_pct"] > 10.0
        # The lower bound is the strict test, and it is what the gate reads.
        assert row["wilson_lb_pct"] < row["observed_pct"]
        assert row["wilson_edge_pp"] == pytest.approx(
            row["wilson_lb_pct"] - 10.0, abs=0.01)

    def test_negative_ev_cannot_fire(self):
        _push("S_F", _FLAT)
        out = Sniper().scan(["S_F"])
        for t in out["targets"]:
            if t["best"]:
                assert not (t["best"]["killed"]
                            and t["best"]["row"]["ev"] <= 0)

    def test_every_rule_is_reported_per_target(self):
        _push("S_F", _FLAT)
        out = Sniper().scan(["S_F"])
        for t in out["targets"]:
            if t["best"]:
                assert set(t["best"]["rules"]) == {
                    "sample_ok", "raw_edge_ok", "wilson_edge_ok",
                    "positive_ev", "multi_window_stable"}


class TestCard:
    """The one-screen card: a shot, or the reason for holding fire."""

    def test_card_fires_with_a_single_run_stated(self):
        _push("S_7", _HOT7)
        c = Sniper().card(["S_7"])
        assert "TARGET_ACQUIRED" in c["card"]
        assert "1 run" in c["card"]
        assert "MATCHES digit 7" in c["card"]

    def test_card_on_hold_names_the_failed_rule(self):
        _push("S_F", _FLAT)
        c = Sniper().card(["S_F"])
        assert "HOLDING_FIRE" in c["card"]
        assert "failed:" in c["card"] or "NO TAPE" in c["card"]

    def test_card_never_claims_certainty(self):
        _push("S_7", _HOT7)
        c = Sniper().card(["S_7"])
        blob = c["card"].lower()
        for banned in ("guaranteed", "sure thing", "cannot lose", "risk free"):
            assert banned not in blob

    def test_card_is_advisory_and_carries_no_order_payload(self):
        _push("S_7", _HOT7)
        c = Sniper().card(["S_7"])
        blob = repr(c).lower()
        for banned in ("place_payload", "scheme_entry", "contract_id",
                       "buy", "execute"):
            assert banned not in blob


class TestRoutes:
    def test_sniper_routes_are_registered(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            assert c.get("/sniper/scan").status_code == 200
            assert c.get("/sniper/card").status_code == 200
            assert c.get("/sniper/targets").status_code == 200

    def test_targets_route_publishes_standing_orders(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            body = c.get("/sniper/targets").json()
        assert body["department"] == "SNIPER"
        assert body["mission"] == ["MATCHES", "OVER_4", "UNDER_6"]
        assert body["shot_runs"] == 1
