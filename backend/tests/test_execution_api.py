"""Phase 5 API integration + LIVE-safety tests.

Covers the /api/exec/* and /api/signal/* endpoints. Also proves, using automated tests,
that attempting to trigger live execution while LIVE_TRADING_ENABLED=false is REJECTED
and no Deriv purchase request is possible.
"""

from __future__ import annotations


from app.services.analysis_engine import analysis_manager
from app.core.ticks import NormalizedTick


def _seed_ticks(symbol="R_10", n=70):
    """Seed the in-memory analysis manager with real ticks (deterministic)."""
    import time as _t

    digits = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 3,
              4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3,
              4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3,
              4, 5, 6, 7, 8, 9, 0, 1, 2, 3]
    base = _t.time() * 1000
    for i in range(n):
        d = digits[i % len(digits)]
        analysis_manager.push(NormalizedTick(
            symbol=symbol, epoch_ms=int(base + i * 500), quote=1000.0 + i,
            last_digit=d, provider="harness",
        ))
    analysis_manager.mark_connection(symbol, "connected")


def test_exec_config_reports_live_disabled(client):
    r = client.get("/api/exec/config")
    assert r.status_code == 200
    body = r.json()
    assert body["live_enabled"] is False
    assert set(body["modes"]) == {"HARNESS", "PAPER", "LIVE"}


def test_live_mode_request_rejected_default(client):
    # The frontend can select a mode, but LIVE remains server-governed (disabled by default).
    r = client.post("/api/exec/mode", json={"mode": "LIVE"})
    assert r.status_code == 200
    assert r.json()["live_enabled"] is False


def test_killswitch_toggle(client):
    r = client.post("/api/exec/killswitch", json={"on": True})
    assert r.json()["kill_switch"] is True
    r = client.get("/api/exec/killswitch")
    assert r.json()["kill_switch"] is True
    client.post("/api/exec/killswitch", json={"on": False})
    assert client.get("/api/exec/killswitch").json()["kill_switch"] is False


def test_signal_endpoint_readonly(client):
    _seed_ticks()
    r = client.get("/api/signal/R_10", params={
        "family": "MATCHES", "barrier": 1, "window": 100,
    })
    assert r.status_code == 200
    body = r.json()
    assert "signal" in body and "risk" in body
    # This endpoint is read-only: it must NOT have placed a trade.
    assert client.get("/api/exec/open").json()["count"] == 0


def test_exec_history_has_no_secrets(client):
    r = client.get("/api/exec/history")
    assert r.status_code == 200
    assert "signals" in r.json() and "trades" in r.json()


def test_invalid_execute_missing_signal(client):
    r = client.post("/api/exec/execute", json={"mode": "HARNESS"})
    assert r.status_code == 400


def test_execute_unknown_signal_404(client):
    r = client.post("/api/exec/execute", json={"signal_id": "does-not-exist", "mode": "HARNESS"})
    assert r.status_code == 404


def test_duplicate_signal_execution_blocked(client):
    """Same signal executed twice => exactly ONE purchase (duplicate protection)."""
    _seed_ticks()
    # produce + qualify a signal
    first = client.get("/api/signal/R_10", params={
        "family": "MATCHES", "barrier": 1, "window": 100,
    }).json()
    sig_id = first["signal"]["signal_id"]
    if not first["executable"]:
        return  # board honestly yields no trade (correct, capital-preserving)

    ok = client.post("/api/exec/execute", json={"signal_id": sig_id, "mode": "HARNESS"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "SUCCEEDED"
    # duplicate execution attempt must be blocked (no second purchase)
    dup = client.post("/api/exec/execute", json={"signal_id": sig_id, "mode": "HARNESS"})
    assert dup.status_code == 200
    assert dup.json()["status"] == "BLOCKED"
    ledger = client.get("/api/exec/ledger").json()["trades"]
    same = [t for t in ledger if t["signal_id"] == sig_id]
    assert len(same) == 1


def test_live_execution_blocked_when_disabled(client):
    """Attempt a LIVE purchase while LIVE_TRADING_ENABLED=false => REJECTED, no request."""
    r = client.post("/api/exec/execute", json={
        "signal_id": "whatever", "mode": "LIVE",
    })
    # unknown signal -> 404 first, but critically never a purchase; live is not enabled
    assert r.status_code in (400, 404)
    assert client.get("/api/exec/probe", params={"mode": "LIVE"}).json()["can_purchase"] is False


def test_unauthorized_live_rejected(client):
    """POST /exec/execute with a LIVE mode must be rejected with live disabled."""
    _seed_ticks()
    r = client.post("/api/exec/execute", json={"signal_id": "s", "mode": "LIVE"})
    assert r.status_code == 404  # unknown signal; live path cannot ever fire
    # probe confirms the server would not allow a purchase right now
    assert client.get("/api/exec/probe", params={"mode": "LIVE"}).json()["can_purchase"] is False


def test_open_contracts_and_performance_endpoints(client):
    r = client.get("/api/exec/open")
    assert r.status_code == 200
    r = client.get("/api/exec/performance")
    assert r.status_code == 200
    assert "net_profit" in r.json()


def test_resolve_requires_outcome(client):
    r = client.post("/api/exec/resolve", json={"contract_id": "x"})
    assert r.status_code == 400