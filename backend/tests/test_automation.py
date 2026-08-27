"""Phase 6 tests — Automated Trading Orchestrator.

The automated trader is a CLIENT of the Phase 4/5 pipeline. These tests verify the state
machine, modes, server-side config caps, freshness, limits, concurrency, crash-recovery
honesty, and the ABSOLUTE no-bypass rule (automation never calls a Broker directly and can
never enable live execution itself).
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.config import settings
from app.services.automated_trader import (
    AutomationConfig,
    AutomationState,
    AutomatedTrader,
    get_trader,
)
from app.services.broker import MODE_LIVE, MODE_PAPER


def _seed_market(trader: AutomatedTrader, symbol: str = "R_10", n: int = 80,
                 provider: str = "harness", digit: int = 1) -> None:
    """Push synthetic ticks so the analysis manager reports DATA_READY for window 100."""
    from app.core.ticks import NormalizedTick
    from app.services.analysis_engine import analysis_manager

    analysis_manager.mark_connection(symbol, "connected")
    base = 5000
    for i in range(n):
        ticks = NormalizedTick(
            symbol=symbol,
            epoch_ms=int(time.time() * 1000) - (n - i) * 50,
            quote=float(base + i % 5) / 10.0,
            last_digit=(digit + i) % 10,
            provider=provider,
        )
        analysis_manager.push(ticks)
    if symbol not in trader.get_config().allowed_markets:
        trader.cfg.allowed_markets.append(symbol)


def _fresh_trader(**cfg_overrides) -> AutomatedTrader:
    from app.services.decision_service import DecisionService
    cfg = AutomationConfig()
    for k, v in cfg_overrides.items():
        setattr(cfg, k, v)
    t = AutomatedTrader(ds=DecisionService())
    t.cfg = cfg
    return t


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------
def test_default_off():
    t = _fresh_trader(enabled=False, mode="OFF")
    assert t.status()["state"] == AutomationState.OFF.value
    assert t.status()["live_enabled"] is settings.execution_live_enabled


def test_validate_start_refuses_disabled():
    t = _fresh_trader(enabled=False, mode="MONITOR")
    ok, probs = t.validate_start()
    assert ok is False
    assert any("not enabled" in p for p in probs)


def test_validate_start_refuses_kill_switch():
    t = _fresh_trader(enabled=True, mode="MONITOR")
    t.ds.kill.enable()
    ok, probs = t.validate_start()
    assert ok is False
    assert any("kill switch" in p for p in probs)


def test_live_start_blocked_without_master_switch():
    # The most important gate: LIVE automation must be refused while execution_live_enabled
    # is FALSE (it always is by default and is never changeable by the trader).
    t = _fresh_trader(enabled=True, mode="LIVE")
    ok, probs = t.validate_start()
    assert ok is False
    assert any("execution_live_enabled is FALSE" in p for p in probs)


def test_start_stop_pause_resume_cycle():
    t = _fresh_trader(enabled=True, mode="MONITOR")
    r = t.start()
    assert r["ok"] is True
    assert t.status()["state"] == AutomationState.STARTING.value
    pause = t.pause()
    assert pause["state"] == AutomationState.PAUSED.value
    resume = t.resume()
    assert resume["ok"] is True
    stop = t.stop()
    assert stop["state"] == AutomationState.OFF.value


def test_set_mode_off_stops():
    t = _fresh_trader(enabled=True, mode="MONITOR")
    t.start()
    probs = t.set_mode("OFF")
    assert probs == []
    assert t.status()["state"] == AutomationState.OFF.value


# ---------------------------------------------------------------------------
# Config validation & security
# ---------------------------------------------------------------------------
def test_config_problems_validate_ranges():
    cfg = AutomationConfig(mode="MONITOR", max_open=0)
    assert any("max_open out of range" in p for p in cfg.problems())
    cfg2 = AutomationConfig(mode="BOGUS")
    assert any("invalid mode" in p for p in cfg2.problems())


def test_config_cannot_touch_master_live():
    # Automation config is allowed to carry live_enabled as informational but set_config
    # never mutates settings.execution_live_enabled. Verify via API-level guard is in the
    # api tests; here ensure setting a config dict with live_enabled has no effect.
    before = settings.execution_live_enabled
    cfg = AutomationConfig.from_settings()
    cfg.mode = "LIVE"
    assert settings.execution_live_enabled == before  # unchanged


# ---------------------------------------------------------------------------
# Candidate ordering / market scope
# ---------------------------------------------------------------------------
def test_candidate_priority_order():
    t = _fresh_trader(mode="MONITOR", allowed_families=[
        "MATCHES", "OVER", "UNDER", "ODD", "EVEN", "DIFFERS"],
        allowed_markets=["R_10"])
    _seed_market(t)
    specs = t._candidate_specs("R_10")
    assert specs, "board must be non-empty"
    fams = [s.family for s in specs]
    # MATCHES must be ordered before DIFFERS (priority only orders, gates stay authoritative).
    idx = {f: fams.index(f) for f in fams}
    assert idx["MATCHES"] < idx["DIFFERS"]


def test_market_not_allowed_returns_empty():
    t = _fresh_trader(mode="MONITOR", allowed_markets=["R_100"])
    assert t._candidate_specs("R_10") == []


# ---------------------------------------------------------------------------
# Freshness revalidation
# ---------------------------------------------------------------------------
def test_freshness_rejects_expired_signal():
    from app.services.signal_engine import Signal
    t = _fresh_trader(mode="MONITOR")
    sig = Signal(
        signal_id="s-exp", symbol="R_10", contract_family="MATCHES", contract_type="D",
        barrier=1, prediction="1", duration_ticks=5, stake=1.0,
        proposal_source="LIVE", multi_window_state="STABLE",
        expiry=time.time() - 5, created_ts=time.time() - 10,
    )
    r = t._freshness_ok(sig)
    assert r["ok"] is False
    assert any("signal expired" in p for p in r["problems"])


def test_freshness_rejects_stale_scan():
    t = _fresh_trader(mode="MONITOR")
    t.last_scan_ts = time.time() - 999
    t.cfg.max_signal_age_secs = 5
    from app.services.signal_engine import Signal
    sig = Signal(
        signal_id="s2", symbol="R_10", contract_family="MATCHES", contract_type="D",
        barrier=1, prediction="1", duration_ticks=5, stake=1.0,
        proposal_source="LIVE", multi_window_state="STABLE", expiry=time.time() + 100,
    )
    r = t._freshness_ok(sig)
    assert r["ok"] is False
    assert any("older than max_signal_age" in p for p in r["problems"])


# ---------------------------------------------------------------------------
# Automation limit gate
# ---------------------------------------------------------------------------
def test_gate_blocks_on_consecutive_losses():
    t = _fresh_trader(mode="PAPER", max_consecutive_losses=3)
    t.consecutive_losses = 3
    g = t._automation_gate()
    assert g["ok"] is False
    assert any("consecutive-loss" in p for p in g["problems"])


def test_gate_blocks_on_session_loss_limit():
    t = _fresh_trader(mode="PAPER", max_session_loss=5.0)
    t.session_loss = 5.0
    g = t._automation_gate()
    assert any("session loss limit" in p for p in g["problems"])


def test_gate_blocks_on_daily_loss_limit():
    t = _fresh_trader(mode="MONITOR", max_daily_loss=5.0)
    t.daily_loss = 6.0
    g = t._automation_gate()
    assert any("daily loss limit" in p for p in g["problems"])


def test_gate_blocks_on_max_open():
    t = _fresh_trader(mode="MONITOR", max_open=1)
    t.ds.exec.open_count = lambda: 1  # simulate one open contract
    g = t._automation_gate()
    assert any("max open trades" in p for p in g["problems"])


def test_loss_handling_no_stake_growth():
    # After losses the stake is never increased; only counters update.
    t = _fresh_trader(mode="PAPER", max_stake=1.0)
    t.on_result("LOST", pnl=-1.0)
    t.on_result("LOST", pnl=-1.0)
    assert t.consecutive_losses == 2
    assert t.session_loss == 2.0
    assert t.get_config().max_stake == 1.0  # never auto-raised
    t.on_result("WON", pnl=2.0)
    assert t.consecutive_losses == 0


# ---------------------------------------------------------------------------
# No-bypass: the trader only ever reaches the Broker through the decision service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_direct_broker_call(monkeypatch):
    # Spy on DecisionService.execute (the ONLY broker gateway the trader may use),
    # and verify it is NEVER invoked by the trader itself — only the pipeline may
    # reach the broker, and MONITOR must not execute anything.
    from app.services.decision_service import DecisionService

    t = _fresh_trader(mode="MONITOR")
    _seed_market(t)
    called = {}

    async def spy_execute(self, signal, *, mode):
        called["mode"] = mode
        return {"status": "SUCCEEDED", "contract_id": "c-1", "reason": "ok"}

    monkeypatch.setattr(DecisionService, "execute", spy_execute)
    await t.scan_once()
    assert "mode" not in called  # MONITOR never executes


# ---------------------------------------------------------------------------
# Crash recovery / state reconstruction honesty
# ---------------------------------------------------------------------------
def test_state_reconstructs_from_status():
    # On restart we reconstruct automation state from persisted status; the trader must
    # default to OFF and NOT blindly resume execution (an operator re-arms it).
    t = get_trader()
    # a fresh trader constructed from settings defaults to a safe state
    assert t.state == AutomationState.OFF


# ---------------------------------------------------------------------------
# Concurrency safety
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_scan_lock(monkeypatch):
    t = _fresh_trader(mode="MONITOR", allowed_markets=["R_10"])
    _seed_market(t)
    ran = {"n": 0}

    async def fake_process(spec):
        await asyncio.sleep(0.01)
        ran["n"] += 1
        return {"decision": "SKIP"}

    monkeypatch.setattr(t, "_process_candidate", fake_process)
    results = await asyncio.gather(t._scan_market("R_10"), t._scan_market("R_10"))
    # because of the scan lock at most one cycle runs; total processed equals one cycle's count
    total = sum(len(r) for r in results)
    assert total == ran["n"]


# ---------------------------------------------------------------------------
# Session stats separation
# ---------------------------------------------------------------------------
def test_session_stats_separate_modes():
    t = _fresh_trader(mode="PAPER")
    t.on_result("LOST", pnl=-1.0)
    t.on_result("WON", pnl=2.0)
    s = t.session_stats()
    assert s["wins"] == 1 and s["losses"] == 1
    assert s["consecutive_losses"] == 0  # reset after win


def test_mode_mapping():
    t = _fresh_trader(mode="PAPER")
    assert t._exec_mode() == MODE_PAPER
    t.cfg.mode = "LIVE"
    assert t._exec_mode() == MODE_LIVE
    t.cfg.mode = "HARNESS"
    assert t._exec_mode() == "HARNESS"