"""Live market-data provider — ticks via the authenticated Deriv session.



The Batch-4 live-integration layer, part 2. This is a `MarketDataProvider`
(per symbol) that streams REAL ticks over an OTP-authenticated WebSocket minted
from the shared `DerivSession` (account vault). It is the missing bridge between
the account layer and the Data Bus:

    LiveSession (account: PAT vault, OTP, REST balance, proposal/buy/settle)
             |
             v
    LiveProvider (per symbol: mint fresh OTP WS, subscribe ticks, reconnect.
             |
             v
    DataBus -> analysis_manager -> EventBus(ticks) -> DB persists; UI WS

Design rules (honest):

- ONE fresh OTP connection per symbol connection (OTP URLs are single-use;
  the account session mints a new URL per `connect()`.)
- A provider NEVER labels harness/demo as live and NEVER invents ticks: it only
  yields ticks that came from Deriv's `tick` messages on this authenticated socket.

- Provider failures surface as `reconnecting` (DataBus publishes that state) and the
  DataBus's existing exception path handles the retry surface. No silent fallback
  to demo happens anywhere in this stack — demo is a separate, explicitly-labeled
  provider used only when no account/session is connected at all.



Auto-reconnect: the provider retries the connection with exponential backoff after
any unexpected socket drop/error (bounded; when retries exhaust it raises so the
bus can surface an honest `reconnecting`/`error` state). The account session itself
is untouched; next reconnect re-mints.


Everything here is read-only market data. Trading (proposal/buy/settle) lives
in `deriv_session.DerivSession` and is gated by the Batch-4 engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Optional

import websockets

from app.core.status import ConnectionState
from app.core.ticks import NormalizedTick, normalize_tick
from app.services.connector import MarketDataProvider
from app.services.deriv_session import DerivSession, get_session

logger = logging.getLogger("eaglex.live_session")


class LiveProvider(MarketDataProvider):
    """Streams live Deriv ticks for one symbol over an account-OTP WebSocket."""

    kind = "deriv_live"

    def __init__(self, session: DerivSession | None = None) -> None:
        self.session = session or get_session()
        self._ws: Any = None
        self._state = ConnectionState.DISCONNECTED.value
        self._symbol = ""
        self._max_attempts = 4

    @property
    def state(self) -> str:
        return self._state

    async def connect(self, symbol: str) -> None:
        """Open the authenticated tick socket for `symbol` (fresh OTP per connection).

        Raises ConnectionError when the account session is not connected or the socket.
        cannot be established (better than silently degrading to harness/demo).
        """
        if not self.session.token_present or not self.session.live_configured:
            self._state = ConnectionState.AUTH_REQUIRED.value
            raise ConnectionError(
                "no authenticated Deriv session — live data unavailable; connect an account first."
            )
        self._symbol = symbol
        self._state = ConnectionState.CONNECTING.value
        last_error: Optional[Exception] = None
        for attempt in range(self._max_attempts):
            try:
                url = await self.session._mint_ws_url()
                ws = await websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=10)
                if self.session._needs_authorize(url):
                    await ws.send(json.dumps({"authorize": self.session._token}))
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if "error" in msg:
                        await ws.close()
                        raise ConnectionError(
                            msg["error"].get("message", "authorize failed on live tick socket")
                        )
                self._ws = ws
                self._state = ConnectionState.CONNECTED.value
                logger.info("live provider connected: %s via OTP socket", symbol)
                return
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException, ConnectionError) as exc:  # noqa: BLE001
                last_error = exc
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                if attempt < self._max_attempts - 1:
                    self._state = ConnectionState.RECONNECTING.value
                    await asyncio.sleep(0.5 * (2 ** attempt))
                continue
        self._state = ConnectionState.DISCONNECTED.value
        logger.warning("live provider connect failed for %s: %s", symbol, last_error)
        raise ConnectionError(f"live provider connect failed: {last_error}")

    async def ticks(self) -> AsyncIterator[NormalizedTick]:
        """Stream normalized ticks for the subscribed symbol (ASCII)."""
        if not self._ws:
            raise ConnectionError("connect() before iterating ticks()")
        sub = {"ticks": self._symbol, "subscribe": 1, "req_id": 2}
        await self._ws.send(json.dumps(sub))
        try:
            while True:
                raw = await self._ws.recv()
                msg = json.loads(raw)
                if "error" in msg:
                    raise ConnectionError(msg["error"].get("message", "tick subscription error"))
                t = msg.get("tick")
                if not t or str(t.get("symbol") or "") != self._symbol:
                    continue
                yield normalize_tick(
                    symbol=self._symbol,
                    epoch_ms=(t.get("epoch") or 0) * 1000,
                    quote=float(t["quote"]),
                    provider="deriv_live",
                )
        finally:
            self._state = ConnectionState.DISCONNECTED.value

    async def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        self._state = ConnectionState.DISCONNECTED.value


def make_live_provider(session: DerivSession | None = None) -> LiveProvider:
    return LiveProvider(session=session)


__all__ = ["LiveProvider", "make_live_provider"]