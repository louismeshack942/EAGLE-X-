from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """A local EAGLE-X user (self-registered or provisioned).

    EAGLE-X never stores Deriv passwords. A user here links to Deriv
    authorizations stored in Authorization (encrypted).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    authorizations: Mapped[list["Authorization"]] = relationship(back_populates="user")


class Session(Base):
    """Web session for a signed-in user."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Authorization(Base):
    """Opaque reference to a connected Deriv account + encrypted token material.

    The Deriv OAuth access/refresh tokens are stored EXTERNALLY (secret store, env
    mediated) or encrypted here. This record is the application-level link the cockpit
    uses; raw tokens are never returned to the frontend.
    """

    __tablename__ = "authorizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    deriv_loginid: Mapped[str] = mapped_column(String(64), index=True, default="")
    scope: Mapped[str] = mapped_column(String(255), default="")
    token_ref: Mapped[str] = mapped_column(Text, default="")  # opaque id into secret store
    balance_currency: Mapped[str] = mapped_column(String(16), default="USD")
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="authorizations")


class Market(Base):
    """A Deriv synthetic/volatility index. Availability reads from live active_symbols."""

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(64), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tick(Base):
    """A normalized price tick for a symbol."""

    __tablename__ = "ticks"
    __table_args__ = (
        Index("ix_ticks_symbol_epoch", "symbol", "epoch_ms"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    epoch_ms: Mapped[int] = mapped_column(Integer, index=True)
    quote: Mapped[float]
    last_digit: Mapped[int] = mapped_column(Integer, default=-1)
    provider: Mapped[str] = mapped_column(String(16), default="deriv_live")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Signal(Base):
    """Signal placeholder — data model reserved for later phases.

    Phase 1 persists nothing predictive; this table exists so later phases can plug in
    without schema churn. No signal is generated in Phase 1.
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    contract: Mapped[str] = mapped_column(String(32), default="")
    direction: Mapped[str] = mapped_column(String(16), default="")
    state: Mapped[str] = mapped_column(String(32), default="NONE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Trade(Base):
    """Trade placeholder — reserved. Real-money execution is DISABLED in Phase 1.

    No trade rows are created by Phase 1 code paths; the schema is reserved so later
    phases can record results without migration churn.
    """

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    contract: Mapped[str] = mapped_column(String(32), default="")
    stake: Mapped[float] = mapped_column(default=0.0)
    direction: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SignalRecord(Base):
    """Persisted signal history (Phase 4 §15) — immutable append-mostly record.

    Stores the created/validated/qualified/expired/executed/rejected/result lifecycle
    for later performance analysis. Does NOT store any secret.
    """

    __tablename__ = "signal_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    contract_family: Mapped[str] = mapped_column(String(16))
    barrier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prediction: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(32), index=True)  # signal_state
    execution_state: Mapped[str] = mapped_column(String(32), default="NOT_ELIGIBLE")
    risk_state: Mapped[str] = mapped_column(String(16), default="NOT_RUN")
    estimated_probability: Mapped[float | None] = mapped_column(nullable=True)
    expected_value: Mapped[float | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="")
    proposal_source: Mapped[str] = mapped_column(String(16), default="")
    multi_window_state: Mapped[str] = mapped_column(String(32), default="INSUFFICIENT_DATA")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_ts: Mapped[float] = mapped_column(Integer, default=0)
    expiry: Mapped[float] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(Text, default="")  # full traceable evidence (JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TradeRecord(Base):
    """Persisted trade ledger (Phase 5 §28) — every trade across modes.

    `idempotency_key` is unique server-side to guarantee ONE purchase per key.
    `mode` is always present so HARNESS/PAPER/LIVE are never mixed silently.
    """

    __tablename__ = "trade_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    execution_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    contract_id: Mapped[str] = mapped_column(String(64), index=True, default="")
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    contract_type: Mapped[str] = mapped_column(String(32))
    prediction: Mapped[str] = mapped_column(String(64))
    stake: Mapped[float] = mapped_column(default=0.0)
    buy_price: Mapped[float | None] = mapped_column(nullable=True)
    payout: Mapped[float | None] = mapped_column(nullable=True)
    profit_loss: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="OPEN")
    error: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(64), default="")
    timestamps: Mapped[str] = mapped_column(Text, default="{}")  # JSON dict
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    """System status / audit event (non-secret)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


# Convenience import surface
__all__ = [
    "Authorization",
    "Event",
    "Market",
    "Session",
    "Signal",
    "SignalRecord",
    "Tick",
    "Trade",
    "TradeRecord",
    "User",
    "utcnow",
]