"""Forge tests — survivability, self-destruction, disaster simulation,
chaos engine and the EAGLE_STRENGTH score. Paper-only, no broker."""
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.forge import (
    chaos_engine,
    disaster_simulation,
    eagle_strength,
    self_destruct,
    survivability,
)
from tests.test_organism import make_organism, tick, uniform_digits

client = TestClient(app)


def skewed(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


# ---------------- §29 survivability ----------------
def test_survivability_rejects_bad_inputs():
    assert survivability(0, 1, 1.95)["survivable"] is False
    assert survivability(100, 0, 1.95)["survivable"] is False
    assert survivability(100, 1, 1.0)["survivable"] is False


def test_survivability_math():
    r = survivability(balance=1000, stake=1.0, payout=1.95)
    assert r["verdict"] == "SURVIVABLE"
    assert r["survives_100_consecutive_losses"] is True  # loss wall at 5 < 100
    assert r["worst_case_loss"] <= r["capital_wall"]
    # oversized stake: 100 x $50 = $5000 >> walls -> must still be caught by streak wall
    r2 = survivability(balance=1000, stake=50.0, payout=1.95,
                       max_consecutive_losses=3, severe_streak=5)
    # capital wall (5% of 1000 = $50) binds before the 5-loss streak ($250)
    assert r2["worst_case_loss"] == 50.0
    assert r2["capital_wall"] == 50.0


# ---------------- §5 self-destruction ----------------
def test_self_destruct_flags_overfit_spike():
    # a fragile edge that only exists in one narrow window dies
    digits = uniform_digits(300)
    # inject a tiny recent spike on digit 2 only
    digits[-30:] = [2 if i % 3 else 4 for i in range(30)]
    r = self_destruct(digits, "MATCHES", 2, base_window=25, seed=3)
    assert r["verdict"] in ("OVERFIT — KILL", "ROBUST")
    assert "perturbation" in r


def test_self_destruct_robust_real_edge():
    r = self_destruct(skewed(), "OVER", 0, base_window=250, seed=3)
    # a true, persistent skew should mostly beat shuffles (allow tail luck)
    assert r["shuffle_survival_rate"] <= 0.7


def test_self_destruct_insufficient_data():
    r = self_destruct([1, 2, 3], "MATCHES", 1)
    assert r["verdict"] == "INSUFFICIENT_DATA"


# ---------------- §4 disaster simulator ----------------
def test_disaster_simulation_all_scenarios():
    r = disaster_simulation(balance=1000, stake=1.0, payout=1.95)
    assert r["verdict"] == "SURVIVES"
    assert "100_consecutive_losses" in r["scenarios"]
    assert "200_consecutive_losses" in r["scenarios"]
    assert r["scenarios"]["100_consecutive_losses"]["halted_by"] == "consecutive-loss wall"


# ---------------- §17 chaos engine ----------------
def test_chaos_engine_safe_degradation():
    org, q, layer = make_organism(uniform_digits(300))
    r = chaos_engine(org, tick)
    assert r["verdict"] == "SAFE DEGRADATION"
    assert r["scenarios"]["websocket_disconnect"]["blocked"] is True
    assert r["scenarios"]["stale_feed"]["blocked"] is True
    assert r["scenarios"]["invalid_digit"]["safe"] is True
    assert r["scenarios"]["risk_lock"]["safe"] is True


# ---------------- §32 strength ----------------
def test_eagle_strength_score_and_band():
    org, _, _ = make_organism(uniform_digits(300))
    for i in range(5):
        org.process(tick("R_100", i % 10, i))
    r = eagle_strength(org=org, engine=org._lightning, layer=org._layer)
    assert 0.0 <= r["EAGLE_STRENGTH"] <= 100.0
    assert r["band"] in ("Fortress", "Strong", "Stable", "Weak", "PRODUCTION PROHIBITED")
    assert set(r["components"]) >= {"data_integrity", "fault_tolerance",
                                    "execution_reliability", "state_consistency"}
    assert isinstance(r["production_allowed"], bool)


# ---------------- API ----------------
def test_forge_api():
    assert client.get("/forge/strength").status_code == 200
    r = client.post("/forge/survivability",
                    json={"balance": 1000, "stake": 1.0, "payout": 1.95}).json()
    assert r["verdict"] == "SURVIVABLE"
    d = client.get("/forge/disaster").json()
    assert d["verdict"] == "SURVIVES"
    sd = client.post("/forge/self-destruct",
                     json={"digits": uniform_digits(300), "kind": "MATCHES", "d": 1}).json()
    assert "verdict" in sd
    c = client.get("/forge/chaos").json()
    assert c["verdict"] in ("SAFE DEGRADATION", "UNSAFE")
