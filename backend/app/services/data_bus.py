"""Data bus — runs a provider for a symbol, persists normalized ticks, broadcasts.

DERIV -> Provider -> normalize -> [DB persist] -> EventBus(ticks)
                                   +-> analysis_manager (realtime windows)
                                   +-> EventBus(status)

Recorder faults never interrupt ingestion.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.core.events import SUBJECT_STATUS, SUBJECT_TICKS, event_bus
from app.core.status import ConnectionState
from app.db import SessionLocal
from app.models.models import Tick
from app.services.analysis_engine import analysis_manager
from app.services.connector import MarketDataProvider
from app.services.harness import HarnessProvider

logger = logging.getLogger("eaglex.data_bus")


def _persist_tick(tick: Tick) -> None:
    db = SessionLocal()
    try:
        db.add(tick)
        db.commit()
    finally:
        db.close()


class DataBus:
    def __init__(self, provider: MarketDataProvider) -> None:
        self.provider = provider
        self.persist = True
        self._task: asyncio.Task | None = None
        self.latest: dict[str, dict] = {}

    def start(self, symbol: str) -> asyncio.Task:
        if self._task and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._run(symbol))
        return self._task

    def provider_connected(self, symbol: str) -> None:
        try:
            analysis_manager.mark_connection(
                symbol, self.provider.state or ""
            )
        except Exception:
            pass

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.provider.close()

    async def _run(self, symbol: str) -> None:
        try:
            await self.provider.connect(symbol)
            self.provider_connected(symbol)
            event_bus.publish(
                SUBJECT_STATUS, {"symbol": symbol, "state": self.provider.state, "kind": "connection"}
            )
            async for tick in self.provider.ticks():
                # Realtime analysis (Phase 2): feed the window engine + broadcast.
                try:
                    analysis_manager.push(tick)
                except Exception:  # analysis faults never interrupt ingestion
                    logger.exception("realtime analysis push failed (non-fatal)")
                event_bus.publish(SUBJECT_TICKS, tick.__dict__)
                self.latest[symbol] = tick.__dict__
                if self.persist:
                    try:
                        _persist_tick(
                            Tick(
                                symbol=tick.symbol,
                                epoch_ms=tick.epoch_ms,
                                quote=tick.quote,
                                last_digit=tick.last_digit,
                                provider=tick.provider,
                            )
                        )
                    except Exception:  # recorder faults never interrupt ingestion
                        logger.exception("tick persist failed (non-fatal)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # surface honest error state
            logger.exception("data bus provider error")
            event_bus.publish(
                SUBJECT_STATUS,
                {
                    "symbol": symbol,
                    "state": ConnectionState.RECONNECTING.value,
                    "kind": "connection",
                    "error": str(exc),
                },
            )
            raise


def make_provider(*, use_harness: bool = False, app_id: str = "", token: str = ""):
    from app.services.deriv_client import DerivClient

    if use_harness:
        return HarnessProvider()
    if app_id:
        return DerivClient(app_id=app_id, token=token)
    return HarnessProvider() if settings.use_unauth_public_data else None


__all__ = ["DataBus", "make_provider"]