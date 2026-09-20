"""Deriv WebSocket client — connects, (optionally) authorizes, subscribes and
streams REAL ticks.

Key facts about the Deriv API that drive this design:

* Market data (ticks) is PUBLIC — no token required. The platform runs in
  true LIVE mode without any credentials.
* A personal API token is only needed for account data and trading
  (authorize/buy). It comes from the TokenVault (OAuth or manual entry) or
  the DERIV_API_TOKEN environment variable.
* Deriv serves no symbols to clients in restricted countries (e.g. the US).
  The client detects that case and reports it honestly instead of silently
  degrading.

Falls back to the deterministic demo generator only when the live WebSocket
cannot be reached at all. Provider separation is absolute: every tick is
stamped "deriv_live" or "demo" — the two streams never mix.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import AsyncGenerator, Callable, Dict, List, Optional

import websockets
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models.tick import Tick
from app.services.token_vault import VAULT
from app.services.deriv_trader import deriv_trader


class GeoRestrictedError(ConnectionError):
    """Deriv refuses all symbols for this egress IP (e.g. US clients)."""


# Credential-free public market-data endpoint. Streams the synthetic board
# from any region (the generic ws.derivws.com endpoint geo-blocks) and cannot
# place orders, so it is the correct feed for an analysis-only deployment.
PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"


class LiveState:
    """Connection/mode bookkeeping shared across the app."""

    def __init__(self) -> None:
        self.mode: str = "demo"  # "live" | "demo"
        self.connected: bool = False
        self.authorized: bool = False
        self.clients_country: Optional[str] = None
        self.last_tick_at: Dict = {}
        self.last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "connected": self.connected,
            "is_live": self.connected and self.mode == "live",
            "trading_enabled": self.authorized,
            "clients_country": self.clients_country,
            "data_label": "LIVE DATA" if self.mode == "live" else "DEMO DATA",
            "last_error": self.last_error,
        }


LIVE_STATE = LiveState()


async def resolve_token() -> str:
    """Vault token (user-connected account) wins over env config."""
    vault_token = await VAULT.get()
    if vault_token:
        return vault_token
    return get_settings().deriv_api_token.strip()


class DerivClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.authorized = False

    async def _connect(self) -> bool:
        """Resolve the URL. ANALYSIS-ONLY deployments use the PUBLIC endpoint.

        wss://api.derivws.com/trading/v1/options/ws/public needs NO
        authentication and NO OTP — it streams the full 41-symbol synthetic
        board (verified: R_100 ticks, 89 active_symbols) even from an egress
        that the generic endpoint geo-blocks outright. That is exactly what an
        analysis-only deployment wants: real ticks, zero credentials on the
        box. It also cannot place an order, so it is a hard structural limit,
        not a policy one.

        When trading is deliberately re-enabled we fall back to the account
        OTP path (the public endpoint is market-data only).
        """
        try:
            if self.settings.analysis_only:
                self.ws = await websockets.connect(
                    PUBLIC_WS_URL, ping_interval=20, ping_timeout=10, open_timeout=10
                )
                return True
            token = await resolve_token()
            url = await deriv_trader._url(token)
            self.ws = await websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=10)
            return True
        except Exception as exc:  # noqa: BLE001
            LIVE_STATE.last_error = f"connect failed: {exc}"
            return False

    async def _detect_country(self) -> None:
        try:
            assert self.ws is not None
            await self.ws.send(json.dumps({"website_status": 1}))
            raw = await asyncio.wait_for(self.ws.recv(), timeout=8)
            msg = json.loads(raw)
            LIVE_STATE.clients_country = (msg.get("website_status") or {}).get("clients_country")
        except Exception:
            pass

    async def authorize(self) -> bool:
        """Optional: only needed for trading. Market data streams regardless.

        ANALYSIS-ONLY: when settings.analysis_only is set we authorize with
        read_only=1. That is a SERVER-SIDE lock — Deriv itself refuses any
        buy on this session, so it holds even if our own guard were bypassed.
        """
        token = await resolve_token()
        if not self.ws or not token:
            return False
        try:
            payload = {"authorize": token}
            if self.settings.analysis_only:
                payload["read_only"] = 1
            await self.ws.send(json.dumps(payload))
            raw = await asyncio.wait_for(self.ws.recv(), timeout=10)
            msg = json.loads(raw)
            if "error" in msg:
                LIVE_STATE.last_error = f"authorize error: {msg['error'].get('message', 'unknown')}"
                return False
            auth = msg.get("authorize") or {}
            self.authorized = True
            LIVE_STATE.authorized = True
            await VAULT.set(token, loginid=auth.get("loginid"), currency=auth.get("currency"))
            balance = auth.get("balance")
            if balance is not None:
                await VAULT.set_balance(float(balance))
            return True
        except Exception as exc:  # noqa: BLE001
            LIVE_STATE.last_error = f"authorize failed: {exc}"
            return False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5))
    async def _subscribe(self, symbol: str) -> None:
        if not self.ws:
            raise ConnectionError("not connected")
        await self.ws.send(json.dumps({"ticks": symbol, "subscribe": 1}))

    async def stream(self, symbols: List[str]) -> AsyncGenerator[Tick, None]:
        """Yield live ticks. Raises if the live stream cannot establish."""
        if not await self._connect():
            raise ConnectionError(LIVE_STATE.last_error or "connect failed")

        await self._detect_country()
        # Authorize when a token exists — but never block market data on it.
        # The public endpoint is unauthenticated: there is no account session
        # to authorize, and sending a token would only draw an error. Skipping
        # it is what makes this feed credential-free.
        if not self.settings.analysis_only:
            await self.authorize()

        for symbol in symbols:
            await self._subscribe(symbol)

        LIVE_STATE.mode = "live"
        LIVE_STATE.connected = True
        LIVE_STATE.last_error = None

        invalid_symbols: set = set()
        valid_symbols: set = set()

        assert self.ws is not None
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)

            if "error" in msg:
                req = msg.get("echo_req") or {}
                sym = req.get("ticks")
                if sym and msg["error"].get("code") == "InvalidSymbol":
                    invalid_symbols.add(sym)
                    if not valid_symbols and invalid_symbols >= set(symbols):
                        LIVE_STATE.mode = "demo"
                        LIVE_STATE.connected = False
                        country = LIVE_STATE.clients_country or "unknown"
                        raise GeoRestrictedError(
                            f"Deriv serves no symbols to country '{country}' — "
                            f"deploy the backend in a supported region (e.g. Frankfurt)"
                        )
                continue

            if "tick" in msg:
                t = msg["tick"]
                valid_symbols.add(t.get("symbol", ""))
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
    symbols: List[str],
    on_tick: Callable[[Tick], None],
    demo_factory,
) -> None:
    """Run the live stream; on failure fall back to deterministic demo ticks.

    This is the top-level ingestion loop run as a background task by main.py.
    The live connection is re-probed periodically even while demo runs.
    """
    client = DerivClient()
    settings = get_settings()
    while True:
        # Live-only mode: once a token is configured (env or vault), demo
        # ticks are NEVER emitted — a fake tape is worse than an honest
        # "reconnecting" state. ANALYSIS-ONLY deployments are likewise
        # live-only: the public feed needs no token, so the absence of one
        # says nothing about live availability, and falling back to the GBM
        # generator would silently manufacture the fake digits this system
        # has already been burned by once.
        token_configured = bool(await resolve_token()) or settings.analysis_only
        try:
            async for tick in client.stream(symbols):
                on_tick(tick)
        except GeoRestrictedError as exc:
            # The geo block only hits the generic endpoint; with a token the
            # account OTP path bypasses it, so re-probe fast instead of
            # parking in demo for 5 minutes.
            probe = 20.0 if token_configured else 300.0
            if token_configured:
                LIVE_STATE.mode = "live"  # intent: live-only, never demo
                LIVE_STATE.connected = False
                LIVE_STATE.last_error = f"reconnecting to live feed: {exc}"
                await asyncio.sleep(probe)
            else:
                LIVE_STATE.mode = "demo"
                LIVE_STATE.connected = False
                LIVE_STATE.last_error = str(exc)
                await _demo_with_live_retry(symbols, on_tick, demo_factory, probe_after=probe)
        except Exception as exc:  # noqa: BLE001
            if token_configured:
                LIVE_STATE.mode = "live"  # intent: live-only, never demo
                LIVE_STATE.connected = False
                LIVE_STATE.last_error = f"reconnecting to live feed: {exc}"
                await asyncio.sleep(15.0)
            else:
                LIVE_STATE.mode = "demo"
                LIVE_STATE.connected = False
                LIVE_STATE.last_error = str(exc)
                await _demo_with_live_retry(symbols, on_tick, demo_factory, probe_after=20.0)


async def _demo_with_live_retry(symbols, on_tick, demo_factory, probe_after: float) -> None:
    """Stream demo ticks while periodically probing whether live is back."""
    demo = demo_factory()
    deadline = time.monotonic() + probe_after
    try:
        await asyncio.gather(*[_consume_symbol(demo, sym, on_tick, deadline) for sym in symbols])
    except (TimeoutError, asyncio.TimeoutError):
        return


async def _consume_symbol(demo, symbol, on_tick, deadline: float) -> None:
    async for tick in demo.stream(symbol):
        on_tick(tick)
        if time.monotonic() > deadline:
            raise TimeoutError("probe live again")
