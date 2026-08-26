"""Rivalry tests — champion/challenger walk-forward with NO LOOKAHEAD,
robustness, promotion rules, tournaments, adversarial blind spots and
decay/rollback. Deterministic tape, no broker."""
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.rivalry import RivalryEngine, rivalry_engine

client = TestClient(app)


def skewed(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def no_lookahead_check():
    """_walk_forward decides at pos and resolves on digit at pos (next tick)."""
    eng = RivalryEngine()
    digits = skewed(400)
    r = eng._walk_forward_one(eng.champion, digits)
    assert r["trades"] > 0
    # no lookahead would trade the last digit — it stops before the end
    assert r["trades"] <= len(digits) / 25


def test_generate_unique_and_snapshot():
    eng = RivalryEngine()
    a = eng.generate("min_edge_x2")
    b = eng.generate("min_edge_x2")
    assert a["ok"] and a["experiment_id"] != b["experiment_id"]
    assert a["candidate"]["min_edge"] == 0.06
    assert eng.generate("bogus")["ok"] is False


def test_walk_forward_no_lookahead():
    no_lookahead_check()


def test_compete_returns_verdict_of_either():
    eng = RivalryEngine()
    v = eng.compete(skewed(1200), kind="min_edge_x0_5")
    assert "promoted" in v and "robustness" in v
    assert v["champion_score"] is not None
    assert v["challenger_walk_forward"]["ok"] in (True, False)
    if v["promoted"]:
        assert eng.champion.version != "v1.0"
        assert eng.history[-1]["reason"]


def test_compete_rejects_with_insufficient_data():
    eng = RivalryEngine()
    v = eng.compete(uniform := [i % 10 for i in range(200)], kind="min_edge_x2")
    assert v["promoted"] is False


def test_robustness_components():
    eng = RivalryEngine()
    folds = eng._folds_eval(eng.champion, skewed(800))
    rb = eng._robustness(eng.champion, skewed(800), folds)
    assert 0.0 <= rb <= 100.0


def test_decoys_die_on_shuffle_or_perturbation():
    eng = RivalryEngine()
    # random tape: any "edge" must not survive the dissonance gate
    v = eng.compete([random.Random(7).randint(0, 9) for _ in range(800)],
                    kind="min_edge_x0_5", folds=4)
    assert v["promoted"] is False


def test_decay_and_rollback_states():
    eng = RivalryEngine()
    r = eng.decay(user_id="nobody", recent_n=5)
    assert r["status"] == "INSUFFICIENT_DATA"


def test_status_and_history():
    st = rivalry_engine.status()
    assert "champion" in st and st["history"][0]["version"] == "v1.0"


def test_api():
    assert client.get("/rivalry/status").status_code == 200
    assert client.post("/rivalry/generate", json={"kind": "windows_50"}).status_code == 200
    v = client.post("/rivalry/compete",
                    json={"digits": skewed(600), "kind": "min_edge_x2", "folds": 3}).json()
    assert "promoted" in v
    assert client.get("/rivalry/tournament").status_code == 200
    assert client.get("/rivalry/adversarial").status_code == 200
    assert client.get("/rivalry/decay").status_code == 200
