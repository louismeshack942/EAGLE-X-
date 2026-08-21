"""Deterministic synthetic tick generator — demo-mode fallback.

Uses Geometric Brownian Motion with a seeded PRNG so demo runs are
reproducible across restarts and backtests.
"""
import asyncio
import random
from datetime import datetime, timezone
from typing import AsyncGenerator

from app.config import get_settings
from app.models.tick import Tick


class DemoGenerator:
    def __init__(
        self,
        seed: int | None = None,
        start_price: float | None = None,
        drift: float | None = None,
        volatility: float | None = None,
        interval_ms: int | None = None,
    ):
        s = get_settings()
        self.seed = seed if seed is not None else s.demo_seed
        self.start_price = start_price if start_price is not None else s.demo_start_price
        self.drift = drift if drift is not None else s.demo_drift
        self.volatility = volatility if volatility is not None else s.demo_volatility
        self.interval_ms = interval_ms if interval_ms is not None else s.demo_tick_interval_ms

        # seeds keyed by symbol so each symbol gets its own deterministic stream
        self._prices: dict[str, float] = {}
        self._rngs: dict[str, random.Random] = {}

    def _get_rng(self, symbol: str) -> random.Random:
        if symbol not in self._rngs:
            # derive per-symbol seed deterministically
            sym_seed = self.seed + sum(ord(c) for c in symbol)
            rng = random.Random(sym_seed)
            self._rngs[symbol] = rng
            self._prices[symbol] = self.start_price * (1 + 0.05 * sum(ord(c) for c in symbol) / 100)
        return self._rngs[symbol]

    def _next_price(self, symbol: str) -> float:
        rng = self._get_rng(symbol)
        price = self._prices[symbol]
        # GBM step: S_{t+1} = S_t * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
        dt = self.interval_ms / 1000.0
        z = rng.gauss(0, 1)
        factor = 1 + self.drift * dt - 0.5 * self.volatility**2 * dt + self.volatility * (dt**0.5) * z
        new_price = max(0.0001, price * factor)
        self._prices[symbol] = new_price
        return new_price

    async def stream(self, symbol: str) -> AsyncGenerator[Tick, None]:
        """Yield synthetic ticks forever at the configured interval."""
        while True:
            quote = self._next_price(symbol)
            yield Tick(
                symbol=symbol,
                quote=round(quote, 4),
                timestamp=datetime.now(timezone.utc),
                provider="demo",
                quality=100,
                raw={"synthetic": True, "seed": self.seed},
            )
            await asyncio.sleep(self.interval_ms / 1000.0)
