"""Pro Trader layer tests — stats primitives, gates, and signal decisions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.queue import BoundedTickQueue
from app.main import app
from app.models.tick import Tick
from app.services.pro_trader import (
    ProTrader,
    benjamini_hochberg,
    chi2_sf,
    wilson_lower_bound,
)

BASE_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_queue(digits, symbol="R_100"):
    q = BoundedTickQueue(maxlen=2000)
    for i, d in enumerate(digits):
        q.push(Tick(
            symbol=symbol,
            quote=round(1000 + d * 0.01, 2),
            timestamp=BASE_TS + timedelta(milliseconds=2 * i),
        ))
    return q


def uniform_digits(n=1000):
    return [i % 10 for i in range(n)]


def skewed_digits(n=1000, hot=7):
    """hot digit appears in 3 of every 10 positions (30%)."""
    others = [d for d in range(10) if d != hot]
    return [hot if i % 10 < 3 else others[i % 9] for i in range(n)]


# ---------------- math primitives ----------------
def test_chi2_sf_critical_values():
    assert chi2_sf(0.0) == pytest.approx(1.0)
    assert chi2_sf(16.919, 9) == pytest.approx(0.05, abs=1e-3)
    assert chi2_sf(21.666, 9) == pytest.approx(0.01, abs=1e-3)


def test_benjamini_hochberg_monotone():
    adj = benjamini_hochberg([0.001, 0.01, 0.04, 0.5, 0.9])
    assert adj[0] < 0.05
    assert adj[4] > 0.5
    assert all(0 <= a <= 1 for a in adj)


def test_wilson_lower_bound():
    lb = wilson_lower_bound(50, 100)
    assert 0.40 < lb < 0.41
    assert wilson_lower_bound(0, 0) == 0.0
    assert wilson_lower_bound(1000, 1000) > 0.99


# ---------------- board ----------------
def test_board_uniform_is_near_uniform_and_calm():
    pt = ProTrader(queue=make_queue(uniform_digits()))
    b = pt.board("R_100")
    assert b["ticks"] == 1000
    assert b["regime"] == "NEAR_UNIFORM"
    assert b["entropy_norm"] > 0.99
    assert b["table_fdr_significant"] is False
    assert b["data_ok"] is True
    assert set(b["windows"].keys()) == {"25", "50", "100", "250", "500", "1000"}


def test_board_flags_skewed_digit():
    pt = ProTrader(queue=make_queue(skewed_digits()))
    b = pt.board("R_100")
    assert b["digit_states"]["7"]["state"] == "HOT"
    assert b["digit_adj_p"]["7"] < 0.05
    assert b["table_fdr_significant"] is True
    # Dirichlet shrinkage in the small windows pulls the weighted estimate
    # below the raw 30% — that is the intended behaviour.
    assert 20.0 < b["digit_states"]["7"]["weighted_pct"] < 30.0


def test_board_gap_and_streak_tracking():
    digits = [0] * 50 + [1, 2, 3, 4, 5, 6, 7, 8, 9] * 15
    pt = ProTrader(queue=make_queue(digits))
    b = pt.board("R_100")
    assert b["digit_states"]["0"]["gap"] >= 100
    assert b["digit_states"]["0"]["max_streak"] == 50


def test_board_no_data():
    pt = ProTrader(queue=make_queue([], symbol="R_X"))
    assert pt.board("R_X")["decision"] == "NO_DATA"


# ---------------- opportunities & gates ----------------
def test_uniform_market_rejects_everything():
    pt = ProTrader(queue=make_queue(uniform_digits()))
    opps = pt.opportunities("R_100")
    assert opps
    assert all(o["decision"] == "REJECT" for o in opps)


def test_differs_rejected_at_default_payout_even_with_real_edge():
    """A genuine 30%-suppressed digit still loses at the 1.10x DIFFERS payout."""
    pt = ProTrader(queue=make_queue(skewed_digits()))
    sig = pt.signal("R_100")
    differs7 = next(
        o for o in pt.opportunities("R_100")
        if o["contract"] == "DIFFERS" and o["barrier"] == 7
    )
    assert differs7["estimated_probability"] == pytest.approx(0.70, abs=0.02)
    assert differs7["ev"] < 0  # 0.70 * 1.10 - 1 < 0
    assert differs7["decision"] == "REJECT"
    assert "ev" in differs7["decision_reasons"]
    # The board's top play is MATCHES on 7: a real 30% digit at 9x payout is
    # genuinely +EV, and the engine is right to accept it.
    assert sig["decision"] == "ACCEPT"
    assert sig["contract"] == "MATCHES" and sig["barrier"] == 7


def test_quoted_payout_unlocks_accept_when_all_gates_pass():
    """With a generous quoted payout the same edge must clear every gate."""
    pt = ProTrader(queue=make_queue(skewed_digits()))
    opps = pt.opportunities("R_100", payouts={"DIFFERS": 1.60})
    differs7 = next(o for o in opps if o["contract"] == "DIFFERS" and o["barrier"] == 7)
    assert differs7["payout_source"] == "quoted"
    assert differs7["breakeven_probability"] == pytest.approx(0.625, abs=1e-3)
    assert differs7["ev"] == pytest.approx(0.70 * 1.60 - 1, abs=0.03)
    assert differs7["wilson_lower_bound"] > differs7["breakeven_probability"]
    assert differs7["decision"] == "ACCEPT"
    assert differs7["decision_reasons"] == []


def test_latency_gate_rejects_stale_signal():
    pt = ProTrader(queue=make_queue(skewed_digits()))
    opps = pt.opportunities("R_100", payouts={"DIFFERS": 1.60}, latency_ms=999)
    differs7 = next(o for o in opps if o["contract"] == "DIFFERS" and o["barrier"] == 7)
    assert differs7["decision"] == "REJECT"
    assert "latency" in differs7["decision_reasons"]


def test_small_sample_blocks_trading():
    pt = ProTrader(queue=make_queue(skewed_digits(60)))
    opps = pt.opportunities("R_100")
    assert opps
    assert all(o["decision"] == "REJECT" for o in opps)
    assert all("sample" in o["decision_reasons"] for o in opps)


def test_over_under_probabilities_complement():
    pt = ProTrader(queue=make_queue(uniform_digits()))
    opps = pt.opportunities("R_100")
    over4 = next(o for o in opps if o["contract"] == "OVER" and o["barrier"] == 4)
    under5 = next(o for o in opps if o["contract"] == "UNDER" and o["barrier"] == 5)
    assert over4["estimated_probability"] == pytest.approx(
        under5["estimated_probability"], abs=1e-6)
    assert over4["estimated_probability"] == pytest.approx(0.5, abs=0.03)


# ---------------- scan ----------------
def test_scan_ranks_and_reports():
    pt = ProTrader(queue=make_queue(skewed_digits()))
    out = pt.scan(["R_100", "R_10"])
    assert len(out["markets"]) == 1  # R_10 has no data → skipped
    m = out["markets"][0]
    assert m["symbol"] == "R_100"
    assert m["best_contract"]
    assert m["decision"] in ("ACCEPT", "REJECT")
    assert "note" in out


# ---------------- API ----------------
def test_pro_trader_endpoints():
    client = TestClient(app)
    r = client.get("/pro-trader/R_100")
    assert r.status_code == 200
    assert r.json()["symbol"] == "R_100"
    r = client.get("/pro-trader/signal/R_100")
    assert r.status_code == 200
    assert "decision" in r.json()
    r = client.get("/pro-trader/scan")
    assert r.status_code == 200
    assert "markets" in r.json()
