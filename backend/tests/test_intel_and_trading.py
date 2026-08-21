from app.services.intelligence import IntelligenceEngine
from app.services.market_master import MarketMaster
from app.services.engines import QualityEngine, VolatilityEngine, MovementEngine
from app.services.money_management import check_hard_stops, compute_stake, cooldown_for
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


from app.services.auto_trader import FLUID_MAX_PLAYS, FLUID_MIN_CONFIDENCE, select_plays


def _mm(signal="STRONG_DATA_SUPPORT", dq=88.0, contracts=None):
    return {
        "signal": signal,
        "data_quality": dq,
        "contracts": contracts or [],
        "volatility": {"regime": "LOW"},
        "movement": {"regime": "RANGING"},
        "anomaly_count": 0,
    }


def _c(name, conf, score=None, evidence="STRONG_DATA_SUPPORT"):
    return {
        "name": name,
        "type": name.split(" ")[0],
        "digit": None,
        "score": score if score is not None else conf,
        "confidence": conf,
        "evidence": evidence,
    }


class TestFluidPlay:
    def test_two_equal_plays_split(self):
        """ODD 100 + MATCHES 100 -> both play, stake splits."""
        mm = _mm(contracts=[_c("ODD", 100), _c("MATCHES on 6", 100)])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 2
        assert {p["name"] for p in plays} == {"ODD", "MATCHES on 6"}
        assert all(p["symbol"] == "R_100" for p in plays)

    def test_second_play_too_far_behind(self):
        """ODD 100, EVEN 60 (< 75% of top) -> single play only."""
        mm = _mm(contracts=[_c("ODD", 100), _c("EVEN", 60)])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1
        assert plays[0]["name"] == "ODD"

    def test_gate_blocks_weak_signal(self):
        mm = _mm(signal="NEUTRAL", contracts=[_c("ODD", 100)])
        assert select_plays(mm, "R_100") == []

    def test_gate_blocks_low_data_quality(self):
        mm = _mm(dq=55.0, contracts=[_c("ODD", 100)])
        assert select_plays(mm, "R_100") == []

    def test_floor_and_contrary_evidence_excluded(self):
        mm = _mm(contracts=[
            _c("ODD", 100),
            _c("EVEN", 30, evidence="WEAK_DATA_CONTRARY"),
            _c("OVER 4", FLUID_MIN_CONFIDENCE - 1),
        ])
        plays = select_plays(mm, "R_100")
        assert len(plays) == 1

    def test_never_more_than_max(self):
        mm = _mm(contracts=[_c("A", 100), _c("B", 100), _c("C", 100)])
        assert len(select_plays(mm, "R_100")) == FLUID_MAX_PLAYS

    def test_compounding_stake_grows_with_balance(self):
        """Stake stays 10% of the CURRENT balance as the account grows."""
        assert compute_stake(10.0) == 1.0
        assert compute_stake(20.0) == 2.0
        assert compute_stake(44.95) == 4.5
