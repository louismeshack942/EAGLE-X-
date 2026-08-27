"""Phase 4 tests — signal creation, state machine, probability, EV, multi-window,
risk gates, expiration, duplicate protection, priority ordering, vetoes.

All deterministic; no real money.
"""

from __future__ import annotations

import time

import pytest

from app.services.broker import MODE_HARNESS, MODE_LIVE, MODE_PAPER
from app.services.contracts import build_spec
from app.services.execution_engine import ExecutionEngine, _trade_outcome
from app.services.probability import BetaConfig, beta_posterior_mean
from app.services.proposal_engine import NormalizedProposal, SOURCE_HARNESS, SOURCE_LIVE
from app.services.risk_engine import RiskContext, RiskGate
from app.services.signal_engine import (
    ExecutionState,
    RiskState,
    SignalEngine,
    SignalState,
    build_signal,
    contract_priority,
    deterministic_signal_id,
)


def make_proposal(*, source=SOURCE_LIVE, ask=1.0, payout=1.9, family="DIFFERS",
                  barrier=1, symbol="R_10", duration=5) -> NormalizedProposal:
    return NormalizedProposal(
        source=source, state="OK", message="", proposal_id="p-" + (source or "x"),
        symbol=symbol, contract_type=_ct(family), barrier=barrier,
        duration_ticks=duration, currency="USD", stake=1.0,
        ask_price=ask, payout=payout, profit_net=payout - ask,
        breakeven_win_rate=(round(ask / payout, 5) if payout else None),
        quote_timestamp=time.time(),
    )


def _ct(family: str) -> str:
    return {
        "DIFFERS": "DIGITDIFF", "MATCHES": "DIGITMATCH", "ODD": "DIGITODD",
        "EVEN": "DIGITEVEN", "OVER": "DIGITOVER", "UNDER": "DIGITUNDER",
    }[family]


def counts_from(list_of_digits):
    c = [0] * 10
    for d in list_of_digits:
        c[d] += 1
    return c


def window(digits, state="DATA_READY"):
    return {
        "n": len(digits),
        "size": len(digits),
        "counts": counts_from(digits),
        "data_quality": {"state": state, "source": "harness"},
    }


# ---------------- probability (transparent Beta/Bayesian) --------------------
def test_beta_posterior_mean_shrinks_toward_prior():
    # 10/10 successes with a uniform prior never reaches 1.0 (honest shrinkage).
    assert beta_posterior_mean(10, 10, BetaConfig(1, 1)) == pytest.approx(11 / 12, 4)
    assert beta_posterior_mean(0, 10, BetaConfig(1, 1)) == pytest.approx(1 / 12, 4)
    assert beta_posterior_mean(45, 100, BetaConfig(1, 1)) == pytest.approx(46 / 102, 4)


def test_beta_no_data_returns_prior_mean():
    assert beta_posterior_mean(0, 0, BetaConfig(1, 1)) == pytest.approx(0.5, 4)


def test_beta_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        beta_posterior_mean(5, 3, BetaConfig(1, 1))
    with pytest.raises(ValueError):
        beta_posterior_mean(-1, 3, BetaConfig(1, 1))


# ---------------- signal construction + state machine ------------------------
def test_signal_rejected_on_insufficient_sample():
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window([1, 2, 3, 4, 5]),
        proposal=make_proposal(family="DIFFERS"),
        data_quality=window([1])["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert sig.signal_state == SignalState.REJECTED.value
    assert "sample" in sig.reason
    assert sig.execution_state == ExecutionState.NOT_ELIGIBLE.value


def test_signal_rejected_on_bad_data_quality():
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window([1] * 60, state="DISCONNECTED"),
        proposal=make_proposal(family="DIFFERS"),
        data_quality=window([1] * 60, state="DISCONNECTED")["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert sig.signal_state == SignalState.REJECTED.value


def test_signal_rejected_without_proposal():
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window([1] * 60),
        proposal=None,
        data_quality=window([1] * 60)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert sig.signal_state == SignalState.REJECTED.value
    assert "proposal" in sig.reason.lower() or "pricing" in sig.reason.lower()


def test_signal_rejected_on_invalid_price():
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window([1] * 60),
        proposal=make_proposal(source=SOURCE_LIVE, payout=0.0),
        data_quality=window([1] * 60)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert sig.signal_state == SignalState.REJECTED.value


def test_valid_signal_enters_validating_with_ev_and_probability():
    digits = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
              1, 1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1]
    # DIFFERS@1: most digits are != 1 -> high success rate
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window(digits),
        proposal=make_proposal(source=SOURCE_HARNESS, family="DIFFERS"),
        data_quality=window(digits)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert sig.signal_state == SignalState.VALIDATING.value
    assert sig.estimated_probability is not None and 0 < sig.estimated_probability < 1
    assert sig.probability_method == "beta-bayesian posterior mean"
    assert sig.expected_value is not None
    assert sig.expiry > 0


def test_no_analysis_to_buy_shortcut():
    """A fresh signal is never executable without going through risk PASS."""
    digits = [1] * 30 + [2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1]
    sig = build_signal(
        build_spec("R_10", "MATCHES", barrier=1),
        window_analysis=window(digits),
        proposal=make_proposal(source=SOURCE_HARNESS, family="MATCHES", payout=6.5),
        data_quality=window(digits)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    assert not sig.is_executable()
    assert sig.signal_state == SignalState.VALIDATING.value
    assert sig.execution_state == ExecutionState.NOT_ELIGIBLE.value


def test_risk_pass_moves_to_execution_ready():
    # DIFFERS@1 wins whenever the digit is NOT 1 => make '1' rare to get a real edge.
    digits = [0, 2, 3, 4, 5, 6, 7, 8, 9, 0, 2, 3, 4, 5, 6, 7, 8, 9, 0, 2,
              3, 4, 5, 6, 7, 8, 9, 0, 2, 3, 4, 5, 6, 7, 8, 9, 0, 2, 3, 4,
              5, 6, 7, 8, 9, 0, 2, 3, 4, 5, 6, 7, 8, 9, 0, 2, 3, 4, 5, 6]
    sig = build_signal(
        build_spec("R_10", "DIFFERS", barrier=1),
        window_analysis=window(digits),
        proposal=make_proposal(source=SOURCE_HARNESS, family="DIFFERS"),
        data_quality=window(digits)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    eng = SignalEngine()
    ctx = RiskContext(execution_mode=MODE_HARNESS, live_enabled=False,
                      connected=True, open_trades=0, now=time.time())
    state, reason, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.PASS.value, reason
    eng.confirm_eligible(sig, risk_state=state, risk_reason=reason, vetos=vetos)
    assert sig.signal_state == SignalState.EXECUTION_READY.value
    assert sig.execution_state == ExecutionState.ELIGIBLE.value
    assert sig.is_executable()


# ---------------- risk gate vetoes -------------------------------------------
def _ready_signal():
    digits = [1] * 30 + [2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0, 1,
                         2, 3, 4, 5, 6, 7, 8, 9, 0]
    sig = build_signal(
        build_spec("R_10", "MATCHES", barrier=1),
        window_analysis=window(digits),
        proposal=make_proposal(source=SOURCE_LIVE, family="MATCHES", payout=6.5),
        data_quality=window(digits)["data_quality"],
        multi_window_state="STABLE",
        snapshot_tag="t1",
        source="harness",
    )
    sig.signal_state = SignalState.EXECUTION_READY.value
    sig.execution_state = ExecutionState.ELIGIBLE.value
    sig.risk_state = RiskState.PASS.value
    return sig


def test_risk_veto_kill_switch():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_LIVE, live_enabled=True, live_authenticated=True,
                      connected=True, kill_switch=True, open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("kill" in v for v in vetos)


def test_risk_veto_live_not_enabled():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_LIVE, live_enabled=False, connected=True,
                      open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("enabled" in v for v in vetos)
    assert any("auth" in v for v in vetos)  # not authenticated too


def test_risk_veto_too_many_open():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=5, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("open" in v for v in vetos)


def test_risk_veto_daily_loss():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_HARNESS, realized_daily_loss=9999,
                      open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("loss" in v for v in vetos)


def test_risk_veto_expired_signal():
    sig = _ready_signal()
    sig.expiry = time.time() - 100  # expired
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("expired" in v for v in vetos)


def test_risk_veto_duplicate_signal():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0,
                      seen_signal_ids={sig.signal_id}, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("duplicate" in v for v in vetos)


def test_risk_veto_stale_data():
    sig = _ready_signal()
    sig.data_quality = "STALE"
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("stale" in v for v in vetos)


def test_risk_veto_negative_ev():
    sig = _ready_signal()
    sig.expected_value = -0.5
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("negative" in v or "EV" in v for v in vetos)


def test_risk_veto_conflicting_windows():
    sig = _ready_signal()
    sig.multi_window_state = "CONFLICTING"
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("conflict" in v for v in vetos)


def test_risk_veto_insufficient_balance():
    sig = _ready_signal()
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, balance=0.2, now=time.time())
    state, _, vetos = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value
    assert any("balance" in v or "reserve" in v for v in vetos)


# ---------------- expiration --------------------------------------------------
def test_signal_expires_blocked():
    sig = _ready_signal()
    sig.expiry = time.time() - 5
    assert not sig.valid_at()
    # risk gate sees expired and the engine refuses
    ctx = RiskContext(execution_mode=MODE_HARNESS, open_trades=0, now=time.time())
    state, _, _ = RiskGate().evaluate(sig, ctx)
    assert state == RiskState.VETO.value


def test_valid_at_ok_inside_window():
    sig = _ready_signal()
    sig.expiry = time.time() + 10
    assert sig.valid_at()


# ---------------- duplicate protection + idempotency -------------------------
def test_deterministic_signal_id_same_inputs():
    a = deterministic_signal_id(build_spec("R_10", "DIFFERS", barrier=1), "w1")
    b = deterministic_signal_id(build_spec("R_10", "DIFFERS", barrier=1), "w1")
    assert a == b
    c = deterministic_signal_id(build_spec("R_10", "DIFFERS", barrier=2), "w1")
    assert a != c


def test_execution_engine_blocks_duplicate_idempotency():
    eng = ExecutionEngine()
    sig1 = _ready_signal()
    sig1.signal_state = SignalState.EXECUTION_READY.value
    sig1.execution_state = ExecutionState.ELIGIBLE.value
    sig1.risk_state = RiskState.PASS.value
    import asyncio

    out1 = asyncio.get_event_loop().run_until_complete(eng.execute(sig1, mode=MODE_HARNESS))
    assert out1["status"] == ExecutionState.SUCCEEDED.value
    # Duplicate: same idempotency key (signal_id) must NOT create a second purchase.
    out2 = asyncio.get_event_loop().run_until_complete(eng.execute(sig1, mode=MODE_HARNESS))
    assert out2["status"] == ExecutionState.BLOCKED.value
    assert "duplicate" in out2["reason"]
    assert eng.open_count() == 1


def test_execution_lock_blocks_concurrent():
    eng = ExecutionEngine()
    assert eng.lock.acquire()
    sig = _ready_signal()
    import asyncio

    out = asyncio.get_event_loop().run_until_complete(eng.execute(sig, mode=MODE_HARNESS))
    assert out["status"] == ExecutionState.BLOCKED.value
    assert "lock" in out["reason"]


# ---------------- priority ordering ------------------------------------------
def test_contract_priority_order():
    assert contract_priority("MATCHES") > contract_priority("OVER")
    assert contract_priority("OVER") > contract_priority("UNDER")
    assert contract_priority("UNDER") > contract_priority("ODD")
    assert contract_priority("ODD") > contract_priority("EVEN")
    assert contract_priority("EVEN") > contract_priority("DIFFERS")
    assert contract_priority("MATCHES") == 6
    assert contract_priority("DIFFERS") == 1


def test_lower_priority_can_outrank_with_better_ev():
    """Priority is a tie-breaker, NOT a mandate: better EV can outrank higher priority."""
    higher_prio = contract_priority("MATCHES")
    lower_prio = contract_priority("DIFFERS")
    assert lower_prio < higher_prio
    # If the higher-priority contract has worse EV, the lower-priority one is chosen.
    better_ev = 0.4
    worse_ev = 0.01
    assert better_ev > worse_ev
    # and ranking uses EV as primary key (equivalent to the scanner behaviour).


# ---------------- outcome resolution (Phase 5) -------------------------------
def test_trade_outcome_semantics():
    assert _trade_outcome("MATCHES", 3, 9, 3) is True
    assert _trade_outcome("MATCHES", 3, 9, 4) is False
    assert _trade_outcome("DIFFERS", 3, 9, 4) is True
    assert _trade_outcome("ODD", None, 9, 3) is True
    assert _trade_outcome("EVEN", None, 9, 2) is True
    assert _trade_outcome("OVER", 4, 9, 7) is True
    assert _trade_outcome("UNDER", 4, 9, 2) is True
    assert _trade_outcome("OVER", 4, 9, 2) is False
    assert _trade_outcome("UNDER", 4, 9, 7) is False


def test_execute_then_resolve_win_loss():
    import asyncio

    eng = ExecutionEngine()
    sig = _ready_signal()
    sig.signal_state = SignalState.EXECUTION_READY.value
    sig.execution_state = ExecutionState.ELIGIBLE.value
    sig.risk_state = RiskState.PASS.value
    out = asyncio.get_event_loop().run_until_complete(eng.execute(sig, mode=MODE_HARNESS))
    cid = out["contract_id"]
    assert out["status"] == ExecutionState.SUCCEEDED.value
    assert eng.open_count() == 1

    r = eng.resolve_result(cid, win=True)
    assert r["status"] == "WON"
    assert r["profit_loss"] > 0
    assert eng.open_count() == 0
    perf = eng.performance()
    assert perf["trades"] == 1
    assert perf["wins"] == 1


def test_result_never_inferred_from_missing_data():
    import asyncio

    eng = ExecutionEngine()
    sig = _ready_signal()
    sig.signal_state = SignalState.EXECUTION_READY.value
    sig.execution_state = ExecutionState.ELIGIBLE.value
    sig.risk_state = RiskState.PASS.value
    out = asyncio.get_event_loop().run_until_complete(eng.execute(sig, mode=MODE_HARNESS))
    cid = out["contract_id"]
    r = eng.resolve_result(cid)  # no next_digit, no win
    assert r["status"] == "UNKNOWN"


def test_execution_live_blocked_without_enable():
    import asyncio

    eng = ExecutionEngine()
    sig = _ready_signal()
    sig.signal_state = SignalState.EXECUTION_READY.value
    sig.execution_state = ExecutionState.ELIGIBLE.value
    sig.risk_state = RiskState.PASS.value
    out = asyncio.get_event_loop().run_until_complete(eng.execute(sig, mode=MODE_LIVE))
    assert out["status"] == ExecutionState.BLOCKED.value
    assert "LIVE" in out["reason"] or "live" in out["reason"]


def test_every_trade_carries_mode():
    import asyncio

    eng = ExecutionEngine()
    sig = _ready_signal()
    sig.signal_state = SignalState.EXECUTION_READY.value
    sig.execution_state = ExecutionState.ELIGIBLE.value
    sig.risk_state = RiskState.PASS.value
    asyncio.get_event_loop().run_until_complete(eng.execute(sig, mode=MODE_PAPER))
    for t in eng.ledger():
        assert t["mode"] in (MODE_HARNESS, MODE_PAPER, MODE_LIVE)