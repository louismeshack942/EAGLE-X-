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

    def test_hard_stops_include_max_profit(self):
        violations = check_hard_stops(100, 600, 0, 0, 0)
        assert any("MAX_PROFIT" in v for v in violations)

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


from app.services.auto_trader import FLUID_MAX_PLAYS, MIN_EDGE_PCT, MIN_EV, select_plays


def _mm(signal="STRONG_DATA_SUPPORT", dq=88.0, contracts=None):
    return {
        "signal": signal,
        "data_quality": dq,
        "contracts": contracts or [],
        "volatility": {"regime": "LOW"},
        "movement": {"regime": "RANGING"},
        "anomaly_count": 0,
    }


def _c(name, ev, edge, evidence="STRONG_DATA_SUPPORT", significant=True):
    return {
        "name": name,
        "type": name.split(" ")[0],
        "digit": None,
        "ev": ev,
        "observed_edge": edge,
        "confidence": 50 + edge * 5,
        "z": -2.5 if significant else -0.5,
        "significant": significant,
        "evidence": evidence,
    }


class TestFluidPlay:
    def test_only_differs_play(self):
        """Manager's directive: bench the coin flips and lottery, play DIFFERS."""
        mm = _mm(contracts=[
            _c("OVER 1", 0.88, 8.0),        # coin flip — benched despite EV
            _c("MATCHES on 6", 0.71, 9.0),   # lottery — benched despite EV
            _c("DIFFERS on 3", 0.09, 5.0),   # the edge — plays
        ])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1
        assert plays[0]["type"] == "DIFFERS"

    def test_two_equal_plays_split(self):
        """Two strong positive-EV DIFFERS -> both play, stake splits."""
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
