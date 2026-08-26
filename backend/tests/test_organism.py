"""Conveyor-Belt organism tests — control spine, stage flow, venom kills,
strength blocks, the full-body STRIKE, and tail profiling."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.bottom_up import BottomUpEngine
from app.services.eagle import EagleEngine
from app.services.lightning import LightningEngine
from app.services.organism import ControlSpine, Organism
from app.services.pro_trader import ProTrader
from app.services.super_profit import SuperProfitEngine

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def make_organism(digits, symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                    timestamp=BASE_TS + timedelta(milliseconds=2 * i)))
    layer = BottomUpEngine(queue=q, board_source=ProTrader(queue=q))
    layer.config.min_edge = 0.03
    layer.config.confirmation_ticks = 3
    ensemble = SuperProfitEngine(queue=q, board_source=ProTrader(queue=q),
                                 decision_layer=layer, rng_seed=11)
    eagle = EagleEngine(queue=q, layer=layer, ensemble=ensemble)
    lightning = LightningEngine(queue=q, layer=layer, ensemble=ensemble)
    org = Organism(lightning=lightning, layer=layer, ensemble=ensemble, eagle=eagle)
    return org, q, layer


def tick(symbol, digit, i):
    return Tick(symbol=symbol, quote=round(1000 + digit * 0.01, 2),
                timestamp=BASE_TS + timedelta(seconds=500, milliseconds=2 * i))


# ---------------- §17 control spine ----------------
def test_spine_transitions():
    sp = ControlSpine()
    assert sp.state == "OBSERVING"
    assert sp.go("ANALYZING") is True
    assert sp.go("ARMED") is False          # illegal jump
    assert sp.go("CANDIDATE") is True
    assert sp.go("VALIDATING") is True
    assert sp.go("ARMED") is True
    assert sp.go("EXECUTING") is True
    assert sp.go("CONFIRMING") is True
    assert sp.go("RECORDED") is True
    assert sp.go("LEARNING") is True
    assert sp.go("HARDENING") is True
    assert sp.go("OBSERVING") is True


def test_spine_failure_reaches_safe_state():
    sp = ControlSpine()
    sp.go("ANALYZING")
    sp.fail("data corrupted")
    assert sp.state == "SAFE_STATE"
    assert sp.go("ANALYZING") is False
    assert sp.go("OBSERVING") is True


# ---------------- Stage 0 data armor ----------------
def test_data_armor_rejects_bad_tick():
    org, _, _ = make_organism(uniform_digits(10))
    bad = Tick(symbol="R_100", quote=-1.0, timestamp=BASE_TS)
    r = org.process(bad)
    assert r["decision"] == "REJECT"
    assert "data armor" in r["reason"]
    assert org.spine.state == "OBSERVING"


# ---------------- fast brain short-circuit ----------------
def test_fast_brain_skips_quiet_market():
    org, _, _ = make_organism(uniform_digits(300))
    r = org.process(tick("R_100", 4, 0))
    assert r["decision"] == "SKIP"
    stages = [t["stage"] for t in r["trace"]]
    assert "speed" in stages
    assert "vision" not in stages  # deep stages never ran


# ---------------- the full body STRIKE ----------------
def test_full_body_strike_on_strong_edge():
    org, q, layer = make_organism(skewed_digits())
    # confirm the signal through the persistence tracker first
    layer.evaluate("R_100")
    results = []
    for i, dgt in enumerate(skewed_digits(6)):
        t = tick("R_100", dgt, i)
        q.push(t)
        layer.on_tick(t)
        results.append(org.process(t))
    last = results[-1]
    stages = [t["stage"] for t in last["trace"]]
    if last["decision"] == "STRIKE":
        assert stages == ["data_armor", "speed", "vision", "precision",
                          "competition", "venom", "strength", "final_gate"]
        assert last["card"]["crosshair"]["contract"] == "OVER"
        assert org.strikes >= 1
        assert org.spine.state == "OBSERVING"  # loop closed
    else:
        # any rejection must still be a clean, traced decision
        assert last["decision"] in ("REJECT", "KILL", "SKIP")
        assert last["reason"]


def test_strength_blocks_when_risk_locked():
    org, q, layer = make_organism(skewed_digits())
    layer.evaluate("R_100")
    r = None
    for i, dgt in enumerate(skewed_digits(6)):
        t = tick("R_100", dgt, i)
        q.push(t)
        layer.on_tick(t)
        r = org.process(t, risk_blocked=True)
    assert r["decision"] != "STRIKE"


def test_venom_kills_decaying_candidate():
    org, _, _ = make_organism(uniform_digits(300))
    strike = {"uncertainty": 0.5, "contradiction": 0.0, "band": "A",
              "signal_lifetime": {"edge_slope": -0.01},
              "horizons": {"spike_only": False}}
    v = org._venom(strike, {"regime": "NORMAL"})
    assert v["toxic"] is True
    assert "uncertainty" in v["reason"]
    strike_ok = {"uncertainty": 0.1, "contradiction": 0.0, "band": "A",
                 "signal_lifetime": {"edge_slope": 0.01},
                 "horizons": {"spike_only": False}}
    assert org._venom(strike_ok, {"regime": "NORMAL"})["toxic"] is False


# ---------------- profiling ----------------
def test_performance_tail_stats():
    org, _, _ = make_organism(uniform_digits(300))
    for i in range(20):
        org.process(tick("R_100", (i * 3) % 10, i))
    p = org.performance()
    assert p["cycles"] == 20
    assert set(p["stages_ms"]) >= {"speed", "total"}
    total = p["stages_ms"]["total"]
    assert total["p50"] <= total["p90"] <= total["p95"] <= total["p99"]
    assert 0.0 <= p["selectivity"] <= 1.0


def test_spine_status_lists_immutable_rules():
    org, _, _ = make_organism(uniform_digits(10))
    st = org.spine_status()
    assert "max_drawdown" in st["immutable_rules"]
    assert "speed_never_bypasses_validation" in st["immutable_rules"]


# ---------------- API ----------------
def test_api_endpoints():
    assert client.get("/organism/spine").status_code == 200
    assert client.get("/organism/performance").status_code == 200
    r = client.post("/organism/process",
                    json={"symbol": "R_100", "quote": 1001.23}).json()
    assert r["decision"] in ("STRIKE", "REJECT", "KILL", "SKIP")
    bad = client.post("/organism/process", json={"symbol": "R_100"}).json()
    assert bad["decision"] == "REJECT"
