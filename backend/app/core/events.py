"""Async event bus for realtime status changes + tick broadcast to frontend WS.

Phase 1: events are status/reliability messages (CONNECTION_LOST, RECONNECTING,
MARKET_UNAVAILABLE, AUTHORIZATION_REQUIRED, SERVER_ERROR, tick). This is the bridge
between the connector/data layer and the frontend WebSocket endpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any

SUBJECT_TICKS = "ticks"
SUBJECT_STATUS = "status"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set] = {}  # subject -> set of asyncio.Queue

    def subject(self, name: str) -> "Subject":
        return Subject(self, name)

    def publish(self, subject: str, payload: Any) -> None:
        for q in list(self._subscribers.get(subject, ())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def subscribe(self, subject: str) -> "asyncio.Queue":
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.setdefault(subject, set()).add(q)
        return q

    def unsubscribe(self, subject: str, q) -> None:
        subs = self._subscribers.get(subject)
        if subs:
            subs.discard(q)


class Subject:
    def __init__(self, bus: EventBus, name: str) -> None:
        self.bus = bus
        self.name = name

    def publish(self, payload: Any) -> None:
        self.bus.publish(self.name, payload)

    async def subscribe(self) -> "asyncio.Queue":
        return await self.bus.subscribe(self.name)

    def unsubscribe(self, q) -> None:
        self.bus.unsubscribe(self.name, q)


event_bus = EventBus()