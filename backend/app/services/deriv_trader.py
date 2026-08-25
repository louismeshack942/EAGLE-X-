"""Deriv trade execution — place real trades via Deriv's WebSocket API.

IMPORTANT: api_token is passed per-request and is NEVER stored server-side.

Internal contract names map to Deriv's API names (Deriv rejects anything
else), digit contracts carry a barrier, and a trade is only resolved when
Deriv reports the contract settled — a successful *purchase* is not a win.
"""
import asyncio
import json
from typing import Optional

import httpx
import websockets

from app.config import get_settings
from app.services.token_vault import VAULT

# Internal name -> Deriv API contract_type.
DERIV_CONTRACT_TYPES = {
    "MATCHES": "DIGITMATCH",
    "DIFFERS": "DIGITDIFF",
    "ODD": "DIGITODD",
    "EVEN": "DIGITEVEN",
    "OVER": "DIGITOVER",
    "UNDER": "DIGITUNDER",
}
# Digit contracts require a barrier (the digit being bet on/for/against).
BARRIER_TYPES = {"DIGITMATCH", "DIGITDIFF", "DIGITOVER", "DIGITUNDER"}
# Digit contracts that need no barrier.
PARITY_TYPES = {"DIGITODD", "DIGITEVEN"}
# Non-digit Deriv contracts that pass through untouched (Trade Planner).
PASSTHROUGH_TYPES = {"CALL", "PUT", "ONETOUCH", "NOTOUCH", "EXPIRYMISS", "EXPIRYRANGE", "RANGE", "UPORDOWN"}
KNOWN_TYPES = BARRIER_TYPES | PARITY_TYPES | PASSTHROUGH_TYPES

SETTLE_TIMEOUT_S = 180  # give up tracking a contract after 3 minutes


def deriv_contract_params(contract_type: str, digit: Optional[int]) -> dict:
    """Translate a contract name into Deriv proposal fields.

    Accepts internal names (MATCHES/DIFFERS/...) and Deriv-native names
    (DIGITMATCH/CALL/...). Digit contracts carry a barrier.
    """
    name = (contract_type or "").upper()
    ct = DERIV_CONTRACT_TYPES.get(name, name)
    if ct not in KNOWN_TYPES:
        raise ValueError(f"Unknown contract type for Deriv: {contract_type!r}")
    params: dict = {"contract_type": ct}
    if ct in BARRIER_TYPES:
        if digit is None:
            raise ValueError(f"{ct} requires a digit barrier")
        params["barrier"] = str(int(digit))
    return params


class DerivTrader:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def _mint_fresh_otp(self, token: str) -> Optional[str]:
        """Mint a fresh OTP-authenticated websocket URL for a PAT token.

        OTP URLs are single-use and short-lived, so a new one is required for
        every connection; reusing the stored one makes trades flaky. Returns
        None when it can't be minted (caller falls back)."""
        account_id = await VAULT.get_account_id()
        if not account_id:
            return None
        stored_app_id = await VAULT.get_app_id()
        base = self.settings.deriv_rest_base.rstrip("/")
        variants = []
        if stored_app_id:
            variants.append({"Authorization": f"Bearer {token}", "Deriv-App-ID": str(stored_app_id)})
        variants.append({"Authorization": f"Bearer {token}"})
        async with httpx.AsyncClient(timeout=15) as client:
            for headers in variants:
                for attempt in range(3):
                    try:
                        resp = await client.post(f"{base}/options/accounts/{account_id}/otp", headers=headers)
                    except httpx.HTTPError:
                        continue
                    if resp.status_code == 200:
                        url = (resp.json().get("data") or {}).get("url")
                        if url:
                            return url
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    break
        return None

    async def _url(self, token: Optional[str] = None) -> str:
        """Pick the websocket endpoint. For a PAT token vaulted at connect
        time, mint a FRESH OTP URL per connection (the stored one is a
        fallback). Legacy tokens use the classic websockets/v3 endpoint."""
        if token and await VAULT.get() == token:
            fresh = await self._mint_fresh_otp(token)
            if fresh:
                return fresh
            ws_url = await VAULT.get_ws_url()
            if ws_url:
                return ws_url
            if await VAULT.get_account_id():
                # PAT flow is active but OTP minting failed. The generic
                # endpoint is geo-blocked for this deployment — falling back
                # to it guarantees InvalidSymbol and a silent slide into demo
                # data. Refuse instead; the caller retries shortly.
                raise ConnectionError(
                    "OTP mint failed and the generic endpoint is geo-blocked — "
                    "retrying rather than degrading to demo data"
                )
        # deriv_ws_url already points at the websockets/v3 path; appending
        # "/websocket" would 404 (Deriv redirects that to an HTML page).
        return f"{self.settings.deriv_ws_url.rstrip('/')}?app_id={self.settings.deriv_app_id}&l=EN"

    @staticmethod
    def _needs_authorize(url: str) -> bool:
        """OTP URLs are pre-authenticated; only legacy ones need authorize."""
        return "otp=" not in url

    @staticmethod
    async def _send_recv(ws, payload: dict, timeout: float = 10.0) -> dict:
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)

    async def authorize(self, api_token: str) -> dict:
        url = await self._url(api_token)
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
            if not self._needs_authorize(url):
                return {"status": "ok", "account": {}}
            msg = await self._send_recv(ws, {"authorize": api_token})
            if "error" in msg:
                return {"status": "error", "error": msg["error"].get("message", "authorize failed")}
            return {"status": "ok", "account": msg.get("authorize", {})}

    async def get_balance(self, api_token: str) -> Optional[float]:
        """Authorize (or reuse the OTP session) and read the account balance."""
        try:
            url = await self._url(api_token)
            async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
                if self._needs_authorize(url):
                    msg = await self._send_recv(ws, {"authorize": api_token})
                    if "error" in msg:
                        return None
                    bal = (msg.get("authorize") or {}).get("balance")
                    return float(bal) if bal is not None else None
                bal_msg = await self._send_recv(ws, {"balance": 1})
                if "error" in bal_msg:
                    return None
                bal = (bal_msg.get("balance") or {}).get("balance")
                return float(bal) if bal is not None else None
        except Exception:
            return None

    async def _await_settlement(self, ws, contract_id: str) -> dict:
        """Follow proposal_open_contract until Deriv marks the contract sold.

        Returns {"won": bool, "pnl": float, "sell_price": float}.
        """
        await ws.send(json.dumps({
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }))
        deadline = asyncio.get_event_loop().time() + SETTLE_TIMEOUT_S
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"contract {contract_id} did not settle within {SETTLE_TIMEOUT_S}s")
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if "error" in msg:
                raise RuntimeError(msg["error"].get("message", "settlement tracking failed"))
            poc = msg.get("proposal_open_contract") or {}
            if not poc:
                continue
            if poc.get("is_sold") or poc.get("is_expired"):
                profit = float(poc.get("profit", 0.0))
                return {
                    "won": profit > 0,
                    "pnl": round(profit, 2),
                    "sell_price": poc.get("sell_price"),
                    "status_label": poc.get("status"),
                }

    async def place_trade(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: int,
        api_token: str,
        duration_unit: str = "t",
        digit: Optional[int] = None,
    ) -> dict:
        """Full trade flow: authorize → proposal → buy → settle.

        Returns {"status": "success", "won": bool, "pnl": float, ...} with the
        REAL settled result, or {"status": "error", "step": ..., "error": ...}
        if any step failed — an error never masquerades as a win or a loss.
        """
        try:
            contract_fields = deriv_contract_params(contract_type, digit)
        except ValueError as exc:
            return {"status": "error", "step": "validate", "error": str(exc)}

        url = await self._url(api_token)
        async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
            if self._needs_authorize(url):
                auth_msg = await self._send_recv(ws, {"authorize": api_token})
                if "error" in auth_msg:
                    return {"status": "error", "step": "authorize", "error": auth_msg["error"].get("message")}
                account = auth_msg.get("authorize") or {}
            else:
                account = {}

            proposal_req = {
                "proposal": 1,
                "amount": float(amount),
                "basis": "stake",
                "currency": (account or {}).get("currency") or "USD",
                "duration": int(duration),
                "duration_unit": duration_unit,
                **contract_fields,
            }
            # OTP endpoint requires the symbol as `underlying_symbol`;
            # the legacy authorize endpoint requires plain `symbol`.
            if self._needs_authorize(url):
                proposal_req["symbol"] = symbol
            else:
                proposal_req["underlying_symbol"] = symbol
            prop_msg = await self._send_recv(ws, proposal_req)
            if "error" in prop_msg:
                return {"status": "error", "step": "proposal", "error": prop_msg["error"].get("message")}
            proposal = prop_msg.get("proposal", {})
            proposal_id = proposal.get("id")
            price = proposal.get("ask_price", amount)

            buy_msg = await self._send_recv(ws, {"buy": proposal_id, "price": price})
            if "error" in buy_msg:
                return {"status": "error", "step": "buy", "error": buy_msg["error"].get("message")}
            buy = buy_msg.get("buy", {})
            contract_id = str(buy.get("contract_id", ""))

            try:
                settled = await self._await_settlement(ws, contract_id)
            except (TimeoutError, RuntimeError, asyncio.TimeoutError) as exc:
                # The money IS in a live contract; we just lost track of it.
                # Surface this loudly — never guess the outcome.
                return {
                    "status": "error",
                    "step": "settle",
                    "error": str(exc),
                    "contract_id": contract_id,
                    "transaction_id": str(buy.get("transaction_id", "")),
                    "buy_price": buy.get("buy_price", price),
                }

            return {
                "status": "success",
                "contract_id": contract_id,
                "transaction_id": str(buy.get("transaction_id", "")),
                "buy_price": buy.get("buy_price", price),
                "payout": proposal.get("payout"),
                "won": settled["won"],
                "pnl": settled["pnl"],
                "sell_price": settled.get("sell_price"),
            }

deriv_trader = DerivTrader()
