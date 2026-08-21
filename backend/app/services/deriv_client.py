"""Deriv WebSocket client — connects, authorizes, subscribes and streams ticks.

Falls back silently to demo mode when no API token is configured or when the
live connection fails. Provider separation: every tick is stamped as
"deriv_live" or "demo" — the two streams are never mixed.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Callable, Dict, Optional

import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.tick import Tick


class LiveState:
    """Connection/mode bookkeeping shared across the app."""

    def __init__(self) -> None:
        self.mode: str = "demo"  # "live" | "demo"
        self.connected: bool = False
        self.last_tick_at: Dict = {}
        self.last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "connected": self.connected,
            "is_live": self.connected and self.mode == "live",
            "data_label": "LIVE DATA" if self.mode == "live" else "DEMO DATA",
            "last_error": self.last_error,
        }


LIVE_STATE = LiveState()


class DerivClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._authorized = False

    async def _connect(self) -> bool:
        try:
            url = f"{self.settings.deriv_ws_url.rstrip('/')}/websocket?app_id={self.settings.deriv_app_id}&l=EN"
            self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=10)
            return True
        except Exception as exc:  # noqa: BLE001
            LIVE_STATE.last_error = f"connect failed: {exc}"
            return False

    async def authorize(self) -> bool:
        token = self.settings.deriv_api_token.strip()
        if not self.ws:
            return False
        if not token:
            LIVE_STATE.last_error = "no DERIV_API_TOKEN — demo mode"
            return False
        try:
            await self.ws.send(json.dumps({"authorize": token}))
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            msg = json.loads(raw)
            if "error" in msg:
                LIVE_STATE.last_error = f"authorize error: {msg['error'].get('message', 'unknown')}"
                return False
            self._authorized = True
            return True
        except Exception as exc:  # noqa: BLE001
            LIVE_STATE.last_error = f"authorize failed: {exc}"
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def _subscribe(self, symbol: str) -> None:
        if not self.ws:
            raise ConnectionError("not connected")
        await self.ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))

    async def stream(self, symbols: list[str]) -> AsyncGenerator[Tick, None]:
        """Yield live ticks. Raises if the live stream cannot establish."""
        if not await self._connect():
            raise ConnectionError(LIVE_STATE.last_error or "connect failed")
        if not await self.authorize():
            raise PermissionError(LIVE_STATE.last_error or "authorize failed")
        for symbol in symbols:
            await self._subscribe(symbol)
        LIVE_STATE.mode = "live"
        LIVE_STATE.connected = True
        LIVE_STATE.last_error = None

        assert self.ws is not None
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)
            if "tick" in msg:
                t = msg["tick"]
                tick = Tick(
                    symbol=t.get("symbol", ""),
                    quote=float(t.get("quote", 0)),
                    timestamp=datetime.fromtimestamp(t.get("epoch", time.time()), tz=timezone.utc),
                    provider="deriv_live",
                    quality=100,
                    raw=msg,
                )
                LIVE_STATE.last_tick_at[tick.symbol] = datetime.now(timezone.utc)
                yield tick


async def stream_lifecycle(
    symbols: list[str],
    on_tick: Callable[[Tick], None],
    demo_factory,
    poll_seconds: float = 0.25,
) -> None:
    """Run live stream; on failure fall back to deterministic demo generator.

    This is the top-level ingestion loop run as a background task by main.py.
    """
    client = DerivClient()
    while True:
        try:
            async for tick in client.stream(symbols):
                on_tick(tick)
        except Exception as exc:  # noqa: BLE001
            LIVE_STATE.mode = "demo"
            LIVE_STATE.connected = False
            LIVE_STATE.last_error = str(exc)
            # Fall into the demo generator; keep retrying live opportunistically
            demo = demo_factory()
            tasks = [
                _stream_demo_symbol(demo, sym, on_tick) for sym in symbols
            ]
            await asyncio.gather(*tasks)


async def _stream_demo_symbol(demo, symbol: str, on_tick) -> None:
    async for tick in demo.stream(symbol):
        on_tick(tick)
