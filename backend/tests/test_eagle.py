"""Hunting-Eagle Precision tests — three-layer vision, probability
consensus, signal stack, precision score bands, barrier ranking,
false-positive hunting and the precision scoreboard."""
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.bottom_up import BottomUpEngine
from app.services.eagle import EagleEngine, _prob_agreement
from app.services.persistence import journal_engine
from app.services.pro_trader import ProTrader
from app.services.super_profit import SuperProfitEngine

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def spike_digits(n=1000, spike=3, spike_len=40):
    digits = uniform_digits(n - spike_len)
    digits += [spike if i % 5 < 4 else (spike + 1) % 10 for i in range(spike_len)]
    return digits


def make_engine(digits, symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                    timestamp=BASE_TS + timedelta(milliseconds=2 * i)))
    layer = BottomUpEngine(queue=q, board_source=ProTrader(queue=q))
    layer.config.min_edge = 0.03
    ensemble = SuperProfitEngine(queue=q, board_source=ProTrader(queue=q),
                                 decision_layer=layer, rng_seed=5)
    return EagleEngine(queue=q, layer=layer, ensemble=ensemble), q, layer


def uid():
    return f"eagle-{uuid.uuid4().hex[:12]}"


def add(user, contract, result, pnl, stake=1.0, snap=None, digit=None, market="R_100"):
    return journal_engine.add_entry(
        market=market, contract=contract, digit=digit, stake=stake,
        result=result, pnl=pnl, data_quality=1.0, evidence_score=100.0,
        mode="paper", analysis_snapshot=snap, user_id=user,
    )


# ---------------- §6 probability consensus ----------------
def test_prob_agreement_high_and_low():
    assert _prob_agreement([0.552, 0.558, 0.555, 0.559]) > 0.9
    assert _prob_agreement([0.61, 0.54, 0.48, 0.57]) < 0.9
    assert _prob_agreement([0.5]) == 0.0


# ---------------- §2 eagle eye ----------------
def test_market_state_complete():
    eng, _, _ = make_engine(skewed_digits())
    st = eng.market_state("R_100")
    assert st["current_digit"] == skewed_digits()[-1]
    assert st["ticks"] == 1000
    assert set(st["horizons"]) == {"eye", "focus", "strike"}
    assert st["horizons"]["eye"]["1000"][7] > 0.25  # hot digit visible
    assert st["gaps"][7] < 10  # hot digit never goes long unseen
    assert st["data_ok"] is True


# ---------------- §4 multi-horizon confirmation ----------------
def test_horizon_agreement_on_persistent_skew():
    eng, _, _ = make_engine(skewed_digits())
    digits = skewed_digits()
    h = eng._horizon_agreement(digits, "MATCHES", 7, 1 / 9.0)
    assert h["consistent"] is True
    assert h["spike_only"] is False


def test_spike_only_is_low_confidence():
    eng, _, _ = make_engine(spike_digits())
    digits = spike_digits()
    h = eng._horizon_agreement(digits, "MATCHES", 3, 1 / 9.0)
    assert h["consistent"] is False
    assert h["spike_only"] is True  # strike hot, eye below the safety margin


# ---------------- §12 signal stack ----------------
def test_signal_stack_all_levels_present():
    eng, _, layer = make_engine(skewed_digits())
    base = layer.signal("R_100")
    if base["decision"] in ("WATCH", "EXECUTE"):
        digits = skewed_digits()
        consensus = eng._prob_estimates(digits, base["contract"], base["barrier"])
        horizon = eng._horizon_agreement(digits, base["contract"], base["barrier"],
                                         base["breakeven_probability"])
        stack = eng._signal_stack(base=base, ctx_state=eng.market_state("R_100"),
                                  consensus=consensus, horizon=horizon,
                                  uncertainty=0.1, contradiction=0.0,
                                  latency_ms=None, risk_blocked=False)
        assert len(stack["levels"]) == 12
        assert stack["passed"] is True


# ---------------- §25 strike ----------------
def test_strike_rejects_fair_market():
    eng, _, _ = make_engine(uniform_digits())
    d = eng.strike("R_100")
    assert d["final"] == "NO_TRADE"


def test_strike_card_shape_on_edge():
    eng, q, layer = make_engine(skewed_digits())
    layer.config.confirmation_ticks = 3
    layer.evaluate("R_100")
    for i, dgt in enumerate(skewed_digits(6)):
        t = Tick(symbol="R_100", quote=round(1000 + dgt * 0.01, 2),
                 timestamp=BASE_TS + timedelta(seconds=60, milliseconds=2 * i))
        q.push(t)
        layer.on_tick(t)
    d = eng.strike("R_100")
    for field in ("contract", "barrier", "probability", "probability_estimates",
                  "probability_consensus", "breakeven", "edge", "ev",
                  "uncertainty", "contradiction", "horizons", "signal_stack",
                  "entry_precision_score", "band", "final"):
        assert field in d
    assert d["final"] in ("STRIKE", "NO_TRADE")
    if d["final"] == "STRIKE":
        assert d["failed_gates"] == []
        assert d["band"] in ("A+", "A", "B", "C")
        assert d["horizons"]["consistent"] is True


def test_strike_uncertainty_gate():
    eng, _, _ = make_engine(skewed_digits())
    eng.config.max_uncertainty = 0.0
    d = eng.strike("R_100")
    assert d["final"] == "NO_TRADE"


# ---------------- §8/§9/§10 barrier precision ----------------
def test_rank_barriers_exact():
    eng, _, _ = make_engine(skewed_digits())
    r = eng.rank_barriers("R_100")
    assert set(r["families"]) == {"MATCHES", "OVER", "UNDER", "ODD", "EVEN", "DIFFERS"}
    assert len(r["families"]["MATCHES"]) == 10
    assert len(r["families"]["OVER"]) == 9
    assert len(r["families"]["UNDER"]) == 9
    m7 = next(x for x in r["families"]["MATCHES"] if x["barrier"] == 7)
    assert m7["decision"] == "PASS"
    assert r["best_per_family"]["MATCHES"] == "MATCHES 7"


# ---------------- §20 false-positive hunting ----------------
def test_false_positive_hunt_finds_pattern():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    for _ in range(10):
        add(user, "MATCHES 3", "loss", -1.0, snap={"regime": "CONCENTRATED"}, digit=3)
    for _ in range(10):
        add(user, "OVER 4", "win", 0.95, snap={"regime": "NORMAL"}, digit=4)
    r = eng.false_positive_hunt(user_id=user)
    assert r["losses_analyzed"] == 10
    assert "family" in r["patterns"]
    assert "MATCHES" in r["patterns"]["family"]["overrepresented_in_losses"]
    assert r["proposed_filters"]


# ---------------- §25 precision scoreboard ----------------
def test_scoreboard_grading_monotonicity_check():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    # A+ wins a lot, B loses a lot -> grading monotone OK
    for _ in range(9):
        add(user, "OVER 4", "win", 0.95, snap={"band": "A+", "estimated_probability": 0.6})
    add(user, "OVER 4", "loss", -1.0, snap={"band": "A+", "estimated_probability": 0.6})
    for _ in range(8):
        add(user, "MATCHES 1", "loss", -1.0, snap={"band": "B", "estimated_probability": 0.2})
    for _ in range(2):
        add(user, "MATCHES 1", "win", 8.0, snap={"band": "B", "estimated_probability": 0.2})
    sb = eng.scoreboard(user_id=user)
    assert sb["bands"]["A+"]["precision"] == 0.9
    assert sb["bands"]["B"]["precision"] == 0.2
    assert sb["grading_monotone"] is True
    assert sb["grading_verdict"] == "OK"


def test_scoreboard_flags_broken_grading():
    user = uid()
    eng, _, _ = make_engine(uniform_digits())
    for _ in range(8):
        add(user, "OVER 4", "loss", -1.0, snap={"band": "A+"})
    for _ in range(2):
        add(user, "OVER 4", "win", 0.95, snap={"band": "A+"})
    for _ in range(9):
        add(user, "MATCHES 1", "win", 8.0, snap={"band": "B"})
    add(user, "MATCHES 1", "loss", -1.0, snap={"band": "B"})
    sb = eng.scoreboard(user_id=user)
    assert sb["grading_monotone"] is False
    assert "BROKEN" in sb["grading_verdict"]


# ---------------- API ----------------
def test_api_endpoints():
    assert client.get("/eagle/state/R_100").status_code == 200
    d = client.get("/eagle/strike/R_100").json()
    assert d["final"] in ("STRIKE", "NO_TRADE")
    assert client.get("/eagle/barriers/R_100").status_code == 200
    assert client.get("/eagle/false-positives").status_code == 200
    assert client.get("/eagle/scoreboard").status_code == 200
    p = client.post("/eagle/strike/R_100", json={"payouts": {"OVER": 1.9}}).json()
    assert p["final"] in ("STRIKE", "NO_TRADE")
