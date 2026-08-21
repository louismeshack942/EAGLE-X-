import statistics
from datetime import datetime, timedelta, timezone

from app.core.queue import BoundedTickQueue
from app.models.tick import Tick
from app.services.analytics import AnalyticsEngine
from app.services.analytics_advanced import AdvancedAnalytics
from app.services.demo_generator import DemoGenerator


class TestTick:
    def test_digit_extraction(self):
        tick = Tick(symbol="R_100", quote=123.456, provider="demo")
        assert tick.digit == 6

    def test_to_dict(self):
        tick = Tick(symbol="R_10", quote=99.5, provider="demo")
        d = tick.to_dict()
        assert d["symbol"] == "R_10"
        assert d["quote"] == 99.5
        assert "digit" in d


class TestBoundedTickQueue:
    def test_bounded_maxlen(self):
        q = BoundedTickQueue(maxlen=5)
        for i in range(10):
            q.push(Tick(symbol="R_10", quote=float(i), provider="demo"))
        assert q.count("R_10") == 5

    def test_recent_and_latest(self):
        q = BoundedTickQueue(maxlen=10)
        for i in range(5):
            q.push(Tick(symbol="R_10", quote=float(i), provider="demo"))
        assert len(q.recent("R_10")) == 5
        assert q.latest("R_10").quote == 4


class TestAnalyticsEngine:
    def test_stats_basic(self):
        q = BoundedTickQueue()
        for i in range(20):
            q.push(Tick(symbol="R_10", quote=100 + i * 0.1, provider="demo"))
        engine = AnalyticsEngine(queue=q)
        stats = engine.get_stats("R_10", window=20)
        assert stats["count"] == 20
        assert stats["latest"] is not None
        assert stats["mean"] > 100

    def test_timer_statuses(self):
        q = BoundedTickQueue()
        now = datetime.now(timezone.utc)
        for i in range(10):
            q.push(Tick(symbol="R_10", quote=100.0, timestamp=now - timedelta(seconds=i), provider="demo"))
        engine = AnalyticsEngine(queue=q)
        result = engine.time_to_next_tick("R_10")
        assert "status" in result
        assert result["status"] in ("GREEN", "YELLOW", "ORANGE", "RED")


class TestAdvancedAnalytics:
    def setup_method(self):
        self.q = BoundedTickQueue()
        demo = DemoGenerator(interval_ms=1)
        import asyncio
        async def fill():
            async for tick in demo.stream("R_100"):
                self.q.push(tick)
                if self.q.count("R_100") >= 150:
                    break
        asyncio.run(fill())
        self.engine = AdvancedAnalytics(queue=self.q)

    def test_digit_frequency(self):
        out = self.engine.get_digit_analysis("R_100", window=100)
        assert set(out["frequency"].keys()) == {str(d) for d in range(10)}
        total = sum(out["frequency"][str(d)]["count"] for d in range(10))
        assert total == 100

    def test_psychology_picks_extremes(self):
        out = self.engine.get_psychology("R_100", window=100)
        assert out["overfed"]["deviation"] >= out["starving"]["deviation"]

    def test_predictor_candidate(self):
        out = self.engine.get_predictor("R_100", window=100)
        assert out["candidate"] in range(10)

    def test_gap_analysis(self):
        out = self.engine.get_gap_analysis("R_100", window=100)
        assert all("current" in g and "max" in g for g in out["gaps"].values())

    def test_contract_modes(self):
        out = self.engine.get_contract_analysis("R_100", window=100)
        assert "ODD" in out["modes"] and "EVEN" in out["modes"]

    def test_ldp_patterns(self):
        out = self.engine.get_ldp_patterns("R_100", pattern_len=2, window=100)
        assert out["top_patterns"]

    def test_multi_window(self):
        out = self.engine.get_multi_window("R_100")
        assert out["windows"]["1m"]


class TestDemoGenerator:
    def test_deterministic(self):
        d1 = DemoGenerator(seed=42, start_price=100.0)
        d2 = DemoGenerator(seed=42, start_price=100.0)
        p1 = [d1._next_price("R_100") for _ in range(3)]
        p2 = [d2._next_price("R_100") for _ in range(3)]
        assert p1 == p2
