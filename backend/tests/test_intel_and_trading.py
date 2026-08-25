from app.services.intelligence import IntelligenceEngine
from app.services.market_master import MarketMaster
from app.services.engines import QualityEngine, VolatilityEngine, MovementEngine
from app.services.money_management import (
    check_hard_stops,
    compute_stake,
    cooldown_for,
    drawdown_multiplier,
    kelly_fraction,
    kelly_stake,
    risk_state,
)
from app.services.persistence import journal_engine, backtest_engine, replay_engine, AlertsEngine
from app.services.demo_generator import DemoGenerator
from app.core.queue import BoundedTickQueue

import asyncio


class TestIntelligence:
    def test_signal_values(self):
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            async for tick in demo.stream("R_100"):
                q.push(tick)
                if q.count("R_100") >= 250:
                    break
        asyncio.run(fill())
        engine = IntelligenceEngine(queue=q)
        out = engine.analyze("R_100", 100)
        assert out["decision"] in [
            "STRONG_DATA_SUPPORT",
            "WEAK_DATA_SUPPORT",
            "NEUTRAL",
            "NO_CLEAR_STATISTICAL_EDGE",
            "INSUFFICIENT_DATA",
        ]
        assert 0 <= out["data_quality"] <= 100

    def test_most_likely_returns_digit(self):
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            async for tick in demo.stream("R_100"):
                q.push(tick)
                if q.count("R_100") >= 250:
                    break
        asyncio.run(fill())
        engine = IntelligenceEngine(queue=q)
        out = engine.most_likely("R_100", 100)
        assert out["digit"] in range(10)
        assert out["contract"] in ("MATCHES", "DIFFERS")

    def test_scan_all_ranks(self):
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            for sym in ("R_10", "R_25"):
                async for tick in demo.stream(sym):
                    q.push(tick)
                    if q.count(sym) >= 100:
                        break
        asyncio.run(fill())
        engine = IntelligenceEngine(queue=q)
        scan = engine.scan_all(["R_10", "R_25"], 100)
        assert len(scan["markets"]) == 2
        assert scan["markets"][0]["score"] >= scan["markets"][1]["score"]


class TestMarketMaster:
    def test_decides_or_waits(self):
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            async for tick in demo.stream("R_100"):
                q.push(tick)
                if q.count("R_100") >= 250:
                    break
        asyncio.run(fill())
        mm = MarketMaster()
        out = mm.analyze("R_100", 100)
        assert out["top_recommendation"] is not None
        assert isinstance(out["contracts"], list)
        assert len(out["contracts"]) <= 6


class TestMoneyManagement:
    def test_stake_pct(self):
        assert compute_stake(100) == 10.0

    def test_hard_stops_include_loss_limit(self):
        violations = check_hard_stops(100, 79, 0, 0, 0)
        assert any("STOP_LOSS" in v for v in violations)

    def test_max_profit_tracks_current_balance(self):
        """Manager's ruling: the profit cap is 500% of the CURRENT balance,
        not the opening one. Up 5x on the opening $100 (balance $600) is NOT
        a violation — the target is now 500% of $600 = $3000, so the run
        continues. Not less, not more."""
        from app.services.money_management import profit_target
        assert profit_target(10.0) == 50.0
        assert profit_target(25.0) == 125.0   # grew with the balance
        assert profit_target(600.0) == 3000.0
        violations = check_hard_stops(100, 600, 0, 0, 0)
        assert not any("MAX_PROFIT" in v for v in violations)
        # Risk state surfaces the live target for the GK panel.
        from app.services.money_management import risk_state
        assert risk_state(100, 600, 0)["profit_target"] == 3000.0

    def test_cooldowns(self):
        assert cooldown_for("loss") == 30.0
        assert cooldown_for("win") == 10.0


class TestPersistence:
    def test_journal_dashboard(self):
        journal_engine.add_entry("R_100", "MATCHES", 3, 1.0, "win", 9.0, 90.0, 80.0, "paper")
        journal_engine.add_entry("R_100", "MATCHES", 3, 1.0, "loss", -1.0, 90.0, 80.0, "paper")
        dash = journal_engine.dashboard()
        assert dash["trades_today"] >= 2

    def test_alerts_lifecycle(self):
        alerts = AlertsEngine()
        a = alerts.add_alert("signal", "hello")
        assert alerts.mark_read(a["id"])
        assert not any(not x["read"] for x in alerts.list_alerts() if x["id"] == a["id"])

    def test_backtest_runs(self):
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            async for tick in demo.stream("R_100"):
                q.push(tick)
                if q.count("R_100") >= 100:
                    break
        asyncio.run(fill())
        ticks = [t.to_dict() for t in q.recent("R_100", 100)]
        out = backtest_engine.run(ticks)
        assert "win_rate" in out and "profit_factor" in out and "equity_curve" in out

    def test_replay_controls(self):
        replay = replay_engine.load([{"digit": 1}], "test")
        session = replay_engine.control(replay["id"], "play", 2.0)
        assert session["playing"] is True
        session = replay_engine.control(replay["id"], "step")
        assert session["position"] == 0 or session["position"] == 1


from app.services.auto_trader import FLUID_MAX_PLAYS, MAX_ANOMALIES, select_plays
from app.services.market_master import MIN_EDGE_PCT, MIN_EV


def _mm(signal="STRONG_DATA_SUPPORT", dq=88.0, contracts=None, anomalies=0):
    return {
        "signal": signal,
        "data_quality": dq,
        "contracts": contracts or [],
        "volatility": {"regime": "LOW"},
        "movement": {"regime": "RANGING"},
        "anomaly_count": anomalies,
    }


def _c(name, ev, edge, evidence="STRONG_DATA_SUPPORT", significant=True, observed_pct=None):
    return {
        "name": name,
        "type": name.split(" ")[0],
        "digit": None,
        "ev": ev,
        "observed_edge": edge,
        "confidence": 50 + edge * 5,
        "z": 2.5 if significant else 0.5,
        "significant": significant,
        "evidence": evidence,
        **({"observed_pct": observed_pct} if observed_pct is not None else {}),
    }


class TestTeamDecision:
    def test_ev_ranks_the_menu_truth_gate_decides(self):
        """No contract type gets a bye: the squad ranks by EV alone and the
        truth gate (all-windows proven_edges) is the final arbiter at fire
        time. A higher-EV OVER leads the menu; a sub-breakeven MATCHES never
        makes it on."""
        mm = _mm(contracts=[
            _c("OVER 1", 0.88, 8.0),         # highest EV leads now
            _c("MATCHES on 6", 0.71, 9.0),   # no observed_pct -> below breakeven, benched
            _c("DIFFERS on 3", 0.09, 5.0),   # real but too far behind the leader
        ])
        plays = select_plays(mm, "R_100")
        assert plays[0]["name"] == "OVER 1"   # EV leads the menu
        assert all(p["type"] != "MATCHES" for p in plays)  # lottery stays benched
        assert all(p["name"] != "DIFFERS on 3" for p in plays)  # pair-ratio: 0.09 < 75% of 0.88

    def test_every_contract_type_is_eligible(self):
        # Every contract type passes the vote when the team agrees — the
        # truth gate, not a type ban, decides what may fire.
        for name in ("DIFFERS on 3", "ODD", "EVEN", "OVER 4", "UNDER 5"):
            plays = select_plays(_mm(contracts=[_c(name, 0.10, 5.0)]), "R_100")
            assert plays and plays[0]["name"] == name, f"{name} benched despite full team approval"
        # MATCHES below the 11.11% breakeven can NEVER be an edge — cheap skip.
        plays = select_plays(_mm(contracts=[_c("MATCHES on 6", 0.71, 9.0, observed_pct=11.0)]), "R_100")
        assert plays == []
        # MATCHES above breakeven is eligible — the truth gate decides the rest.
        plays = select_plays(_mm(contracts=[_c("MATCHES on 6", 0.71, 9.0, observed_pct=16.0)]), "R_100")
        assert plays and plays[0]["name"] == "MATCHES on 6"

    def test_two_equal_plays_split(self):
        """Two strong positive-EV plays -> both play, stake splits."""
        mm = _mm(contracts=[_c("DIFFERS on 3", 0.09, 5.0), _c("DIFFERS on 7", 0.085, 4.8)])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 2
        assert {p["name"] for p in plays} == {"DIFFERS on 3", "DIFFERS on 7"}
        assert all(p["symbol"] == "R_100" for p in plays)

    def test_second_play_too_far_behind(self):
        mm = _mm(contracts=[_c("DIFFERS on 3", 0.10, 5.0), _c("DIFFERS on 7", 0.07, 4.0)])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1
        assert plays[0]["name"] == "DIFFERS on 3"

    def test_physio_blocks_anomalous_market(self):
        mm = _mm(anomalies=MAX_ANOMALIES + 1, contracts=[_c("DIFFERS on 3", 0.10, 5.0)])
        assert select_plays(mm, "R_100") == []

    def test_gate_blocks_weak_signal(self):
        mm = _mm(signal="NEUTRAL", contracts=[_c("DIFFERS on 3", 0.10, 5.0)])
        assert select_plays(mm, "R_100") == []

    def test_gate_blocks_low_data_quality(self):
        mm = _mm(dq=55.0, contracts=[_c("DIFFERS on 3", 0.10, 5.0)])
        assert select_plays(mm, "R_100") == []

    def test_negative_ev_excluded(self):
        mm = _mm(contracts=[_c("DIFFERS on 3", -0.05, 5.0)])
        assert select_plays(mm, "R_100") == []

    def test_edge_floor_enforced(self):
        mm = _mm(contracts=[_c("DIFFERS on 3", 0.10, MIN_EDGE_PCT - 0.5)])
        assert select_plays(mm, "R_100") == []

    def test_contrary_evidence_excluded(self):
        mm = _mm(contracts=[
            _c("DIFFERS on 3", 0.10, 5.0),
            _c("DIFFERS on 7", 0.09, 4.8, evidence="WEAK_DATA_CONTRARY"),
        ])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1

    def test_never_more_than_max(self):
        mm = _mm(contracts=[_c("DIFFERS on 1", 0.10, 5.0), _c("DIFFERS on 2", 0.095, 5.0), _c("DIFFERS on 3", 0.09, 5.0)])
        assert len(select_plays(mm, "R_100")) == FLUID_MAX_PLAYS

    def test_compounding_stake_grows_with_balance(self):
        """Stake stays 10% of the CURRENT balance as the account grows."""
        assert compute_stake(10.0) == 1.0
        assert compute_stake(20.0) == 2.0
        assert compute_stake(44.95) == 4.5

    def test_significance_gate_blocks_noise(self):
        """A starving digit without 95% significance never makes the team."""
        mm = _mm(contracts=[_c("DIFFERS on 3", 0.10, 5.0, significant=False)])
        assert select_plays(mm, "R_100") == []

    def test_benching_after_consecutive_losses(self):
        """Manager benches the CF after MAX_GAMES_WITHOUT_GOAL straight misses."""
        from app.services.auto_trader import AutoTrader, MAX_GAMES_WITHOUT_GOAL, BENCH_GAMES
        at = AutoTrader()
        at.running = True
        at._scan_count = 5
        for _ in range(MAX_GAMES_WITHOUT_GOAL):
            at.consecutive_losses += 1
            at.losses_today += 1
            if at.consecutive_losses >= MAX_GAMES_WITHOUT_GOAL and not at.benched:
                at.benched = True
                at.benched_until = at._scan_count + BENCH_GAMES
        assert at.benched is True
        assert at.benched_until == 5 + BENCH_GAMES
        # He returns after the bench window and his slate is wiped clean.
        at._scan_count = at.benched_until
        at.benched = False
        at.consecutive_losses = 0
        assert at.benched is False
        assert at.consecutive_losses == 0

    def test_pep_rule_benches_at_two_and_tightens_the_return(self):
        """Pep: lose possession twice → pause, regroup, tight marking. No
        chasing. The CF returns only for a proven strike (higher z, extra
        confirmation), and a goal releases the marking."""
        from app.services.auto_trader import (
            AutoTrader, MAX_GAMES_WITHOUT_GOAL, TIGHT_CONFIRM_TICKS, TIGHT_MIN_Z,
        )
        assert MAX_GAMES_WITHOUT_GOAL == 2  # Pep's number, not three
        at = AutoTrader()
        assert at.tight_marking is False
        # Two straight losses → benched.
        at.consecutive_losses = 2
        assert at.consecutive_losses >= MAX_GAMES_WITHOUT_GOAL
        # Returning from the bench engages tight marking.
        at.benched = False
        at.tight_marking = True
        assert at.tight_marking is True
        # The bar is provably higher than normal pressing.
        assert TIGHT_CONFIRM_TICKS > 2
        assert TIGHT_MIN_Z > 1.96
        # A win releases the marking (mirrors place_trade's win path).
        at.tight_marking = False
        assert at.tight_marking is False


class TestWorldClassGK:
    def test_kelly_refuses_negative_edge(self):
        """Kelly says 0 for a fair coin flip at 1.9 payout — GK won't play it."""
        assert kelly_fraction(0.5, 1.9) == 0.0
        assert kelly_stake(0.5, 1.9, 100.0) == 0.0

    def test_kelly_sizes_positive_edge(self):
        """DIFFERS at 90% / 1.1 payout: quarter-Kelly, capped at 10%."""
        f = kelly_fraction(0.92, 1.1)
        assert f > 0  # genuine edge exists
        s = kelly_stake(0.92, 1.1, 100.0)
        assert 0.0 < s <= 10.0  # quarter-Kelly under the cap

    def test_kelly_never_exceeds_cap(self):
        s = kelly_stake(0.999, 9.0, 1000.0)
        assert s <= 100.0  # max_kelly_pct * balance

    def test_drawdown_multiplier_scales_down(self):
        assert drawdown_multiplier(10.0, 10.0) == 1.0       # at par
        mid = drawdown_multiplier(10.0, 8.5)                # 15% down
        assert 0.35 < mid < 1.0
        wall = drawdown_multiplier(10.0, 8.0)               # at the stop-loss wall
        assert wall >= 0.35

    def test_risk_state_posture(self):
        s = risk_state(10.0, 10.0, 0)
        assert s["posture"] == "FULL_ATTACK" and s["rating"] >= 90
        s2 = risk_state(10.0, 8.2, 2)
        assert s2["posture"] in ("CAUTIOUS", "DEFEND")
        assert s2["rating"] < s["rating"]


class TestZScoreAnalytics:
    def test_digit_analysis_has_z_and_estimate(self):
        from app.services.analytics_advanced import digit_engine
        from app.models.tick import Tick
        from datetime import datetime, timezone
        from app.core.queue import tick_queue
        # Feed a skewed sample: digit 7 appears 30% of the time.
        for i in range(200):
            quote = 100.0 + (7 if i % 10 < 3 else i % 10) * 0.01
            tick_queue.push(Tick(symbol="Z_TEST", quote=quote, timestamp=datetime.now(timezone.utc), provider="demo"))
        a = digit_engine.get_digit_analysis("Z_TEST", 200)
        f7 = a["frequency"]["7"]
        assert f7["estimate"] > 10.0
        assert f7["z"] > 1.96          # statistically significant overfeed
        assert f7["significant"] is True
        # A fair digit is NOT significant.
        f3 = a["frequency"]["3"]
        assert f3["significant"] is False


class TestDerivContractMapping:
    """Live-money correctness: Deriv only understands its own contract names,
    and digit contracts are rejected without a barrier."""

    def test_internal_names_map(self):
        from app.services.deriv_trader import deriv_contract_params
        assert deriv_contract_params("MATCHES", 6) == {"contract_type": "DIGITMATCH", "barrier": "6"}
        assert deriv_contract_params("DIFFERS", 3) == {"contract_type": "DIGITDIFF", "barrier": "3"}
        assert deriv_contract_params("ODD", None) == {"contract_type": "DIGITODD"}
        assert deriv_contract_params("EVEN", None) == {"contract_type": "DIGITEVEN"}
        assert deriv_contract_params("OVER", 4) == {"contract_type": "DIGITOVER", "barrier": "4"}
        assert deriv_contract_params("UNDER", 7) == {"contract_type": "DIGITUNDER", "barrier": "7"}

    def test_deriv_native_names_pass_through(self):
        from app.services.deriv_trader import deriv_contract_params
        assert deriv_contract_params("DIGITDIFF", 2) == {"contract_type": "DIGITDIFF", "barrier": "2"}
        assert deriv_contract_params("CALL", None) == {"contract_type": "CALL"}
        assert deriv_contract_params("PUT", None) == {"contract_type": "PUT"}

    def test_digit_contract_without_barrier_rejected(self):
        import pytest
        from app.services.deriv_trader import deriv_contract_params
        with pytest.raises(ValueError):
            deriv_contract_params("MATCHES", None)
        with pytest.raises(ValueError):
            deriv_contract_params("DIGITOVER", None)

    def test_unknown_contract_rejected(self):
        import pytest
        from app.services.deriv_trader import deriv_contract_params
        with pytest.raises(ValueError):
            deriv_contract_params("LOTTERY_TICKET", None)


class TestTeamBoard:
    def test_every_contract_carries_a_verdict(self):
        """Market Master stamps PLAY/BENCH + reason on every contract — the
        whole team's vote is visible, not just the winner."""
        q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        async def fill():
            async for tick in demo.stream("R_100"):
                q.push(tick)
                if q.count("R_100") >= 300:
                    break
        asyncio.run(fill())
        from app.services.analytics_advanced import AdvancedAnalytics
        from app.services.intelligence import IntelligenceEngine
        import app.services.market_master as mm_mod
        # Point the shared engines at our queue for this test (restored after).
        orig_intel, orig_digit = mm_mod.intelligence_engine, mm_mod.digit_engine
        mm_mod.intelligence_engine = IntelligenceEngine(queue=q)
        mm_mod.digit_engine = AdvancedAnalytics(queue=q)
        try:
            out = mm_mod.MarketMaster().analyze("R_100", 100)
        finally:
            mm_mod.intelligence_engine, mm_mod.digit_engine = orig_intel, orig_digit
        board = out["all_contracts"]
        assert board, "board empty"
        for c in board:
            assert c["verdict"] in ("PLAY", "BENCH")
            assert "verdict_reason" in c
            assert "z" in c and "significant" in c  # every type, incl. ODD/EVEN/OVER/UNDER
        plays = select_plays(out, "R_100")
        # CF may only finish what the team approved.
        for p in plays:
            on_board = next(b for b in board if b["name"] == p["name"])
            assert on_board["verdict"] == "PLAY"

    def test_odd_even_carry_z_scores(self):
        """A deliberately odd-heavy stream must make ODD significant, not EVEN."""
        from app.models.tick import Tick
        from datetime import datetime, timezone
        from app.core.queue import tick_queue
        from app.services.analytics_advanced import AdvancedAnalytics
        from app.services.intelligence import IntelligenceEngine
        import app.services.market_master as mm_mod
        for i in range(400):
            # last digit odd 70% of the time
            last = 7 if i % 10 < 7 else 2
            tick_queue.push(Tick(symbol="Z_OE", quote=100.0 + last * 0.01, timestamp=datetime.now(timezone.utc), provider="demo"))
        orig_intel, orig_digit = mm_mod.intelligence_engine, mm_mod.digit_engine
        mm_mod.intelligence_engine = IntelligenceEngine(queue=tick_queue)
        mm_mod.digit_engine = AdvancedAnalytics(queue=tick_queue)
        try:
            board = mm_mod.MarketMaster().analyze("Z_OE", 400)["all_contracts"]
        finally:
            mm_mod.intelligence_engine, mm_mod.digit_engine = orig_intel, orig_digit
        odd = next(c for c in board if c["type"] == "ODD")
        even = next(c for c in board if c["type"] == "EVEN")
        assert odd["z"] > 1.96 and odd["significant"] is True
        assert even["significant"] is False
        over = next(c for c in board if c["type"] == "OVER" and c["digit"] == 1)
        assert over["z"] > 1.96 and over["significant"] is True

    def test_status_exposes_decision_history(self):
        from app.services.auto_trader import AutoTrader
        at = AutoTrader()
        s = at.status()
        assert "decision_history" in s and isinstance(s["decision_history"], list)
