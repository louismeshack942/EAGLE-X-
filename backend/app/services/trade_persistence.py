"""Persistence helpers for signal history (Phase 4 §15) and the trade ledger (§28).

These write the in-memory records to SQLite/Postgres so history survives restarts. All
writes are non-fatal to the realtime pipeline. No secrets are ever stored.
"""

from __future__ import annotations

import json
import logging

from app.db import SessionLocal
from app.models.models import SignalRecord, TradeRecord

logger = logging.getLogger("eaglex.persistence")


def save_signal(sig: dict) -> None:
    try:
        db = SessionLocal()
        try:
            db.add(SignalRecord(
                signal_id=sig.get("signal_id", ""),
                symbol=sig.get("symbol", ""),
                contract_family=sig.get("contract_family", ""),
                barrier=sig.get("barrier"),
                prediction=sig.get("prediction", ""),
                state=sig.get("signal_state", sig.get("state", "NO_SIGNAL")),
                execution_state=sig.get("execution_state", "NOT_ELIGIBLE"),
                risk_state=sig.get("risk_state", "NOT_RUN"),
                estimated_probability=sig.get("estimated_probability"),
                expected_value=sig.get("expected_value"),
                source=sig.get("source", ""),
                proposal_source=sig.get("proposal_source", ""),
                multi_window_state=sig.get("multi_window_state", "INSUFFICIENT_DATA"),
                reason=sig.get("reason", ""),
                created_ts=int(sig.get("created_ts", 0)),
                expiry=int(sig.get("expiry", 0)),
                payload=json.dumps(sig),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # media never lets persistence break realtime
        logger.warning("signal persist failed (non-fatal): %s", exc)


def save_trade(entry: dict) -> None:
    try:
        db = SessionLocal()
        try:
            db.add(TradeRecord(
                trade_id=entry.get("trade_id", ""),
                signal_id=entry.get("signal_id", ""),
                execution_id=entry.get("execution_id", ""),
                contract_id=entry.get("contract_id", ""),
                idempotency_key=entry.get("idempotency_key", entry.get("contract_id", "")),
                mode=entry.get("mode", "HARNESS"),
                symbol=entry.get("symbol", ""),
                contract_type=entry.get("contract_type", ""),
                prediction=entry.get("prediction", ""),
                stake=entry.get("stake", 0.0),
                buy_price=entry.get("buy_price"),
                payout=entry.get("payout"),
                profit_loss=entry.get("profit_loss"),
                status=entry.get("status", "OPEN"),
                error=entry.get("error", ""),
                source=entry.get("source", ""),
                timestamps=json.dumps(entry.get("timestamps", {})),
            ))
            db.commit()
        finally:
            db.close()
    except Exception as exc:  # uniqueness on retry must not crash the caller
        logger.warning("trade persist failed (non-fatal): %s", exc)


def list_signals(limit: int = 50) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(SignalRecord)
            .order_by(SignalRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "signal_id": r.signal_id,
                "symbol": r.symbol,
                "contract_family": r.contract_family,
                "barrier": r.barrier,
                "prediction": r.prediction,
                "state": r.state,
                "execution_state": r.execution_state,
                "risk_state": r.risk_state,
                "estimated_probability": r.estimated_probability,
                "expected_value": r.expected_value,
                "source": r.source,
                "proposal_source": r.proposal_source,
                "multi_window_state": r.multi_window_state,
                "reason": r.reason,
            }
            for r in rows
        ]
    finally:
        db.close()


def list_trades(limit: int = 100, mode: str = "") -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(TradeRecord).order_by(TradeRecord.created_at.desc())
        if mode:
            q = q.filter(TradeRecord.mode == mode)
        rows = q.limit(limit).all()
        return [
            {
                "trade_id": r.trade_id,
                "signal_id": r.signal_id,
                "contract_id": r.contract_id,
                "mode": r.mode,
                "symbol": r.symbol,
                "contract_type": r.contract_type,
                "prediction": r.prediction,
                "stake": r.stake,
                "buy_price": r.buy_price,
                "payout": r.payout,
                "profit_loss": r.profit_loss,
                "status": r.status,
                "error": r.error,
            }
            for r in rows
        ]
    finally:
        db.close()


__all__ = ["list_signals", "list_trades", "save_signal", "save_trade"]