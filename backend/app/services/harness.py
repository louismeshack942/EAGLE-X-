"""HARNESS — development/test tick provider. NOT REAL, NEVER labeled live.

This provider exists so the app is runnable without live Deriv credentials. It emits
deterministic pseudo-random ticks. Every tick is tagged provider='harness' and every
consumer must surface that honestly (the frontend labels feed source: HARNESS).

The harness NEVER masquerades as real data.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import AsyncIterator

from app.core.ticks import NormalizedTick, normalize_tick
from app.services.connector import MarketDataProvider


class HarnessProvider(MarketDataProvider):
    kind = "harness"

    def __init__(self, interval_ms: int = 500, seed: int | None = None) -> None:
        self.interval_s = interval_ms / 1000.0
        self._rng = random.Random(seed)
        self._price = 5000.0
        self._running = False
        self._state = "disconnected"

    async def connect(self, symbol: str) -> None:
        self._symbol = symbol
        self._state = "connected"
        self._running = True

    async def ticks(self) -> AsyncIterator[NormalizedTick]:
        while self._running:
            step = self._rng.uniform(-5.0, 5.0)
            self._price += step
            # Synthetic-style whole-ish quote at a few decimals
            quote = round(self._price, 4)
            yield normalize_tick(
                symbol=self._symbol,
                epoch_ms=int(time.time() * 1000),
                quote=quote,
                provider="harness",
            )
            await asyncio.sleep(self.interval_s)

    async def close(self) -> None:
        self._running = False
        self._state = "disconnected"

    @property
    def state(self) -> str:
        return self._state