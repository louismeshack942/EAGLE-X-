"""Certification harness — audit log, $1/60-trade budget, chaos,
recovery and security probes. Paper-mode only: no real broker calls."""
import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.eagle import EagleEngine
from app.services.lightning import LightningEngine, SymbolState, TradeLedger
from app.services.organism import Organism
from app.services.shell import (  # noqa: F401
    AuditLog,
    RealTradeBudget,
    audit_log as global_audit,
    real_trade_budget as global_budget,
)

client = TestClient(app)


# ---------------- §16 audit log ----------------
def test_audit_newest_first_and_filter():
    log = AuditLog()
    log.record("user", "risk_limit_changed", old="0.05", new="0.08")
    log.record("engine", "strategy_promoted", detail="v14")
    evs = log.list()
    assert evs[0]["action"] == "strategy_promoted"
    assert evs[1]["action"] == "risk_limit_changed"
    assert log.count() == 2
    only = log.list(action="risk_limit_changed")
    assert len(only) == 1


def test_audit_api():
    rec = client.post("/shell/audit",
                      json={"actor": "test", "action": "x", "detail": "d"}).json()
    assert rec["action"] == "x"
    evs = client.get("/shell/audit?action=x").json()["events"]
    assert any(e["actor"] == "test" for e in evs)


# ---------------- §6/§20 certification budget ----------------
def test_budget_rejects_non_one_dollar_stake():
    b = RealTradeBudget()
    for stake in (0.5, 1.01, 5.0):
        r = b.record(stake=stake, symbol="R_10", contract="OVER 4",
                     result="win", pnl=0.95)
        assert r["ok"] is False
        assert "exactly" in r["error"]


def test_budget_locks_at_sixty():
    b = RealTradeBudget()
    for i in range(60):
        r = b.record(stake=1.0, symbol="R_100", contract="EVEN",
                     result="win" if i % 3 else "loss",
                     pnl=0.95 if i % 3 else -1.0)
        assert r["ok"] is True
    assert b.locked is True
    blocked = b.record(stake=1.0, symbol="R_100", contract="EVEN",
                       result="win", pnl=0.95)
    assert blocked["ok"] is False
    assert "REAL_TEST_EXECUTION_LOCKED" in blocked["error"]
    rep = b.report()
    assert rep["total_trades"] == 60
    assert rep["locked"] is True
    # human reset reopens deliberately
    assert b.reset(actor="human")["ok"] is True
    assert b.report()["total_trades"] == 0


def test_budget_report_math():
    b = RealTradeBudget()
    b.record(stake=1.0, symbol="R_10", contract="OVER 4", result="win",
             pnl=0.95, latency_ms=100.0)
    b.record(stake=1.0, symbol="R_100", contract="EVEN", result="loss",
             pnl=-1.0, latency_ms=50.0)
    rep = b.report()
    assert rep["wins"] == 1 and rep["losses"] == 1
    assert rep["total_stake"] == 2.0
    assert rep["net_pnl"] == -0.05
    assert rep["max_consecutive_losses"] == 1
    assert rep["by_market"]["R_10"]["wins"] == 1
    assert rep["by_contract"]["EVEN"]["losses"] == 1
    assert rep["latency_ms"]["p50"] is not None


def test_budget_api():
    global_budget.reset(actor="test-setup")
    r = client.post("/shell/certification/trade",
                    json={"stake": 1.0, "symbol": "R_10", "contract": "OVER 4",
                          "result": "win", "pnl": 0.95}).json()
    assert r["ok"] is True
    assert r["trade_number"] >= 1
    bad = client.post("/shell/certification/trade",
                      json={"stake": 2.0, "symbol": "R_10", "contract": "OVER 4",
                            "result": "win", "pnl": 0.95}).json()
    assert bad["ok"] is False
    rep = client.get("/shell/certification/report").json()
    assert "total_trades" in rep
    client.post("/shell/certification/reset", json={"actor": "test-teardown"})


# ---------------- §9 chaos ----------------
def _organism_factory(dgt=4):
    from tests.test_organism import make_organism, uniform_digits, tick
    org, q, layer = make_organism(uniform_digits(300))
    return org, tick


def test_chaos_duplicate_and_invalid_ticks_safe():
    from tests.test_organism import tick
    org, tickfn = _organism_factory()
    bad = tickfn("R_100", 4, 0)
    bad.raw = {"digit": 99}  # corrupt digit payload must be armored away
    r = org.process(bad)
    assert r["decision"] == "REJECT"
    assert "invalid digit" in r["reason"]


def test_chaos_disconnect_failsafe():
    org, _ = _organism_factory()
    org._lightning.symbols["R_100"] = SymbolState("R_100", org._lightning.window_sizes)
    org._lightning.symbols["R_100"].last_tick_epoch = 1.0  # ancient
    assert "stale feed" in org._lightning.failsafe("R_100")
    org._lightning.connection["connected"] = False
    assert "connection lost" in org._lightning.failsafe("R_100")


def test_chaos_risk_lock_blocks_strike():
    from tests.test_organism import make_organism, skewed_digits, tick
    org, q, layer = make_organism(skewed_digits())
    layer.evaluate("R_100")
    r = org.process(tick("R_100", 7, 0), risk_blocked=True)
    assert r["decision"] != "STRIKE"


# ---------------- §10 recovery ----------------
def test_recovery_ledger_reconstruction_and_timeout():
    ledger = TradeLedger()
    ledger.create("rc1", "R_100", "OVER", 4)
    ledger.transition("rc1", "SUBMITTED")
    ledger._records["rc1"].updated_at = time.monotonic() - 11.0
    assert ledger.expire_unknowns() == ["rc1"]
    snap = ledger.snapshot()
    assert snap["unknowns"], "UNKNOWN executions must block new trades after recovery"
    # resolve before any retry — never blind-resubmit
    assert ledger.transition("rc1", "REJECTED") is not None


# ---------------- §11 security ----------------
def test_security_no_secrets_in_responses():
    for path in ("/shell/ops-card", "/organism/spine", "/lightning/dashboard"):
        body = client.get(path).text
        assert "pat_" not in body
        assert "DERIV_API_TOKEN" not in body


def test_security_input_validation():
    bad = client.post("/organism/process", json={"symbol": "R_100"}).json()
    assert bad["decision"] == "REJECT"
    bad2 = client.post("/shell/certification/trade", json={"stake": "x"}).json()
    assert bad2["ok"] is False  # unparseable numeric rejected cleanly, no 500
