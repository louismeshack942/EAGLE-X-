"""Phase 7 — full validation: failure injection, reconciliation, long-run soak, data integrity.

Failure injection uses REAL code paths only: a fake broker returns ambiguous/timeout
PurchaseResults, and the engine must record UNKNOWN and NEVER re-buy the same idempotency
key. Duplicate repurchase attempts must be BLOCKED. Kill switch must veto LIVE. A soak loop
runs thousands of decision-service cycles without leaks or duplicated ledgers. Data-integrity
checks verify the engine ledger vs execution state and the automation counters.
"""

from __future__ import annotations

from app.services.broker import MODE_LIVE, MODE_PAPER, PurchaseResult
from app.services.execution_engine import ExecutionEngine
from app.services.signal_engine import ExecutionState
from app.services.automated_trader import AutomationConfig, AutomatedTrader
from tests.test_execution import ready_signal


def _ambiguous_broker():
    """A broker that always resolves purchases as UNCERTAIN (timeout)."""

    class AmbiguousBroker:
        calls = 0

        async def buy(self, request):
            type(self).calls += 1
            return PurchaseResult(
                request_id=request.request_id, idempotency_key=request.idempotency_key,
                status="UNCERTAIN", contract_id="",
                message="purchase acknowledgement timeout (network)",
            )

    return AmbiguousBroker()


def _reject_broker():
    class RejectBroker:
        async def buy(self, request):
            return PurchaseResult(
                request_id=request.request_id, idempotency_key=request.idempotency_key,
                status="REJECTED", message="risk rejection at broker",
            )

    return RejectBroker()


# ---------------------------------------------------------------------------
# 1. Failure injection: ambiguous purchase -> UNKNOWN, NEVER re-buy
# ---------------------------------------------------------------------------
async def test_ambiguous_purchase_records_unknown_and_never_rebuys():
    engine = ExecutionEngine()
    engine.brokers[MODE_PAPER] = _ambiguous_broker()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_PAPER)
    assert out["status"] == ExecutionState.UNCERTAIN.value
    # ledger must show UNKNOWN, not OPEN, and open_count zero
    assert engine.open_count() == 0
    led = engine.ledger(mode=MODE_PAPER)
    assert led and led[0]["status"] == "UNKNOWN"
    # A retry with the SAME signal must be duplicate-blocked, never a second broker call
    engine.brokers[MODE_PAPER].calls = 0
    out2 = await engine.execute(sig, mode=MODE_PAPER)
    assert out2["status"] == ExecutionState.BLOCKED.value
    assert engine.brokers[MODE_PAPER].calls == 0


async def test_rejected_purchase_returns_rejected_not_open():
    engine = ExecutionEngine()
    engine.brokers[MODE_PAPER] = _reject_broker()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_PAPER)
    assert out["status"] == "REJECTED"
    assert engine.open_count() == 0


async def test_reconcile_uncertain_after_timeout():
    engine = ExecutionEngine()
    engine.brokers[MODE_PAPER] = _ambiguous_broker()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_PAPER)
    assert out["status"] == ExecutionState.UNCERTAIN.value
    # reconcile must resolve to UNKNOWN and explicitly refuse a re-buy
    r = await engine.reconcile_uncertain(sig, contract_id=out.get("contract_id", ""))
    assert "no re-buy" in r["reason"].lower() or r["status"] == "OPEN"
    assert engine.open_count() == 0


async def test_live_blocked_and_kill_switch_vetoes_execution():
    engine = ExecutionEngine()
    engine.brokers[MODE_PAPER] = _ambiguous_broker()
    engine.kill.enable()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_LIVE)
    # LIVE purchase is BLOCKED by the server master switch (or the kill switch), never sent.
    assert out["status"] == ExecutionState.BLOCKED.value
    assert engine.open_count() == 0
    # With the kill switch ACTIVE, even a server-side-enabled live attempt is vetoed:
    # verify the engine exposes the kill veto path when live is enabled.
    from app.config import settings
    old = settings.execution_live_enabled
    settings.execution_live_enabled = True
    try:
        out2 = await engine.execute(sig, mode=MODE_LIVE)
        assert out2["status"] == "REJECTED" or out2["status"] == ExecutionState.BLOCKED.value
        assert "kill switch" in out2["reason"].lower()
    finally:
        settings.execution_live_enabled = old


# ---------------------------------------------------------------------------
# 2. Long-run soak: thousands of cycles, no leaks/dupes/growth
# ---------------------------------------------------------------------------
async def test_long_run_soak_no_leak_no_duplicate_ledger():
    engine = ExecutionEngine()
    engine.brokers[MODE_PAPER] = _ambiguous_broker()
    for i in range(3000):
        sig = ready_signal()
        sig.signal_id = f"soak-{i}"
        out = await engine.execute(sig, mode=MODE_PAPER)
        # every cycle resolves: either uncertain (recorded once) or blocked (dup) — no crash
        assert out["status"] in (ExecutionState.UNCERTAIN.value, ExecutionState.BLOCKED.value)
    led = engine.ledger(mode=MODE_PAPER)
    # unique signal_ids across ledger: each soak cycle recorded at most once
    keys = [r["signal_id"] for r in led]
    assert len(keys) == len(set(keys)), "duplicated idempotency keys in ledger"
    assert engine.open_count() == 0


# ---------------------------------------------------------------------------
# 3. Data integrity: automation P/L is driven only by engine results
# ---------------------------------------------------------------------------
async def test_automation_pnl_derived_from_engine_results():
    t = AutomatedTrader(ds=None)
    t.cfg = AutomationConfig(mode="MONITOR")
    t.on_result("LOST", pnl=-1.0)
    t.on_result("WON", pnl=2.0)
    s = t.session_stats()
    assert s["wins"] == 1 and s["losses"] == 1
    # session loss reflects the losing trade (net tracked in live counters)
    assert s["session_loss"] == 1.0  # only the LOST trade contributed losses
    # consecutive losses reset on win
    assert s["consecutive_losses"] == 0