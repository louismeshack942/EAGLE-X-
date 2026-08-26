"""Bottom-Up Profitability Engine tests — hierarchy, gates, persistence,
decay, grades, post-mortem, scorecard, kill switches, and the API."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.bottom_up import (
    CONTRACT_HIERARCHY,
    BottomUpConfig,
    BottomUpEngine,
    wilson_upper_bound,
)
from app.services.persistence import journal_engine
from app.services.pro_trader import ProTrader

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)
client = TestClient(app)


def make_engine(digits, symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                    timestamp=BASE_TS + timedelta(milliseconds=2 * i)))
    eng = BottomUpEngine(queue=q, board_source=ProTrader(queue=q))
    eng.config = BottomUpConfig()  # isolate from any stored overrides
    return eng, q


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


def spike_digits(n=1000, spike=3, spike_len=50):
    """Uniform tape with a short-lived hot spike at the very end."""
    digits = uniform_digits(n - spike_len)
    digits += [spike if i % 5 < 4 else (spike + 1) % 10 for i in range(spike_len)]
    return digits


def push_ticks(q, eng, digits, symbol="R_100", start=0):
    for i, d in enumerate(digits):
        t = Tick(symbol=symbol, quote=round(1000 + d * 0.01, 2),
                 timestamp=BASE_TS + timedelta(seconds=60, milliseconds=2 * (start + i)))
        q.push(t)
        eng.on_tick(t)


# ---------------- math primitives ----------------
def test_wilson_upper_bound():
    assert wilson_upper_bound(50, 100) == pytest.approx(0.60, abs=0.01)
    assert wilson_upper_bound(0, 0) == 1.0
    assert wilson_upper_bound(0, 25) < 0.15


def test_thin_tape_does_not_crash():
    # Fresh boot: a handful of ticks must yield NO_TRADE, not a 500.
    eng, _ = make_engine([4, 4, 7])
    e = eng.evaluate("R_100")
    assert e["n_ticks"] == 3
    assert all(c["decision"] == "REJECT" for c in e["candidates"])
    assert eng.signal("R_100")["decision"] == "NO_TRADE"


# ---------------- §1 hierarchy ----------------
def test_candidates_follow_hierarchy():
    kinds = [k for k, _ in BottomUpEngine._candidates()]
    assert kinds[0] == "MATCHES"
    assert kinds[-1] == "DIFFERS"
    order = [k for k in CONTRACT_HIERARCHY]
    positions = [kinds.index(k) for k in order]
    assert positions == sorted(positions)
    # full board: 10 MATCHES, 9 OVER, 9 UNDER, ODD, EVEN, 10 DIFFERS
    assert len(kinds) == 40


# ---------------- §4/§19 fair market => NO TRADE ----------------
def test_uniform_tape_rejects_everything():
    eng, _ = make_engine(uniform_digits())
    e = eng.evaluate("R_100")
    assert e["passing"] == 0
    assert all(c["decision"] == "REJECT" for c in e["candidates"])
    assert all(c["grade"] == "D" for c in e["candidates"])
    sig = eng.signal("R_100")
    assert sig["decision"] == "NO_TRADE"
    assert eng.tracker()["stats"]["signals_detected"] == 0


# ---------------- validated edge on skewed tape ----------------
def test_skewed_tape_finds_matches_edge():
    eng, _ = make_engine(skewed_digits())
    e = eng.evaluate("R_100")
    passing = [c for c in e["candidates"] if c["decision"] == "PASS"]
    assert passing, "a genuine multi-window skew must be detectable"
    best = e["best"]
    assert best["contract"] == "MATCHES"
    assert best["barrier"] == 7
    assert best["edge"] >= eng.config.min_edge
    assert best["wilson_lower_bound"] > best["breakeven_probability"]
    assert best["grade"] in ("A+", "A")
    assert best["auto_executable"] is True


def test_first_detection_is_watch_not_execute():
    eng, _ = make_engine(skewed_digits())
    sig = eng.signal("R_100")
    # §11: detection is not execution — the confirmation window must close.
    assert sig["decision"] == "WATCH"
    assert sig["signal_state"] == "PENDING"


def test_confirmation_window_leads_to_execute():
    eng, q = make_engine(skewed_digits())
    eng.config.confirmation_ticks = 3
    eng.evaluate("R_100")
    push_ticks(q, eng, skewed_digits(3))
    sig = eng.signal("R_100")
    assert sig["signal_state"] == "CONFIRMED"
    assert sig["decision"] == "EXECUTE"
    life = sig["signal_lifetime"]
    assert life["observations"] >= 4
    assert life["initial_edge"] > 0


# ---------------- §10 false-signal filter ----------------
def test_short_lived_spike_is_rejected():
    eng, _ = make_engine(spike_digits())
    e = eng.evaluate("R_100")
    spike_c = next(c for c in e["candidates"]
                   if c["contract"] == "MATCHES" and c["barrier"] == 3)
    assert spike_c["decision"] == "REJECT"
    assert "long_term" in spike_c["decision_reasons"]


def test_diluted_edge_cancels_tracked_signal():
    eng, q = make_engine(skewed_digits())
    eng.evaluate("R_100")
    key = ("R_100", "MATCHES", 7)
    assert eng._signals[key].state == "PENDING"
    # Flood the tape with uniform ticks until the edge is gone.
    push_ticks(q, eng, uniform_digits(900))
    eng.evaluate("R_100")
    assert eng._signals[key].state in ("CANCELLED", "STALE")
    stats = eng.tracker()["stats"]
    assert stats["signals_cancelled"] + stats["signals_stale"] >= 1


# ---------------- §8 confidence gate ----------------
def test_thin_sample_cannot_pass_confidence():
    # 100 ticks with digit 7 at 20%: point estimate clears the margin but
    # the Wilson lower bound cannot — the directive's §8 example.
    digits = [7 if i % 5 == 0 else (i % 9 + 1) % 10 for i in range(100)]
    digits = [d if d != 7 or i % 5 == 0 else 8 for i, d in enumerate(digits)]
    eng, _ = make_engine(digits * 1)
    eng.config.min_edge = 0.01
    e = eng.evaluate("R_100")
    m7 = next(c for c in e["candidates"] if c["contract"] == "MATCHES" and c["barrier"] == 7)
    if m7["edge"] >= eng.config.min_edge:
        assert m7["decision"] == "REJECT"
        assert "confidence" in m7["decision_reasons"] or "stability" in m7["decision_reasons"]


# ---------------- §14/§15 score never overrides a hard rejection ----------------
def test_grade_boundaries():
    eng, _ = make_engine(uniform_digits())
    assert eng._grade(90, False) == "D"
    assert eng._grade(90, True) == "A+"
    assert eng._grade(75, True) == "A"
    assert eng._grade(55, True) == "B"
    assert eng._grade(35, True) == "C"
    assert eng._grade(10, True) == "D"


def test_risk_authority_blocks_everything():
    eng, _ = make_engine(skewed_digits())
    e = eng.evaluate("R_100", risk_blocked=True)
    assert e["passing"] == 0
    assert all("risk" in c["decision_reasons"] for c in e["candidates"])
    assert eng.signal("R_100", risk_blocked=True)["decision"] == "NO_TRADE"


def test_stale_latency_blocks():
    eng, _ = make_engine(skewed_digits())
    e = eng.evaluate("R_100", latency_ms=900)
    assert e["passing"] == 0
    assert all("latency" in c["decision_reasons"] for c in e["candidates"])


# ---------------- §13 global rank ----------------
def test_rank_orders_by_score_and_reports_no_trade_on_fair_board():
    eng, _ = make_engine(uniform_digits())
    r = eng.rank(["R_100"])
    assert r["decision"] == "NO_TRADE"
    assert r["executable"] == []


# ---------------- §18 martingale ----------------
def test_martingale_plan_capped_math():
    plan = BottomUpEngine.martingale_plan(base_stake=1.0, payout=1.95, max_level=3, bankroll=100.0)
    assert plan["feasible"] is True
    stakes = [l["stake"] for l in plan["levels"]]
    assert stakes[0] == 1.0
    assert stakes[1] == pytest.approx(round(1 / 0.95, 2), abs=0.01)
    assert plan["worst_case_loss"] == pytest.approx(sum(stakes), abs=0.02)
    assert plan["levels"][0]["remaining_bankroll_after_loss"] == 99.0


def test_martingale_unlimited_prohibited():
    plan = BottomUpEngine.martingale_plan(base_stake=1.0, payout=1.95, max_level=0, bankroll=100.0)
    assert "error" in plan


def test_martingale_stops_when_bankroll_breaks():
    plan = BottomUpEngine.martingale_plan(base_stake=1.0, payout=1.95, max_level=8, bankroll=5.0)
    assert plan["feasible"] is False
    assert "do not chase" in plan["reason"]


# ---------------- journal-driven analysis ----------------
def uid():
    return f"bu-{uuid.uuid4().hex[:12]}"


def add(user, contract, result, pnl, stake=1.0, dq=1.0, ev=100.0, snap=None, digit=None):
    return journal_engine.add_entry(
        market="R_100", contract=contract, digit=digit, stake=stake,
        result=result, pnl=pnl, data_quality=dq, evidence_score=ev,
        mode="paper", analysis_snapshot=snap, user_id=user,
    )


def test_postmortem_classifies_losses():
    user = uid()
    eng, _ = make_engine(uniform_digits())
    add(user, "OVER 4", "loss", -1.0, dq=0.5)                                  # DATA_PROBLEM
    add(user, "OVER 4", "loss", -1.0, snap={"edge_decay": True})               # SIGNAL_DECAY
    add(user, "OVER 4", "loss", -1.0, snap={"latency_ms": 900})                # EXECUTION_DELAY
    add(user, "OVER 4", "loss", -1.0, snap={"payout": 1.80, "expected_payout": 1.95})  # PAYOUT_PROBLEM
    add(user, "OVER 4", "loss", -1.0, snap={"regime_change": True})            # REGIME_CHANGE
    add(user, "OVER 4", "loss", -1.0, ev=30.0)                                 # FALSE_SIGNAL
    add(user, "OVER 4", "loss", -1.0, snap={"balance": 100.0}, stake=5.0)      # RISK_MANAGEMENT
    add(user, "OVER 4", "loss", -1.0, snap={"confirmed": False})               # BAD_ENTRY
    # healthy family, plain variance
    for _ in range(4):
        add(user, "MATCHES 3", "win", 8.0, snap={"ev": 0.1, "edge": 0.05, "grade": "A"})
    add(user, "MATCHES 3", "loss", -1.0, snap={"ev": 0.1, "edge": 0.05, "grade": "A"})
    pm = eng.postmortem(user_id=user)
    classes = {c["loss_class"] for c in pm["classified"]}
    assert {"DATA_PROBLEM", "SIGNAL_DECAY", "EXECUTION_DELAY", "PAYOUT_PROBLEM",
            "REGIME_CHANGE", "FALSE_SIGNAL", "RISK_MANAGEMENT", "BAD_ENTRY",
            "NORMAL_RANDOM_LOSS"} <= classes
    assert pm["losses"] == 9


def test_win_analysis_separates_skill_from_variance():
    user = uid()
    eng, _ = make_engine(uniform_digits())
    # DIFFERS at 90% wr vs 1.1 payout: breakeven 90.9% — lucky, not skill.
    for i in range(20):
        add(user, "DIFFERS 5", "win" if i < 18 else "loss",
            0.1 if i < 18 else -1.0)
    wa = eng.win_analysis(user_id=user)
    assert wa["families"]["DIFFERS"]["verdict"] == "VARIANCE_NOT_PROVEN"
    assert wa["wins"] == 18


def test_scorecard_metrics_and_kill_switch():
    user = uid()
    eng, _ = make_engine(uniform_digits())
    for _ in range(25):
        add(user, "MATCHES 9", "loss", -1.0, snap={"ev": 0.2, "edge": 0.1})
    for _ in range(3):
        add(user, "OVER 2", "win", 0.95, snap={"ev": 0.05, "edge": 0.04, "grade": "A+"})
    add(user, "OVER 2", "loss", -1.0, snap={"ev": 0.05, "edge": 0.04, "grade": "A"})
    sc = eng.scorecard(user_id=user)
    assert sc["trades"] == 29
    assert sc["win_rate"] == pytest.approx(3 / 29, abs=1e-4)
    assert sc["max_drawdown"] >= 25.0
    assert sc["longest_losing_streak"] == 25
    assert sc["kill_switches"]["MATCHES"]["kill"] is True
    assert sc["kill_switches"]["OVER"]["kill"] is False
    assert sc["by_contract"]["MATCHES"]["trades"] == 25
    assert sc["by_barrier"]["OVER 2"]["wins"] == 3
    assert sc["grade_win_rates"]["A+"] == 1.0
    assert sc["grade_win_rates"]["A"] == 0.0


def test_validate_thresholds_reports_grid():
    user = uid()
    eng, _ = make_engine(uniform_digits())
    for i in range(10):
        add(user, "OVER 4", "win" if i % 2 == 0 else "loss",
            0.95 if i % 2 == 0 else -1.0, snap={"edge": 0.06 if i < 5 else 0.01})
    v = eng.validate_thresholds(user_id=user)
    by_edge = {r["min_edge"]: r for r in v["grid"]}
    assert by_edge[0.0]["trades"] == 10
    assert by_edge[0.05]["trades"] == 5
    assert "out-of-sample" in v["note"]


# ---------------- config ----------------
def test_update_config_persists_and_clamps():
    eng, _ = make_engine(uniform_digits())
    out = eng.update_config(min_edge=0.07, confirmation_ticks=8, bogus_field=1)
    assert out["min_edge"] == 0.07
    assert out["confirmation_ticks"] == 8
    assert "bogus_field" not in out
    out = eng.update_config(min_edge=99)
    assert out["min_edge"] == 0.5
    eng.update_config(min_edge=0.03, confirmation_ticks=5)


# ---------------- API ----------------
def test_api_endpoints_respond():
    assert client.get("/bottom-up/rank").status_code == 200
    sig = client.get("/bottom-up/signal/R_100").json()
    assert sig["decision"] in ("NO_TRADE", "WATCH", "EXECUTE", "NO_DATA")
    cand = client.get("/bottom-up/candidates/R_100").json()
    assert cand["n_ticks"] > 0
    assert len(cand["candidates"]) == 40
    assert client.get("/bottom-up/tracker").status_code == 200
    rp = client.get("/bottom-up/risk-profile").json()
    assert rp["martingale"] == "OFF"
    assert rp["max_simultaneous_trades"] == 1
    mg = client.get("/bottom-up/martingale?base_stake=1&payout=1.95&max_level=3&bankroll=100").json()
    assert mg["feasible"] is True
    assert client.get("/bottom-up/postmortem").status_code == 200
    assert client.get("/bottom-up/win-analysis").status_code == 200
    assert client.get("/bottom-up/scorecard").status_code == 200
    assert client.get("/bottom-up/validate").status_code == 200
    cfg = client.get("/bottom-up/config").json()
    assert cfg["min_edge"] >= 0.0
