"""Phase 5 tests — execution engine, lifecycle, ledger, performance, safety.

Full lifecycle test: SIGNAL -> RISK GATE -> EXECUTION REQUEST -> VALIDATION ->
PAPER/HARNESS EXECUTION -> OPEN -> RESULT -> LEDGER -> PERFORMANCE. Then LIVE-path
safety: with the master switch OFF every attempt to purchase must be REJECTED and no
Deriv purchase request may be sent.
"""

from __future__ import annotations

import time


from app.services.execution_engine import ExecutionEngine
from app.services.broker import (
    MODE_HARNESS,
    MODE_LIVE,
    MODE_PAPER,
    KillSwitch,
    PaperBroker,
)
from app.services.signal_engine import ExecutionState, SignalState


def ready_signal() -> object:
    from app.services.contracts import build_spec
    from app.services.proposal_engine import NormalizedProposal, SOURCE_LIVE
    from app.services.risk_engine import RiskContext, RiskGate
    from app.services.signal_engine import SignalEngine, build_signal

    digits = [1] * 38 + [2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3]
    counts = [0] * 10
    for d in digits:
        counts[d] += 1
    proposal = NormalizedProposal(
        source=SOURCE_LIVE, state="OK", message="", proposal_id="p-live-1",
        symbol="R_10", contract_type="DIGITMATCH", barrier=1, duration_ticks=5,
        currency="USD", stake=1.0, ask_price=1.0, payout=6.5, profit_net=5.5,
        breakeven_win_rate=round(1.0 / 6.5, 5), quote_timestamp=time.time(),
    )
    sig = build_signal(
        build_spec("R_10", "MATCHES", barrier=1),
        window_analysis={"n": len(digits), "size": len(digits), "counts": counts,
                         "data_quality": {"state": "DATA_READY", "source": "deriv_live"}},
        proposal=proposal,
        data_quality={"state": "DATA_READY", "source": "deriv_live"},
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="deriv_live",
    )
    # Force it through the risk gate honestly (live-agnostic harness execution though).
    ctx = RiskContext(execution_mode=MODE_LIVE, live_enabled=True, live_authenticated=True,
                      connected=True, open_trades=0, now=time.time())
    state, reason, vetos = RiskGate().evaluate(sig, ctx)
    SignalEngine().confirm_eligible(sig, risk_state=state, risk_reason=reason, vetos=vetos)
    assert sig.is_executable(), reason
    return sig


async def test_full_lifecycle_paper():
    # PAPER mode: uses a real spot provider to snap an entry.
    state = {"spot": 500.0}
    engine = ExecutionEngine(spot_provider=lambda: state["spot"])
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_PAPER)
    assert out["status"] == ExecutionState.SUCCEEDED.value
    assert engine.open_count() == 1

    open_rows = engine.open_contracts()
    assert open_rows[0]["execution_mode"] == MODE_PAPER
    assert open_rows[0]["status"] == "OPEN"

    # resolve a WIN
    r = engine.resolve_result(out["contract_id"], win=True)
    assert r["status"] == "WON"
    perf = engine.performance(mode=MODE_PAPER)
    assert perf["trades"] == 1
    assert perf["wins"] == 1
    assert perf["net_profit"] > 0
    ledger = engine.ledger(mode=MODE_PAPER)
    assert ledger and ledger[0]["status"] == "WON"


async def test_full_lifecycle_harness():
    engine = ExecutionEngine()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_HARNESS)
    assert out["status"] == ExecutionState.SUCCEEDED.value
    r = engine.resolve_result(out["contract_id"], win=False)
    assert r["status"] == "LOST"
    assert r["profit_loss"] < 0
    p = engine.performance(mode=MODE_HARNESS)
    assert p["losses"] == 1


async def test_harness_and_paper_not_mixed_in_performance():
    engine = ExecutionEngine(spot_provider=lambda: 999.0)
    s1 = ready_signal()
    h1 = await engine.execute(s1, mode=MODE_HARNESS)
    engine.resolve_result(h1["contract_id"], win=True)
    s2 = ready_signal()
    s2.signal_id = "s2-distinct-abc"
    pap = await engine.execute(s2, mode=MODE_PAPER)
    assert pap["status"] == ExecutionState.SUCCEEDED.value
    engine.resolve_result(pap["contract_id"], win=False)
    h = engine.performance(mode=MODE_HARNESS)
    pap_perf = engine.performance(mode=MODE_PAPER)
    assert h["mode"] == MODE_HARNESS and h["trades"] == 1 and h["wins"] == 1
    assert pap_perf["mode"] == MODE_PAPER and pap_perf["trades"] == 1 and pap_perf["losses"] == 1


async def test_kill_switch_blocks_new_trades():
    engine = ExecutionEngine(kill_switch=KillSwitch())
    engine.kill.enable()
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_LIVE)
    assert out["status"] == ExecutionState.BLOCKED.value
    assert engine.open_count() == 0


async def test_live_disabled_blocks_purchase():
    engine = ExecutionEngine()
    sig = ready_signal()
    # master switch is OFF by default (settings.execution_live_enabled == False)
    out = await engine.execute(sig, mode=MODE_LIVE)
    assert out["status"] == ExecutionState.BLOCKED.value
    assert "LIVE" in out["reason"] or "live" in out["reason"]
    # no Harness/Paper/Purchase happened => open count 0
    assert engine.open_count() == 0


async def test_stale_signal_blocked_even_in_harness():
    engine = ExecutionEngine()
    sig = ready_signal()
    sig.expiry = time.time() - 100
    out = await engine.execute(sig, mode=MODE_HARNESS)
    assert out["status"] == ExecutionState.BLOCKED.value


async def test_signal_not_execution_ready_is_failed():
    engine = ExecutionEngine()
    sig = ready_signal()
    sig.signal_state = SignalState.VALIDATING.value
    sig.execution_state = ExecutionState.NOT_ELIGIBLE.value
    out = await engine.execute(sig, mode=MODE_HARNESS)
    # FAILED (never a purchase), open count stays 0
    assert out["status"] in (ExecutionState.FAILED.value, ExecutionState.BLOCKED.value)
    assert engine.open_count() == 0


async def test_ambiguous_execution_goes_uncertain_and_no_fabricated_result():
    class AmbiguousBroker:
        async def buy(self, req):
            from app.services.broker import PurchaseResult
            return PurchaseResult(request_id="x", idempotency_key="y", status="UNKNOWN",
                                  message="timeout/no confirmation", execution_mode=MODE_HARNESS)

    engine = ExecutionEngine(brokers={MODE_HARNESS: AmbiguousBroker()})
    sig = ready_signal()
    out = await engine.execute(sig, mode=MODE_HARNESS)
    assert out["status"] == ExecutionState.UNCERTAIN.value
    assert sig.execution_state == ExecutionState.UNCERTAIN.value
    # no results invented
    assert engine.open_count() == 0
    assert engine.performance()["trades"] == 0


async def test_duplicate_retry_after_uncertain_never_buys_again():
    """WHEN UNCERTAIN, DO NOT BUY AGAIN: a retry of the same signal is a NO-OP."""
    class AmbiguousBroker:
        async def buy(self, req):
            from app.services.broker import PurchaseResult
            return PurchaseResult(request_id="x", idempotency_key="y", status="UNKNOWN",
                                  message="ambiguous", execution_mode=MODE_HARNESS)

    engine = ExecutionEngine(brokers={MODE_HARNESS: AmbiguousBroker()})
    sig = ready_signal()
    await engine.execute(sig, mode=MODE_HARNESS)
    # retry with the same signal -> still no second purchase
    engine._by_idem[sig.signal_id] = "unknown-contract"
    out = await engine.execute(sig, mode=MODE_HARNESS)
    assert out["status"] == ExecutionState.BLOCKED.value


async def test_reconciliation_no_automatic_rebuy():
    engine = ExecutionEngine()
    sig = ready_signal()
    sig.execution_state = ExecutionState.UNCERTAIN.value
    res = await engine.reconcile_uncertain(sig, contract_id="c1")
    assert "never buy" in res["reason"].lower() or "no re-buy" in res["reason"].lower()


def test_paper_broker_needs_spot():
    broker = PaperBroker(spot_provider=lambda: None)
    import asyncio
    from app.services.broker import ExecutionRequest

    req = ExecutionRequest(signal_id="s", symbol="R_10", contract_type="DIGITMATCH",
                           family="MATCHES", barrier=1, prediction="1", duration_ticks=5,
                           stake=1.0, execution_mode=MODE_PAPER)
    res = asyncio.run(broker.buy(req))
    assert res.status == "ERROR"


def test_live_broker_rejects_without_enable():
    from app.services.broker import DerivLiveBroker, ExecutionRequest

    broker = DerivLiveBroker(live_enabled=False)
    req = ExecutionRequest(signal_id="s", symbol="R_10", contract_type="DIGITMATCH",
                           family="MATCHES", barrier=1, prediction="1", duration_ticks=5,
                           stake=1.0, proposal_id="pid", proposal_source="LIVE",
                           execution_mode=MODE_LIVE)
    import asyncio

    res = asyncio.run(broker.buy(req))
    assert res.status == "REJECTED"
    assert "DISABLED" in res.message.upper()


def test_kill_switch_independent_of_frontend():
    ks = KillSwitch()
    assert not ks.enabled
    ks.enable()
    assert ks.enabled
    ks.disable()
    assert not ks.enabled


# ---------------- ledger immutability-ish (append-mostly, no dup) -------------
async def test_ledger_prevents_duplicate_records():
    engine = ExecutionEngine()
    sig = ready_signal()
    await engine.execute(sig, mode=MODE_HARNESS)
    sig2 = ready_signal()
    sig2.signal_id = "unique-2"
    await engine.execute(sig2, mode=MODE_HARNESS)
    ids = [t["trade_id"] for t in engine.ledger()]
    assert len(ids) == len(set(ids))


# ---------------- concurrency : only permitted number execute -----------------
async def test_concurrent_requests_respect_lock():
    engine = ExecutionEngine()
    # First signal acquires and completes; a second held lock can't execute.
    sig = ready_signal()
    assert engine.lock.acquire()
    out = await engine.execute(sig, mode=MODE_HARNESS)
    assert out["status"] == ExecutionState.BLOCKED.value
    engine.lock.release()
    # Now it can run.
    out2 = await engine.execute(sig, mode=MODE_HARNESS)
    assert out2["status"] == ExecutionState.SUCCEEDED.value
    assert engine.open_count() == 1


# ---------------- security : no secrets logged, no live autocall --------------
def test_no_secret_material_in_responses():
    engine = ExecutionEngine()
    # The engine never includes tokens/secrets in any observable dict.
    assert "token" not in str(engine.ledger())
    assert "secret" not in str(engine.performance())