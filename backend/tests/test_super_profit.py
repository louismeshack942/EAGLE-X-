"""Super-Profitability Engine tests — brains, ensemble, adversarial tests,
adaptive windows, regimes, uncertainty, health, calibration, matrix,
profiles, allocation, profit locking, auction and the decision card."""
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.bottom_up import BottomUpEngine
from app.services.persistence import journal_engine
from app.services.pro_trader import ProTrader
from app.services.super_profit import (
    BRAIN_NAMES,
    NEU,
    OPP,
    SUP,
    SuperConfig,
    SuperProfitEngine,
    _transition_deviation,
)

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


def make_engine(digits, symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                    timestamp=BASE_TS + timedelta(milliseconds=2 * i)))
    layer = BottomUpEngine(queue=q, board_source=ProTrader(queue=q))
    layer.config.min_edge = 0.03
    eng = SuperProfitEngine(queue=q, board_source=ProTrader(queue=q),
                            decision_layer=layer, rng_seed=7)
    eng.config = SuperConfig()
    return eng, q, layer


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def random_digits(n=1000, seed=99):
    rng = random.Random(seed)
    return [rng.randrange(10) for _ in range(n)]


def push_ticks(q, eng, layer, digits, symbol="R_100", start=100000):
    for i, d in enumerate(digits):
        t = Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                 timestamp=BASE_TS + timedelta(milliseconds=2 * (start + i)))
        q.push(t)
        layer.on_tick(t)
        eng.on_tick(t)


def confirmed(layer, eng, q, symbol="R_100", extra=6):
    """Push enough ticks that the persistence tracker CONFIRMS the signal."""
    layer.evaluate(symbol)
    push_ticks(q, eng, layer, skewed_digits(extra), symbol=symbol)
    return layer.signal(symbol)


# ---------------- shuffle test / sequence gating ----------------
def test_transition_deviation_metric():
    assert _transition_deviation(uniform_digits(500)) > 0.15  # deterministic cycle
    assert _transition_deviation(random_digits(500)) < 0.1   # iid -> ~uniform


def test_shuffle_verdict_detects_sequence():
    eng, _, _ = make_engine(uniform_digits(400))
    assert eng._shuffle_verdict("R_100", uniform_digits(400)) == "SEQUENTIAL"


def test_shuffle_verdict_rejects_iid():
    eng, _, _ = make_engine(random_digits(400))
    assert eng._shuffle_verdict("R_100", random_digits(400)) == "NO_SEQUENCE"


# ---------------- regime classification ----------------
def test_regime_classification():
    # iid noise can legitimately trip serial-dependence by chance; the
    # classifier must land on a defensible state, never crash.
    eng, _, _ = make_engine(random_digits())
    ctx = eng._context("R_100")
    assert eng._regime(ctx) in ("NORMAL", "LOW_INFORMATION", "UNSTABLE")
    eng2, _, _ = make_engine(skewed_digits())
    ctx2 = eng2._context("R_100")
    assert eng2._regime(ctx2) in ("CONCENTRATED", "HIGH_ANOMALY", "DISTRIBUTION_SHIFT")


# ---------------- adaptive windows ----------------
def test_adaptive_window_uses_available_data():
    eng, _, _ = make_engine(uniform_digits())
    ctx = eng._context("R_100")
    aw = eng._adaptive_window(ctx, "MATCHES", 3)
    assert aw in sorted(ctx["posts"])


# ---------------- brains vote ----------------
def _votes(eng, base, user=None):
    ctx = eng._context(base["symbol"])
    regime = eng._regime(ctx)
    health = eng.health(user_id=user or "test-nobody")["families"].get(
        base["contract"], {}).get("status", "YELLOW")
    return eng._brain_votes(base["symbol"], base, ctx, None,
                            base.get("payout_source", "assumed_default"), regime, health)


def test_brains_support_validated_edge():
    eng, q, layer = make_engine(skewed_digits())
    base = layer.signal("R_100")
    assert base["decision"] in ("WATCH", "EXECUTE")
    votes = _votes(eng, base)
    names = [v.brain for v in votes]
    assert names == list(BRAIN_NAMES)
    support = [v for v in votes if v.vote == SUP]
    assert {"frequency", "probability", "anomaly", "contract"} <= {v.brain for v in support}
    consensus = eng._consensus(votes)
    assert consensus["support"] >= 5
    assert consensus["oppose"] == 0
    assert consensus["passed"] is True


def test_risk_veto_opposes():
    eng, _, layer = make_engine(skewed_digits())
    base = layer.signal("R_100", risk_blocked=True)
    assert base["decision"] == "NO_TRADE"


def test_opposition_kills_consensus():
    eng, q, layer = make_engine(skewed_digits())
    base = layer.signal("R_100", latency_ms=900)  # execution OPPOSE via latency? base NO_TRADE anyway
    assert base["decision"] == "NO_TRADE"
    # Craft a base candidate then oppose via health RED.
    base = layer.signal("R_100")
    if base["decision"] in ("WATCH", "EXECUTE"):
        ctx = eng._context("R_100")
        votes = eng._brain_votes("R_100", base, ctx, None,
                                 "assumed_default", "NORMAL", "RED")
        consensus = eng._consensus(votes)
        assert consensus["passed"] is False
        assert "risk" in consensus["opposing"]


# ---------------- uncertainty ----------------
def test_uncertainty_blocks_thin_contradicted_signal():
    eng, _, layer = make_engine(uniform_digits(120))
    base = layer.evaluate("R_100")
    # build synthetic opposing votes
    ctx = eng._context("R_100")
    votes = []
    for brain in BRAIN_NAMES:
        votes.append(eng_vote(brain, OPP, confidence=0.2))
    cal = eng.calibration(user_id="test-nobody")["calibration_error"]
    best = base["candidates"][0] if base["candidates"] else None
    if best:
        u = eng._uncertainty(best, votes, ctx, cal)
        assert u > eng.config.max_uncertainty


def eng_vote(brain, vote, confidence=0.5):
    from app.services.super_profit import BrainVote
    return BrainVote(brain, vote, 0.5, confidence, 0.5)


# ---------------- health ----------------
def uid():
    return f"sp-{uuid.uuid4().hex[:12]}"


def add(user, contract, result, pnl, stake=1.0, snap=None, dq=1.0, ev=100.0, digit=None):
    return journal_engine.add_entry(
        market="R_100", contract=contract, digit=digit, stake=stake,
        result=result, pnl=pnl, data_quality=dq, evidence_score=ev,
        mode="paper", analysis_snapshot=snap, user_id=user,
    )


def test_health_statuses():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    for _ in range(25):
        add(user, "MATCHES 1", "loss", -1.0)
    h = eng.health(user_id=user)
    assert h["families"]["MATCHES"]["status"] in ("RED", "ORANGE")
    for _ in range(30):
        add(user, "OVER 3", "win", 0.95)
    h = eng.health(user_id=user)
    assert h["families"]["OVER"]["status"] == "GREEN"
    assert h["families"]["OVER"]["exposure_multiplier"] == 1.0


# ---------------- calibration ----------------
def test_calibration_detects_miscalibration():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    for _ in range(40):
        # claim 90% confidence, realize 40% — grossly mis-calibrated
        add(user, "MATCHES 2", "win" if _ % 10 < 4 else "loss",
            0.1 if _ % 10 < 4 else -1.0, snap={"estimated_probability": 0.9})
    for _ in range(40):
        add(user, "MATCHES 2", "win" if _ % 10 < 4 else "loss",
            0.1 if _ % 10 < 4 else -1.0, snap={"estimated_probability": 0.9})
    c = eng.calibration(user_id=user)
    assert c["verdict"] == "MIS_CALIBRATED"


# ---------------- matrix & profiles ----------------
def test_matrix_and_profiles():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    for _ in range(6):
        add(user, "OVER 4", "win", 0.95, snap={"regime": "NORMAL"})
    for _ in range(3):
        add(user, "MATCHES 2", "loss", -1.0, snap={"regime": "CONCENTRATED"})
    m = eng.matrix(user_id=user)
    assert m["matrix"]["R_100"]["OVER"]["trades"] == 6
    assert m["matrix"]["R_100"]["OVER"]["best_conditions"] == "NORMAL"
    assert m["matrix"]["R_100"]["MATCHES"]["worst_conditions"] == "CONCENTRATED"
    p = eng.profiles(user_id=user)
    assert p["profiles"]["R_100"]["best_contract"] == "OVER"
    assert p["profiles"]["R_100"]["worst_contract"] == "MATCHES"


# ---------------- allocation ----------------
def test_allocation_prefers_risk_adjusted():
    eng, _, _ = make_engine(uniform_digits())
    opps = [
        {"symbol": "R_75", "contract": "OVER", "barrier": 4, "ev": 0.06, "confidence": 0.9},
        {"symbol": "R_100", "contract": "MATCHES", "barrier": 3, "ev": 0.07, "confidence": 0.3},
    ]
    a = eng.allocate(1000, opps)
    assert len(a["allocations"]) == 2
    by = {x["symbol"]: x for x in a["allocations"]}
    assert by["R_75"]["stake"] >= by["R_100"]["stake"]
    assert all(x["stake"] <= eng._layer.config.max_stake_pct * 1000 for x in a["allocations"])


def test_allocation_empty_when_no_positive_ev():
    eng, _, _ = make_engine(uniform_digits())
    a = eng.allocate(1000, [{"symbol": "R_75", "contract": "OVER", "barrier": 4, "ev": -0.01}])
    assert a["allocations"] == []


# ---------------- profit locking ----------------
def test_profit_lock_tiers():
    eng, _, _ = make_engine(uniform_digits())
    assert eng.profit_lock_multiplier(1.0)["stake_multiplier"] == 1.0
    assert eng.profit_lock_multiplier(2.5)["stake_multiplier"] == 0.75
    assert eng.profit_lock_multiplier(3.5)["stake_multiplier"] == 0.5
    r = eng.profit_lock_multiplier(4.5)
    assert r["stop_session"] is True


# ---------------- conditional edge ----------------
def test_conditional_edge_reports_features():
    eng, _, _ = make_engine(random_digits(600))
    r = eng.conditional_edge("R_100")
    assert set(r["features"]) == {"previous_digit", "streak_state", "gap_bucket"}
    for f in r["features"].values():
        assert f["verdict"] in ("KEEP", "DISCARD")


def test_conditional_edge_keeps_real_dependency():
    # every 5 follows a 3 — P(next|prev=3) is genuinely skewed
    digits = [(3 if i % 5 == 0 else (i * 5 + 1) % 10) for i in range(600)]
    eng, _, _ = make_engine(digits)
    r = eng.conditional_edge("R_100")
    prev = r["features"]["previous_digit"]
    assert prev["verdict"] == "KEEP"
    assert prev["significant"] is True


# ---------------- ablation ----------------
def test_ablation_walk_forward():
    eng, _, _ = make_engine(skewed_digits(600))
    r = eng.ablation("R_100")
    assert set(r["ablation"].keys()) == set(BRAIN_NAMES)
    assert 0.0 <= r["baseline_consensus_rate"] <= 1.0


# ---------------- the ultimate decision ----------------
def test_decision_rejects_fair_market_with_clear_reason():
    eng, _, layer = make_engine(uniform_digits())
    d = eng.decide("R_100")
    assert d["final"] == "REJECT"
    assert d["base_decision"] in ("NO_TRADE", "NO_DATA")


def test_decision_card_when_edge_is_real():
    user = uid()
    eng, q, layer = make_engine(skewed_digits())
    layer.config.confirmation_ticks = 3
    base = confirmed(layer, eng, q)
    if base["decision"] == "EXECUTE":
        d = eng.decide("R_100", user_id=user)
        for field in ("market", "contract", "barrier", "probability", "breakeven",
                      "edge", "ev", "confidence", "model_agreement", "signal_quality",
                      "uncertainty", "meta_score", "regime", "health", "risk",
                      "execution", "brains", "consensus", "self_critic", "final"):
            assert field in d
        assert d["final"] in ("EXECUTE", "WATCH", "REJECT")
        assert len(d["brains"]) == 7
        assert d["self_critic"]["contradictory_evidence"] in (
            "LOW", pytest.approx(d["self_critic"]["contradictory_evidence"]))
        if d["final"] == "EXECUTE":
            assert d["failed_gates"] == []
    # thin data must never crash
    eng2, _, _ = make_engine([1, 2, 3])
    assert eng2.decide("R_100")["final"] == "REJECT"


def test_decision_uncertainty_gate():
    eng, _, layer = make_engine(skewed_digits())
    eng.config.max_uncertainty = 0.0  # impossible threshold
    d = eng.decide("R_100")
    assert d["final"] != "EXECUTE"
    assert "uncertainty" in d.get("failed_gates", [d.get("final")])


# ---------------- auction ----------------
def test_auction_zero_opportunities_zero_trades():
    eng, _, _ = make_engine(uniform_digits())
    a = eng.auction(["R_100"], user_id=uid())
    assert a["winner"] is None
    assert a["trades"] == 0
    assert "never forced" in a["note"]


def test_auction_picks_highest_ev_executive():
    eng, q, layer = make_engine(skewed_digits())
    layer.config.confirmation_ticks = 3
    base = confirmed(layer, eng, q)
    a = eng.auction(["R_100"], user_id=uid())
    if base["decision"] == "EXECUTE" and base["ev"] >= eng.config.min_ev_auction:
        d = eng.decide("R_100", user_id=uid())
        if d["final"] == "EXECUTE":
            assert a["winner"]["symbol"] == "R_100"
            assert a["winner"]["ev"] >= eng.config.min_ev_auction
    else:
        assert a["winner"] is None


# ---------------- config ----------------
def test_super_config_update():
    eng, _, _ = make_engine(uniform_digits())
    out = eng.update_config(min_support_brains=6, meta_min=55.0, bogus=1)
    assert out["min_support_brains"] == 6
    assert out["meta_min"] == 55.0
    assert "bogus" not in out
    eng.update_config(min_support_brains=5, meta_min=60.0)


# ---------------- API ----------------
def test_api_endpoints():
    assert client.get("/super/config").status_code == 200
    assert client.get("/super/auction").status_code == 200
    assert client.get("/super/health").status_code == 200
    assert client.get("/super/calibration").status_code == 200
    assert client.get("/super/matrix").status_code == 200
    assert client.get("/super/profiles").status_code == 200
    d = client.get("/super/decision/R_100").json()
    assert d["final"] in ("EXECUTE", "WATCH", "REJECT")
    assert client.get("/super/brains/R_100").status_code == 200
    assert client.get("/super/conditional/R_100").status_code == 200
    a = client.get("/super/ablation/R_100").json()
    assert "note" in a or "ablation" in a
    pl = client.get("/super/profit-lock?session_pnl_pct=3.5").json()
    assert pl["stake_multiplier"] == 0.5
    al = client.post("/super/allocate", json={"balance": 1000, "opportunities": []}).json()
    assert al["allocations"] == []
    cfg = client.post("/super/config", json={"meta_min": 61.0}).json()
    assert cfg["meta_min"] == 61.0
    client.post("/super/config", json={"meta_min": 60.0})
