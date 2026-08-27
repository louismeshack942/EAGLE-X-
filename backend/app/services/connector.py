"""Connection provider abstraction.

A Provider produces NormalizedTick objects for a symbol. Phase 1 ships two providers:

- DerivClient: connects to the real Deriv WebSocket API (OAuth/PAT authorized or public).
- HarnessProvider: a determinstic tick generator used ONLY for development/testing.
                     It is NEVER labeled "live" or "real".

Choosing a provider must be explicit; nothing labels a harness as live.
"""

from __future__ import annotations

import abc
from typing import AsyncIterator

from app.core.ticks import NormalizedTick


class MarketDataProvider(abc.ABC):
    kind: str = "abstract"

    @abc.abstractmethod
    async def connect(self, symbol: str) -> None:
        ...

    @abc.abstractmethod
    def ticks(self) -> AsyncIterator[NormalizedTick]:
        """Return an async generator of normalized ticks. Implement as `async def` + `yield`."""
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...

    @property
    @abc.abstractmethod
    def state(self) -> str:
        ...


__all__ = ["MarketDataProvider"]