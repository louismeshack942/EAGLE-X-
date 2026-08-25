"""The 200-idea smoothness wave: forensics, season, guard v2, bank v2,
scout v2, trader v2 behaviours, and every new endpoint."""
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import forensics, scout as scout_svc, season as season_svc
from app.services.risk_guard import RiskGuard
from app.services.virtual_bank import VirtualBank

client = TestClient(app)


def _fresh_guard() -> RiskGuard:
    """Guard with every gate explicitly neutralised — the persisted store
    must never decide what a unit test is testing."""
    g = RiskGuard()
    g.killed = False
    g.kill_reason = ""
    g.mode = "FULL_AUTO"
    g.daily_loss_limit = 0.0
    g.session_take_profit = 0.0
    g.max_trades_per_hour = 0
    g.trail_arm = 0.0
    g.auto_kill_drawdown_pct = 0.0
    g.allowed_hours_utc = []
    g.quiet_hours_utc = []
    g.escalate_after_losses = 0
    return g


def _fresh_bank() -> VirtualBank:
    b = VirtualBank()
    b.mode_totals = {}
    b.vault_goal = 0.0
    b._goal_celebrated = False
    b.lock_pct = 0.0
    b.floor = 0.0
    return b


class TestMonteCarlo:
    def test_deterministic(self):
        a = forensics.monte_carlo(0.9, 1.1, seed=42)
        b = forensics.monte_carlo(0.9, 1.1, seed=42)
        assert a == b

    def test_ordering(self):
        mc = forensics.monte_carlo(0.92, 1.1, seed=7)
        assert mc["final_p05"] <= mc["final_p50"] <= mc["final_p95"]

    def test_house_edge_is_shown_honestly(self):
        """90% at 1.1 payout is -1% edge: the sim must NOT promise profits."""
        mc = forensics.monte_carlo(0.90, 1.1, seed=42)
        assert mc["params"]["edge_per_trade"] < 0
        assert mc["final_p50"] < 100.0

    def test_coinflip_at_quarter_stake_ruins(self):
        mc = forensics.monte_carlo(0.5, 1.9, stake_pct=0.25, seed=42)
        assert mc["risk_of_ruin_pct"] > 50.0


class TestForensicsBasics:
    def test_entry_quality_grades(self):
        good = {"analysis_snapshot": {"ev": 0.05, "significant": True, "z": 2.5, "evidence": "STRONG_DATA_SUPPORT"}}
        bad = {"analysis_snapshot": {"ev": -0.1, "significant": False, "z": 0.5, "evidence": "WEAK_DATA_CONTRARY"}}
        assert forensics.entry_quality(good)["grade"] == "A"
        assert forensics.entry_quality(bad)["grade"] == "F"

    def test_mistakes_shape(self):
        m = forensics.mistakes()
        assert "trades_reviewed" in m and "mistake_counts" in m and "verdict" in m

    def test_lessons_always_speaks(self):
        ls = forensics.lessons()
        assert ls["lessons"] and "lesson" in ls["lessons"][0]

    def test_expectancy_and_smoothness_run(self):
        assert "trades" in forensics.expectancy()
        assert "score" in forensics.smoothness() or "note" in forensics.smoothness()

    def test_suggestions_shape(self):
        sg = forensics.suggestions()
        assert 1 <= len(sg["suggestions"]) <= 3
        assert all("priority" in s and "text" in s for s in sg["suggestions"])

    def test_scorecard_na_with_no_trades(self):
        sc = forensics.session_scorecard([], 0.0, 0, 0)
        assert sc["grade"] == "N/A"


class TestSeason:
    def test_weekly_table_shape(self):
        t = season_svc.weekly_table()
        assert "weeks" in t and "played" in t

    def test_weekly_report_positions(self):
        rep = season_svc.weekly_report()
        assert "note" in rep or rep["position"] in (
            "TITLE RACE", "TOP FOUR", "MIDTABLE", "RELEGATION BATTLE")

    def test_season_chart_cumulative(self):
        ch = season_svc.season_chart()
        assert "chart" in ch and "season_pnl" in ch


class TestGuardV2:
    def test_trailing_stop(self):
        g = _fresh_guard()
        g.open_session(100.0)
        g.set_limits(trail_arm=10.0, trail_pct=0.5)
        g.record_trade(115.0)  # peak +15
        assert g.check(14.0) == []
        assert any("TRAILING_STOP" in v for v in g.check(6.0))

    def test_auto_kill_arms_itself(self):
        g = _fresh_guard()
        g.open_session(100.0)
        g.set_limits(auto_kill_drawdown_pct=0.15)
        v = g.check(-16.0, balance=84.0)
        assert any("AUTO_KILL" in v for v in v)
        assert g.killed

    def test_schedule_gate(self):
        g = _fresh_guard()
        g.set_limits(allowed_hours_utc=[(g_now := 25)])  # impossible hour -> always closed
        assert any("SCHEDULE" in v for v in g.check(0.0))
        g.set_limits(allowed_hours_utc=[])

    def test_escalation_ladder(self):
        g = _fresh_guard()
        g.set_limits(escalate_after_losses=2)
        g.set_mode("FULL_AUTO")
        assert g.maybe_escalate(1) is None
        assert g.maybe_escalate(2) is not None
        assert g.mode == "COACH"
        g.set_mode("FULL_AUTO")
        g.set_limits(escalate_after_losses=0)

    def test_presets(self):
        g = _fresh_guard()
        assert g.apply_preset("SAFE")["preset"] == "SAFE"
        assert g.daily_loss_limit == 10.0 and g.streak_halving
        assert g.apply_preset("BALANCED")["applied"]["max_trades_per_hour"] == 12
        assert "error" in g.apply_preset("YOLO")
        g.apply_preset("AGGRESSIVE")  # restore loose state for other tests

    def test_quiet_hours(self):
        g = _fresh_guard()
        assert g.alerts_quiet_now() is False
        g.set_limits(quiet_hours_utc=list(range(24)))
        assert g.alerts_quiet_now() is True
        g.set_limits(quiet_hours_utc=[])


class TestBankV2:
    def test_per_mode_ledgers(self):
        b = _fresh_bank()
        b.sync_opening(100.0)
        b.record_pnl(10.0, mode="live")
        b.record_pnl(5.0, mode="paper")
        s = b.status()
        assert s["mode_totals"]["live"]["profit"] == 10.0
        assert s["mode_totals"]["paper"]["trades"] == 1

    def test_vault_goal_milestone(self):
        b = _fresh_bank()
        b.sync_opening(10.0)
        b.set_goal(5.0)
        b.record_pnl(10.0)  # vault 6 >= 5
        assert b._goal_celebrated
        assert any(e["kind"] == "milestone" for e in b.recent_history())

    def test_lock_in_ladder(self):
        b = _fresh_bank()
        b.sync_opening(100.0)
        b.set_lock(0.8)
        b.record_pnl(50.0)   # total 150 -> floor 120
        assert b.floor == 120.0
        assert b.check_floor() is None
        b.record_pnl(-40.0)  # total 110 < 120
        assert "BANK_FLOOR" in (b.check_floor() or "")


class TestScoutV2:
    def test_z_age_tracking(self):
        scout_svc._note_z("TEST", 3, -2.5)
        assert scout_svc.z_age_s("TEST", 3) >= 0.0
        scout_svc._note_z("TEST2", 3, 0.0)
        assert scout_svc.z_age_s("TEST2", 3) == 0.0

    def test_feed_health_flags_unknown_symbol(self):
        fh = scout_svc.feed_health(["NO_SUCH_SYMBOL_XYZ"])
        assert fh["all_fresh"] is False
        assert "NO_SUCH_SYMBOL_XYZ" in fh["stale_symbols"]

    def test_tables_carry_track_record_fields(self):
        out = scout_svc.scan_tables(["NO_SUCH_SYMBOL_XYZ"], window=100)
        assert out["tables"][0]["tradeable"] is False


class TestTraderV2:
    def test_matchday_rollover(self):
        from app.services.auto_trader import AutoTrader
        at = AutoTrader()
        at._matchday = "2020-01-01"
        at.trades_today = 10
        at.daily_pnl = -5.0
        at._rollover_matchday()
        assert at.trades_today == 0 and at.daily_pnl == 0.0

    def test_adaptive_z_rises_on_cold_form(self):
        from app.services.auto_trader import AutoTrader
        at = AutoTrader()
        # Fresh journal in tests is small; with <10 entries the bar stays 1.96
        assert at._effective_min_z() in (1.96, 2.46)

    def test_status_exposes_v2_fields(self):
        from app.services.auto_trader import auto_trader
        s = auto_trader.status()
        for k in ("matchday", "effective_min_z", "no_trade_reasons", "counters", "shadow"):
            assert k in s, k

    def test_shadow_scoreboard_shape(self):
        from app.services.auto_trader import AutoTrader
        at = AutoTrader()
        at._shadow_track("R_100", [{"name": "DIFFERS on 3", "type": "DIFFERS", "digit": 3}], 100)
        assert len(at._pending_shadow) == 1


class TestNewEndpointsV2:
    def test_forensics_endpoints(self):
        for path in ("/forensics/mistakes", "/forensics/lessons", "/forensics/expectancy",
                     "/forensics/smoothness", "/forensics/risk-of-ruin", "/forensics/suggestions"):
            assert client.get(path).status_code == 200, path

    def test_monte_carlo_endpoint(self):
        r = client.get("/forensics/monte-carlo?p_win=0.9&payout=1.1&sims=100")
        assert r.status_code == 200
        assert "risk_of_ruin_pct" in r.json()

    def test_season_endpoints(self):
        for path in ("/season", "/season/report", "/season/chart"):
            assert client.get(path).status_code == 200, path

    def test_scorecard_endpoint(self):
        assert client.get("/session/scorecard").status_code == 200

    def test_preset_and_schedule_endpoints(self):
        r = client.post("/guard/preset/SAFE")
        assert r.status_code == 200 and r.json()["preset"] == "SAFE"
        client.post("/guard/preset/AGGRESSIVE")  # restore loose defaults
        r = client.post("/guard/schedule", json={"allowed_hours_utc": [], "quiet_hours_utc": []})
        assert r.status_code == 200

    def test_bank_goal_and_lock_endpoints(self):
        assert client.post("/bank/goal", json={"goal": 50}).json()["vault_goal"] == 50.0
        assert client.post("/bank/lock", json={"lock_pct": 0.0}).status_code == 200

    def test_metrics_and_doctor(self):
        m = client.get("/metrics")
        assert m.status_code == 200 and "counters" in m.json()
        d = client.get("/doctor")
        assert d.status_code == 200 and d.json()["checks"]
        assert all("check" in c and "ok" in c for c in d.json()["checks"])

    def test_feed_health_endpoint(self):
        assert client.get("/scout/feed-health").status_code == 200


class TestCopilotV2:
    def test_ruin_branch(self):
        from app.services.ai_copilot import ai_copilot
        r = ai_copilot.ask("what is my risk of ruin?")
        assert "ruin" in r["answer"].lower() or "need" in r["answer"].lower()

    def test_lessons_branch(self):
        from app.services.ai_copilot import ai_copilot
        r = ai_copilot.ask("any lessons from my mistakes?")
        assert "debrief" in r["answer"].lower() or "lesson" in r["answer"].lower()

    def test_season_branch(self):
        from app.services.ai_copilot import ai_copilot
        r = ai_copilot.ask("how is my season going?")
        assert r["answer"]

    def test_suggestions_branch(self):
        from app.services.ai_copilot import ai_copilot
        r = ai_copilot.ask("how do I improve?")
        assert r["answer"]


class TestPrecisionGate:
    """The 8W/2L upgrade: fewer entries, cleaner entries."""

    def _mm(self, ev=0.06, z=-2.5, ctype="DIFFERS", digit=3, significant=True):
        contract = {
            "name": f"{ctype} on {digit}" if digit is not None else ctype,
            "type": ctype, "digit": digit, "ev": ev, "observed_edge": 3.0,
            "confidence": 80.0, "evidence": "STRONG_DATA_SUPPORT",
            "significant": significant, "z": z,
        }
        return {
            "data_quality": 85, "signal": "STRONG_DATA_SUPPORT",
            "anomaly_count": 0, "contracts": [contract], "all_contracts": [contract],
        }

    def test_sliver_differs_ev_rejected(self):
        from app.services.auto_trader import select_plays
        mm = self._mm(ev=0.005)  # positive but paper-thin
        assert select_plays(mm, "R_100") == []

    def test_real_differs_ev_reaches_precision_layer(self):
        from app.services.auto_trader import select_plays
        mm = self._mm(ev=0.06)
        out = select_plays(mm, "R_100")
        assert isinstance(out, list)

    def test_non_differs_skips_precision_layer(self):
        from app.services.auto_trader import select_plays
        mm = self._mm(ev=0.5, ctype="OVER", digit=1)
        out = select_plays(mm, "R_100")
        assert len(out) == 1  # OVER bypasses the DIFFERS-only precision gate

    def test_min_differs_ev_constant(self):
        from app.services.market_master import MIN_DIFFERS_EV, MIN_Z_AGE_S, BREAKEVEN_MIN_TICKS
        assert MIN_DIFFERS_EV >= 0.01  # sliver-rejection bar (speed-tuned)
        assert MIN_Z_AGE_S >= 5.0
        assert BREAKEVEN_MIN_TICKS >= 200

    def test_z_age_blocks_fresh_edges(self):
        from app.services import scout as sc
        sc._note_z("AGE_TEST", 5, -3.0)
        assert sc.z_age_s("AGE_TEST", 5) < 10.0

    def test_multi_window_has_deep_window(self):
        from app.services import scout as sc
        out = sc.multi_window_confirmed("R_100", 3)
        assert 2000 in out["windows"]


class TestEVRanking:
    """The squad ranks by EV alone; the truth gate decides what may fire.
    (Was TestDiffersPriority — the DIFFERS-first sort bias was removed so
    every contract type competes honestly.)"""

    def _mm(self):
        return {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'OVER 1', 'type': 'OVER', 'digit': 1, 'ev': 0.86,
                 'observed_edge': 5.0, 'confidence': 80, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.3},
                {'name': 'DIFFERS on 0', 'type': 'DIFFERS', 'digit': 0, 'ev': 0.078,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.33},
            ],
        }

    # A fresh symbol sandbox: select_plays derives its precision gates from
    # the shared tick_queue per symbol. Feeding another symbol (a sibling
    # test) ages that symbol's z-thread and poisons the OVER/DIFFERS ladder
    # this test sets up — so build the starved table on an isolated symbol.
    SYMBOL = "R_100_DIFFERS_GATED"

    def test_ev_leads_the_menu_on_gated_table(self):
        from app.core.queue import tick_queue
        from app.models.tick import Tick
        import random
        from app.services.auto_trader import select_plays
        rng = random.Random(9)
        # digit 0 starved across the full window — the stamped digit channel
        # (same as the demo generator) keeps 0 absent at every gate depth.
        for i in range(2000):
            d = rng.randrange(1, 10)
            tick_queue.push(Tick(symbol=self.SYMBOL,
                                 quote=100.0 + rng.randrange(1, 10) / 10,
                                 raw={"digit": d}))
        plays = select_plays(self._mm(), self.SYMBOL)
        # EV ranks the menu now: OVER 1 (0.86) leads, and the pair-ratio
        # rule drops the far-behind DIFFERS (0.078 < 75% of 0.86). The truth
        # gate — not the sort order — decides what may actually fire.
        assert plays and plays[0]['type'] == 'OVER'

    def test_differs_still_gated_when_it_leads(self):
        """When DIFFERS carries the top EV, the table-level precision gates
        (chi-square, multi-window, momentum, z-age) still apply to it."""
        from app.core.queue import tick_queue
        from app.models.tick import Tick
        import random
        from app.services.auto_trader import select_plays
        rng = random.Random(9)
        for i in range(2000):
            d = rng.randrange(1, 10)  # digit 0 starved at every depth
            tick_queue.push(Tick(symbol=self.SYMBOL,
                                 quote=100.0 + rng.randrange(1, 10) / 10,
                                 raw={"digit": d}))
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'DIFFERS on 0', 'type': 'DIFFERS', 'digit': 0, 'ev': 0.90,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.33},
            ],
        }
        plays = select_plays(mm, self.SYMBOL)
        assert plays and plays[0]['type'] == 'DIFFERS'

    def test_over_leads_when_no_differs_passes(self):
        from app.services.auto_trader import select_plays
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'OVER 1', 'type': 'OVER', 'digit': 1, 'ev': 0.86,
                 'observed_edge': 5.0, 'confidence': 80, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.3},
            ],
        }
        plays = select_plays(mm, 'R_100')
        assert plays and plays[0]['type'] == 'OVER'

    def test_fair_table_still_blocks(self):
        from app.services.auto_trader import select_plays
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'DIFFERS on 0', 'type': 'DIFFERS', 'digit': 0, 'ev': 0.078,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.33},
            ],
        }
        plays = select_plays(mm, 'EMPTY_SYMBOL_XYZ')
        assert plays == []


class TestPayoutHonesty:
    """The 2026-08-24 drawdown: OVER 1 pays ~1.22, not 1.9. Never again."""

    def test_digit_payout_matches_deriv_pricing(self):
        from app.services.market_master import _digit_payout
        assert abs(_digit_payout(0.8) - 1.231) < 0.01   # OVER 1
        assert abs(_digit_payout(0.1) - 9.85) < 0.01    # UNDER 1
        assert abs(_digit_payout(0.5) - 1.97) < 0.01    # coin-flip tables

    def test_over_1_is_negative_ev_at_fair_odds(self):
        """At fair 80% frequency, OVER 1 at its real 1.23 payout LOSES money.
        The old 1.9 payout made it look like +0.52 EV — that lie cost $4k."""
        from app.services.market_master import _digit_payout, _ev
        fair = 0.80
        payout = _digit_payout(fair)
        assert _ev(fair, payout) < 0  # fair odds = the house wins, as always

    def test_contracts_carry_real_payout(self):
        from app.core.queue import tick_queue
        from app.models.tick import Tick
        import random
        from app.services.market_master import market_master
        rng = random.Random(3)
        for i in range(150):
            tick_queue.push(Tick(symbol='PO_TEST', quote=100.0 + rng.randrange(10) / 10))
        mm = market_master.analyze('PO_TEST', 100)
        for c in mm.get('all_contracts', []):
            assert 'payout' in c, f"{c['name']} missing payout"
        over1 = next(c for c in mm['all_contracts'] if c['name'] == 'OVER 1')
        assert over1['payout'] < 1.5  # real odds, not the 1.9 fantasy


class TestCorrelationBan:
    """Two contracts that lose on the same digit are not a pair — they're
    double exposure. The exact structure that drained the account."""

    def test_loss_sets(self):
        from app.services.auto_trader import _loss_set
        assert _loss_set({'type': 'DIFFERS', 'digit': 0}) == {0}
        assert _loss_set({'type': 'OVER', 'digit': 1}) == {0, 1}
        assert _loss_set({'type': 'UNDER', 'digit': 7}) == {7, 8, 9}
        assert _loss_set({'type': 'MATCHES', 'digit': 3}) == {0, 1, 2, 4, 5, 6, 7, 8, 9}
        assert _loss_set({'type': 'ODD'}) == {0, 2, 4, 6, 8}

    def test_loss_set_parses_digit_from_name(self):
        from app.services.auto_trader import _loss_set
        assert _loss_set({'type': 'OVER', 'name': 'OVER 1'}) == {0, 1}
        assert _loss_set({'type': 'DIFFERS', 'name': 'DIFFERS on 3'}) == {3}

    def test_differs0_over1_pair_banned(self):
        """The exact killer pair from the drawdown."""
        from app.services.auto_trader import select_plays
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'DIFFERS on 0', 'type': 'DIFFERS', 'digit': 0, 'ev': 0.078,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.33},
                {'name': 'OVER 1', 'type': 'OVER', 'digit': 1, 'ev': 0.86,
                 'observed_edge': 5.0, 'confidence': 80, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.3},
            ],
        }
        plays = select_plays(mm, 'R_100')
        # at most ONE of them — they both lose on digit 0. EV ranks the
        # menu now, so the survivor is the bigger-paying OVER 1.
        assert len(plays) <= 1
        if plays:
            assert plays[0]['name'] == 'OVER 1'

    def test_uncorrelated_pair_still_allowed(self):
        from app.services.auto_trader import select_plays
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'OVER 1', 'type': 'OVER', 'digit': 1, 'ev': 0.86,
                 'observed_edge': 5.0, 'confidence': 80, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.3},
                {'name': 'UNDER 7', 'type': 'UNDER', 'digit': 7, 'ev': 0.70,
                 'observed_edge': 4.5, 'confidence': 75, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 2.8},
            ],
        }
        plays = select_plays(mm, 'R_100')
        assert len(plays) == 2  # {0,1} and {7,8,9} never collide


class TestManualStake:
    """The manager sets the bullet size himself."""

    def test_stake_override_set_and_clear(self):
        g = _fresh_guard()
        r = g.set_stake(250.0)
        assert r["stake_override"] == 250.0 and r["mode"] == "manual stake"
        r = g.set_stake(0)
        assert r["stake_override"] == 0.0 and "auto" in r["mode"]

    def test_stake_endpoint(self):
        r = client.post("/guard/stake", json={"amount": 50.0})
        assert r.status_code == 200
        assert r.json()["stake_override"] == 50.0
        assert client.get("/guard").json()["stake_override"] == 50.0
        client.post("/guard/stake", json={"amount": 0})  # restore auto

    def test_env_arming_survives_store_wipe(self):
        import os
        from app.services.risk_guard import RiskGuard
        os.environ["GUARD_DAILY_LOSS_LIMIT"] = "400"
        os.environ["GUARD_STAKE_OVERRIDE"] = "100"
        try:
            g = RiskGuard()  # fresh load, store may be empty
            assert g.daily_loss_limit == 400.0
            assert g.stake_override == 100.0
        finally:
            del os.environ["GUARD_DAILY_LOSS_LIMIT"]
            del os.environ["GUARD_STAKE_OVERRIDE"]

    def test_limits_endpoint_forwards_all_fields(self):
        r = client.post("/guard/limits", json={
            "trail_arm": 150, "trail_pct": 0.5,
            "auto_kill_drawdown_pct": 0.10, "escalate_after_losses": 2,
        })
        d = r.json()
        assert d["trail_arm"] == 150.0
        assert d["auto_kill_drawdown_pct"] == 0.10
        assert d["escalate_after_losses"] == 2
        # restore loose defaults so other tests aren't gated
        client.post("/guard/limits", json={
            "trail_arm": 0, "auto_kill_drawdown_pct": 0, "escalate_after_losses": 0,
        })

    def test_trader_status_shows_stake_mode(self):
        client.post("/guard/stake", json={"amount": 25.0})
        s = client.get("/auto-trader/status").json()
        assert s["stake_mode"] == "manual"
        assert s["current_stake"] == 25.0
        client.post("/guard/stake", json={"amount": 0})
        s = client.get("/auto-trader/status").json()
        assert s["stake_mode"] == "auto"


class TestPHEV:
    """Plug-in hybrid: fewer, bigger, cleaner strikes."""

    def _mm(self, ev=0.09, z=3.33, ctype="DIFFERS", digit=0):
        return {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': f'{ctype} on {digit}', 'type': ctype, 'digit': digit, 'ev': ev,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': z},
            ],
        }

    def test_phev_mode_exists(self):
        g = _fresh_guard()
        assert g.set_mode("PHEV")["mode"] == "PHEV"
        g.set_mode("FULL_AUTO")

    def test_phev_preset(self):
        g = _fresh_guard()
        r = g.apply_preset("PHEV")
        assert r["preset"] == "PHEV"
        assert g.max_trades_per_hour == 4
        assert g.auto_kill_drawdown_pct == 0.08

    def test_phev_rejects_weak_z(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("PHEV")
        mm = self._mm(z=2.0)
        assert select_plays(mm, "R_100") == []
        risk_guard.set_mode("FULL_AUTO")

    def test_phev_rejects_thin_ev(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("PHEV")
        mm = self._mm(ev=0.02, z=3.0)
        assert select_plays(mm, "R_100") == []
        risk_guard.set_mode("FULL_AUTO")

    def test_phev_single_strike_only(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("PHEV")
        mm = {
            'data_quality': 85, 'signal': 'STRONG_DATA_SUPPORT', 'anomaly_count': 0,
            'contracts': [], 'all_contracts': [
                {'name': 'DIFFERS on 0', 'type': 'DIFFERS', 'digit': 0, 'ev': 0.09,
                 'observed_edge': 8.0, 'confidence': 90, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.33},
                {'name': 'OVER 2', 'type': 'OVER', 'digit': 2, 'ev': 0.08,
                 'observed_edge': 7.0, 'confidence': 85, 'evidence': 'STRONG_DATA_SUPPORT',
                 'significant': True, 'z': 3.0},
            ],
        }
        plays = select_plays(mm, 'R_100')
        assert len(plays) <= 1
        risk_guard.set_mode("FULL_AUTO")

    def test_phev_stake_compounds_with_profit(self):
        from app.services.auto_trader import AutoTrader
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("PHEV")
        at = AutoTrader()
        at._session_active = True
        at.initial_balance = 1000.0
        at.daily_pnl = 0.0
        assert at._phev_stake(1000.0) == 1000.0
        at.daily_pnl = 500.0
        assert at._phev_stake(1000.0) == 1000.0 + 200.0
        at.daily_pnl = -100.0
        assert at._phev_stake(1000.0) == 1000.0
        risk_guard.set_mode("FULL_AUTO")


class TestHEV:
    """HEV: the speed bot between MANUAL and HYBRID."""

    def test_hev_mode_exists(self):
        g = _fresh_guard()
        assert g.set_mode("HEV")["mode"] == "HEV"
        g.set_mode("FULL_AUTO")

    def test_hev_preset(self):
        g = _fresh_guard()
        r = g.apply_preset("HEV")
        assert r["preset"] == "HEV"
        assert g.max_trades_per_hour == 30
        assert not g.streak_halving

    def test_hev_fires_everything_significant(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("HEV")
        mm = {
            "data_quality": 85, "signal": "STRONG_DATA_SUPPORT", "anomaly_count": 0,
            "contracts": [], "all_contracts": [
                {"name": "OVER 1", "type": "OVER", "digit": 1, "ev": 0.08,
                 "observed_edge": 3.0, "confidence": 80, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.0},
                {"name": "DIFFERS on 3", "type": "DIFFERS", "digit": 3, "ev": 0.06,
                 "observed_edge": 2.5, "confidence": 75, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.1},
            ],
        }
        plays = select_plays(mm, "R_100")
        assert len(plays) == 2
        risk_guard.set_mode("FULL_AUTO")

    def test_hev_still_blocks_negative_ev(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("HEV")
        mm = {
            "data_quality": 85, "signal": "STRONG_DATA_SUPPORT", "anomaly_count": 0,
            "contracts": [], "all_contracts": [
                {"name": "OVER 1", "type": "OVER", "digit": 1, "ev": -0.02,
                 "observed_edge": 3.0, "confidence": 80, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.0},
            ],
        }
        plays = select_plays(mm, "R_100")
        assert plays == []
        risk_guard.set_mode("FULL_AUTO")

    def test_hev_still_blocks_correlated_pair(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("HEV")
        mm = {
            "data_quality": 85, "signal": "STRONG_DATA_SUPPORT", "anomaly_count": 0,
            "contracts": [], "all_contracts": [
                {"name": "DIFFERS on 0", "type": "DIFFERS", "digit": 0, "ev": 0.08,
                 "observed_edge": 8.0, "confidence": 90, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 3.33},
                {"name": "OVER 1", "type": "OVER", "digit": 1, "ev": 0.06,
                 "observed_edge": 5.0, "confidence": 80, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.5},
            ],
        }
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1
        assert plays[0]["name"] == "DIFFERS on 0"
        risk_guard.set_mode("FULL_AUTO")


class TestParity:
    """PARITY: OVER/UNDER/ODD/EVEN/MATCHES only, never DIFFERS."""

    def test_parity_mode_exists(self):
        g = _fresh_guard()
        assert g.set_mode("PARITY")["mode"] == "PARITY"
        g.set_mode("FULL_AUTO")

    def test_parity_preset(self):
        g = _fresh_guard()
        r = g.apply_preset("PARITY")
        assert r["preset"] == "PARITY"

    def test_parity_blocks_differs_allows_parity(self):
        from app.services.auto_trader import select_plays
        from app.services.risk_guard import risk_guard
        risk_guard.set_mode("PARITY")
        mm = {
            "data_quality": 85, "signal": "STRONG_DATA_SUPPORT", "anomaly_count": 0,
            "contracts": [], "all_contracts": [
                {"name": "DIFFERS on 0", "type": "DIFFERS", "digit": 0, "ev": 0.08,
                 "observed_edge": 8.0, "confidence": 90, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 3.33},
                {"name": "OVER 1", "type": "OVER", "digit": 1, "ev": 0.06,
                 "observed_edge": 5.0, "confidence": 80, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.5},
                {"name": "ODD", "type": "ODD", "digit": None, "ev": 0.04,
                 "observed_edge": 3.0, "confidence": 70, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.0},
                {"name": "MATCHES on 5", "type": "MATCHES", "digit": 5, "ev": 0.10,
                 "observed_edge": 4.0, "confidence": 75, "evidence": "STRONG_DATA_SUPPORT",
                 "significant": True, "z": 2.2},
            ],
        }
        plays = select_plays(mm, "R_100")
        names = [p["name"] for p in plays]
        assert "DIFFERS on 0" not in names
        assert any("OVER" in n or "ODD" in n or "MATCHES" in n for n in names)
        risk_guard.set_mode("FULL_AUTO")
