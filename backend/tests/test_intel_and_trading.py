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
