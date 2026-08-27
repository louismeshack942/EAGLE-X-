"""Deriv real WebSocket tick client (architecture for live connectivity).

Endpoint facts (publicly documented):
- Public tick stream: wss://ws.derivws.com/websockets/v3
- Authenticated options WS: wss://api.derivws.com/trading/v1/options/ws/<demo|real>?otp=...
- Auth: an app id is required; OAuth access tokens / PAT add authorization.

Phase 1 behavior:
- `app_id` from env if set => connect to the public websocket and subscribe ticks.
- If no app_id/credentials, connect() raises a clear 'not configured' error rather
  than faking data. The app then offers the HarnessProvider (explicitly labeled) or an
  'authorization required' state.

Tick message shape (public): {"tick": {"id":..., "symbol":..., "quote":..., "epoch":...}}
"""

from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any, AsyncIterator

import websockets

from app.config import settings
from app.core.status import ConnectionState
from app.core.ticks import NormalizedTick, normalize_tick
from app.services.connector import MarketDataProvider


class DerivClient(MarketDataProvider):
    kind = "deriv_live"

    def __init__(self, app_id: str, token: str | None = None) -> None:
        self.app_id = app_id
        self.token = token
        self._ws: Any = None
        self._running = False
        self._state = ConnectionState.DISCONNECTED.value
        self._symbol = ""

    @property
    def state(self) -> str:
        return self._state

    async def connect(self, symbol: str) -> None:
        if not self.app_id:
            raise ConnectionError("DERIV_APP_ID not configured; cannot connect to live feed.")
        self._symbol = symbol
        url = f"{settings.deriv_ws_url}?app_id={self.app_id}"
        self._state = ConnectionState.CONNECTING.value
        ssl_ctx = ssl.create_default_context()
        self._ws = await websockets.connect(url, ssl=ssl_ctx, ping_interval=15, ping_timeout=10)
        if self.token:
            await self._ws.send(json.dumps({"authorize": self.token, "req_id": 1}))
            resp = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=10))
            if "error" in resp:
                raise ConnectionError(f"Deriv authorize failed: {resp['error'].get('message')}")
        self._state = ConnectionState.CONNECTED.value

    async def ticks(self) -> AsyncIterator[NormalizedTick]:
        if not self._ws:
            raise ConnectionError("connect() before iterating ticks()")
        sub = {"ticks": self._symbol, "subscribe": 1, "req_id": 2}
        await self._ws.send(json.dumps(sub))
        try:
            while self._running or True:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                t = msg.get("tick")
                if not t:
                    # optionally handle subscription ack / errors
                    if "error" in msg:
                        raise ConnectionError(
                            f"Deriv tick subscription error: {msg['error'].get('message')}"
                        )
                    continue
                yield normalize_tick(
                    symbol=self._symbol,
                    epoch_ms=t.get("epoch", 0) * 1000 if t.get("epoch") else 0,
                    quote=float(t["quote"]),
                    provider="deriv_live",
                )
        finally:
            self._state = ConnectionState.DISCONNECTED.value

    async def close(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._state = ConnectionState.DISCONNECTED.value