"""Deriv trade execution — place real trades via Deriv's WebSocket API.

IMPORTANT: api_token is passed per-request and is NEVER stored server-side.
"""
import asyncio
import json
import time
from typing import Optional

import websockets

from app.config import get_settings


class DerivTrader:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def authorize(self, api_token: str) -> dict:
        url = f"{self.settings.deriv_ws_url.rstrip('/')}/websocket?app_id={self.settings.deriv_app_id}&l=EN"
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if "error" in msg:
                return {"status": "error", "error": msg["error"].get("message", "authorize failed")}
            return {"status": "ok", "account": msg.get("authorize", {})}

    async def place_trade(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: int,
        api_token: str,
        duration_unit: str = "t",
    ) -> dict:
        """Full trade flow: connect → authorize → proposal → buy → return confirmation."""
        url = f"{self.settings.deriv_ws_url.rstrip('/')}/websocket?app_id={self.settings.deriv_app_id}&l=EN"
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
            await ws.send(json.dumps({"authorize": api_token}))
            auth_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            auth_msg = json.loads(auth_raw)
            if "error" in auth_msg:
                return {"status": "error", "step": "authorize", "error": auth_msg["error"].get("message")}

            proposal_req = {
                "proposal": 1,
                "amount": float(amount),
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "duration": int(duration),
                "duration_unit": duration_unit,
                "symbol": symbol,
                "product_type": "basic",
            }
            await ws.send(json.dumps(proposal_req))
            prop_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            prop_msg = json.loads(prop_raw)
            if "error" in prop_msg:
                return {"status": "error", "step": "proposal", "error": prop_msg["error"].get("message")}
            proposal = prop_msg.get("proposal", {})
            proposal_id = proposal.get("id")
            price = proposal.get("ask_price", amount)

            await ws.send(json.dumps({"buy": proposal_id, "price": price}))
            buy_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            buy_msg = json.loads(buy_raw)
            if "error" in buy_msg:
                return {"status": "error", "step": "buy", "error": buy_msg["error"].get("message")}
            buy = buy_msg.get("buy", {})
            return {
                "status": "success",
                "contract_id": str(buy.get("contract_id", "")),
                "transaction_id": str(buy.get("transaction_id", "")),
                "buy_price": buy.get("buy_price", price),
                "payout": proposal.get("payout"),
            }


deriv_trader = DerivTrader()
