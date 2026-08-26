"""Lightning layer tests — O(1) ring windows, two-tier short-circuit,
latency profiler, event bus priorities, trade ledger duplicate protection,
and the fast failsafe."""
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.bottom_up import BottomUpEngine
from app.services.lightning import (
    EventBus,
    LatencyProfiler,
    LightningEngine,
    RingWindow,
    StageTimes,
    SymbolState,
    TradeLedger,
)
from app.services.pro_trader import ProTrader
from app.services.super_profit import SuperProfitEngine

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def make_engine(digits=(), symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                    timestamp=BASE_TS + timedelta(milliseconds=2 * i)))
    layer = BottomUpEngine(queue=q, board_source=ProTrader(queue=q))
    layer.config.min_edge = 0.03
    ensemble = SuperProfitEngine(queue=q, board_source=ProTrader(queue=q),
                                 decision_layer=layer, rng_seed=3)
    eng = LightningEngine(queue=q, layer=layer, ensemble=ensemble)
    return eng, q


def tick(symbol, digit, i):
    return Tick(symbol=symbol, quote=round(1000 + digit * 0.01, 2),
                timestamp=BASE_TS + timedelta(seconds=500, milliseconds=2 * i))


# ---------------- §5/§6 ring window O(1) ----------------
def test_ring_window_incremental_counts():
    w = RingWindow(10)
    for d in range(10):
        w.push(d)
    assert w.filled == 10
    assert w.counts == [1] * 10
    w.push(3)  # evicts 0, inserts 3 — two counter ops
    assert w.counts[0] == 0
    assert w.counts[3] == 2
    assert w.filled == 10
    probs = w.probs()
    assert probs[3] == pytest.approx(3 / 20, abs=1e-9)
    assert sum(probs) == pytest.approx(1.0, abs=1e-9)


def test_ring_window_matches_counter():
    import random
    from collections import Counter
    rng = random.Random(1)
    digits = [rng.randrange(10) for _ in range(500)]
    w = RingWindow(100)
    for d in digits:
        w.push(d)
    c = Counter(digits[-100:])
    assert w.counts == [c.get(d, 0) for d in range(10)]


# ---------------- §4/§9 two-tier hot path ----------------
def test_tick_updates_only_its_market():
    eng, q = make_engine(uniform_digits(300))
    eng.on_tick(tick("R_75", 5, 0))
    assert "R_75" in eng.symbols
    assert "R_10" not in eng.symbols  # other markets untouched


def test_fast_filter_skips_fair_market():
    eng, q = make_engine()
    res = eng.on_tick(tick("R_100", 4, 0))
    assert res["decision"] == "SKIP"
    assert "insufficient_sample" in res["reason"]
    assert eng.symbols["R_100"].deep_count == 0  # deep analysis never ran


def test_hot_path_latency_budget():
    eng, q = make_engine(uniform_digits(300))
    results = [eng.on_tick(tick("R_100", (i * 7) % 10, i)) for i in range(50)]
    skips = [r for r in results if r["decision"] == "SKIP"]
    assert skips, "uniform tape should mostly skip"
    for r in skips:
        s = r["stages"]
        assert s["total_ms"] < 50  # generous CI ceiling; local target is <10ms
    stats = eng.profiler.stats()
    assert stats["samples"] >= 50
    assert stats["stages"]["features"]["p50"] >= 0


# ---------------- §13/§26 event bus ----------------
def test_event_bus_priority_order():
    bus = EventBus()
    bus.emit("P5_LOGGING", "log", {"x": 1})
    bus.emit("P0_TRADE", "trade", {"x": 2})
    bus.emit("P3_DASHBOARD", "dash", {"x": 3})
    drained = bus.drain()
    assert [e["kind"] for e in drained] == ["trade", "dash", "log"]


def test_event_bus_handlers_never_break():
    bus = EventBus()
    bus.subscribe(lambda ev: 1 / 0)
    bus.emit("P1_TICK", "tick", {})
    assert len(bus.drain()) == 1


# ---------------- §29 trade ledger ----------------
def test_ledger_duplicate_protection():
    ledger = TradeLedger()
    assert ledger.create("t1", "R_100", "OVER", 4) is not None
    assert ledger.create("t1", "R_100", "OVER", 4) is None  # duplicate refused


def test_ledger_transitions_and_timeout():
    ledger = TradeLedger()
    ledger.create("t1", "R_100", "OVER", 4)
    assert ledger.transition("t1", "SUBMITTED") is not None
    assert ledger.transition("t1", "SUBMITTED") is None  # illegal
    # force timeout
    rec = ledger._records["t1"]
    rec.updated_at = time.monotonic() - 11.0
    expired = ledger.expire_unknowns()
    assert expired == ["t1"]
    assert ledger.open_unknowns()
    # UNKNOWN can only resolve, never silently become CONFIRMED->traded again
    assert ledger.transition("t1", "CONFIRMED") is not None
    snap = ledger.snapshot()
    assert snap["by_state"]["CONFIRMED"] == 1


def test_ledger_unknowns_block_failsafe():
    eng, _ = make_engine()
    eng.symbols["R_100"] = SymbolState("R_100", eng.window_sizes)
    eng.symbols["R_100"].last_tick_epoch = time.monotonic()
    eng.ledger.create("u1", "R_100", "OVER", 4)
    eng.ledger.transition("u1", "SUBMITTED")
    eng.ledger._records["u1"].updated_at = time.monotonic() - 11.0
    reason = eng.failsafe("R_100")
    assert reason and "UNKNOWN" in reason


# ---------------- §28 failsafe ----------------
def test_failsafe_blocks_on_stale_feed_and_disconnect():
    eng, _ = make_engine()
    assert "no ticks" in eng.failsafe("R_100")
    st = SymbolState("R_100", eng.window_sizes)
    st.last_tick_epoch = time.monotonic() - 60.0
    eng.symbols["R_100"] = st
    assert "stale feed" in eng.failsafe("R_100")
    st.last_tick_epoch = time.monotonic()
    assert eng.failsafe("R_100") is None
    eng.connection["connected"] = False
    assert "connection lost" in eng.failsafe("R_100")


# ---------------- §31 profiler ----------------
def test_profiler_percentiles():
    prof = LatencyProfiler()
    for i in range(100):
        prof.record(StageTimes(parse_ms=0.1, features_ms=0.5,
                               fast_filter_ms=0.3, deep_ms=i / 10.0,
                               risk_ms=0.2, total_ms=i / 5.0))
    s = prof.stats()["stages"]["deep"]
    assert s["p50"] < s["p90"] <= s["p95"] <= s["p99"]
    assert prof.stats()["samples"] == 100


# ---------------- §32 dashboard ----------------
def test_dashboard_shape():
    eng, _ = make_engine()
    for i in range(5):
        eng.on_tick(tick("R_100", i % 10, i))
    d = eng.dashboard()
    for k in ("websocket", "markets_active", "tick_processing_ms",
              "decision_p95_ms", "ledger", "within_targets"):
        assert k in d
    assert d["markets_active"] == 1
    assert "never places a trade" in d["note"]


# ---------------- API ----------------
def test_api_endpoints():
    assert client.get("/lightning/dashboard").status_code == 200
    assert client.get("/lightning/profiler").status_code == 200
    assert client.get("/lightning/events").status_code == 200
    assert client.get("/lightning/ledger").status_code == 200
    f = client.get("/lightning/failsafe/R_100").json()
    assert "blocked" in f
    b = client.post("/lightning/ledger/begin",
                    json={"client_trade_id": "api-t1", "symbol": "R_100",
                          "contract": "OVER", "barrier": 4}).json()
    assert b["ok"] is True
    dup = client.post("/lightning/ledger/begin",
                      json={"client_trade_id": "api-t1", "symbol": "R_100",
                            "contract": "OVER", "barrier": 4}).json()
    assert dup["ok"] is False
    u = client.post("/lightning/ledger/update",
                    json={"client_trade_id": "api-t1", "state": "SUBMITTED"}).json()
    assert u["ok"] is True
    bad = client.post("/lightning/ledger/update",
                      json={"client_trade_id": "api-t1", "state": "SUBMITTED"}).json()
    assert bad["ok"] is False
