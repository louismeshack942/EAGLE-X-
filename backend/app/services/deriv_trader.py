"""Deriv trade execution — place real trades via Deriv's WebSocket API.

IMPORTANT: api_token is passed per-request and is NEVER stored server-side.

Internal contract names map to Deriv's API names (Deriv rejects anything
else), digit contracts carry a barrier, and a trade is only resolved when
Deriv reports the contract settled — a successful *purchase* is not a win.
"""
import asyncio
import json
import logging
from typing import Optional

import httpx
import websockets

from app.config import get_settings
from app.services.token_vault import VAULT

logger = logging.getLogger(__name__)

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
        """Pick the websocket endpoint. Resolves the token itself (argument →
        vault → env). A PAT token ALWAYS goes through the account OTP URL —
        the legacy endpoint rejects PATs at authorize ("The token is
        invalid") and is geo-blocked for this deployment anyway. Legacy
        tokens use the classic websockets/v3 endpoint."""
        token = (token or await VAULT.get() or self.settings.deriv_api_token or "").strip()
        if token.startswith("pat_"):
            if await VAULT.get() != token or not await VAULT.get_account_id():
                # Vault cleared (ephemeral FS) or never bootstrapped for this
                # token — re-validate via the REST flow so OTP minting works.
                await self._revalidate_pat(token)
            fresh = await self._mint_fresh_otp(token)
            if fresh:
                return fresh
            ws_url = await VAULT.get_ws_url()
            if ws_url:
                return ws_url
            raise ConnectionError(
                "OTP mint failed and a PAT token cannot use the legacy "
                "endpoint — retrying rather than degrading"
            )
        if token and await VAULT.get() == token:
            fresh = await self._mint_fresh_otp(token)
            if fresh:
                return fresh
            ws_url = await VAULT.get_ws_url()
            if ws_url:
                return ws_url
        # deriv_ws_url already points at the websockets/v3 path; appending
        # "/websocket" would 404 (Deriv redirects that to an HTML page).
        return f"{self.settings.deriv_ws_url.rstrip('/')}?app_id={self.settings.deriv_app_id}&l=EN"

    async def _rest_balance(self, token: str) -> Optional[float]:
        """Read the balance via the REST accounts endpoint — pure HTTP, no
        OTP mint, no websocket. The reliable path for PAT tokens: the OTP
        endpoint rate-limits under load, and a failed mint used to take the
        whole balance read down with it."""
        account_id = await VAULT.get_account_id()
        app_id = await VAULT.get_app_id() or self.settings.deriv_pat_app_id or None
        base = self.settings.deriv_rest_base.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        if app_id:
            headers["Deriv-App-ID"] = str(app_id)
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{base}/options/accounts", headers=headers)
                if resp.status_code != 200:
                    if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    logger.warning("rest balance: accounts lookup HTTP %s", resp.status_code)
                    return None
                accounts = resp.json().get("data") or []
                wanted = str(account_id or (await VAULT.status()).get("loginid") or "")
                if wanted:
                    for a in accounts:
                        ids = {str(a.get("account_id") or ""), str(a.get("loginid") or "")}
                        if wanted in ids:
                            bal = a.get("balance")
                            return float(bal) if bal is not None else None
                if accounts:
                    bal = accounts[0].get("balance")
                    return float(bal) if bal is not None else None
                return None
            except (httpx.HTTPError, ValueError) as exc:
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                logger.warning("rest balance failed: %s", exc)
        return None

    async def _revalidate_pat(self, token: str) -> None:
        """Refill the vault's PAT fields (account_id, OTP ws_url) from the
        REST API — the same validation the connect endpoint performs."""
        from app.api.auth import _pat_validate
        app_id = await VAULT.get_app_id() or self.settings.deriv_pat_app_id or None
        info = await _pat_validate(token, app_id)
        await VAULT.set(
            token,
            loginid=info.get("loginid"),
            currency=info.get("currency"),
            account_id=info.get("account_id"),
            ws_url=info.get("ws_url"),
            app_id=info.get("app_id") or app_id,
            accounts=info.get("accounts"),
        )
        if info.get("balance") is not None:
            try:
                await VAULT.set_balance(float(info["balance"]))
            except (TypeError, ValueError):
                pass

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
        """Authorize (or reuse the OTP session) and read the account balance.

        PAT tokens read via REST first — no OTP mint, no websocket, no rate
        limit exposure. The websocket path remains as fallback and for legacy
        tokens. Failures are logged, never swallowed."""
        try:
            token = (api_token or await VAULT.get() or self.settings.deriv_api_token or "").strip()
            if token.startswith("pat_"):
                bal = await self._rest_balance(token)
                if bal is not None:
                    await VAULT.set_balance(bal)
                    return bal
                logger.warning("get_balance: REST read failed, trying websocket path")
            url = await self._url(token)
            async with websockets.connect(url, ping_interval=20, open_timeout=10) as ws:
                if self._needs_authorize(url):
                    msg = await self._send_recv(ws, {"authorize": token})
                    if "error" in msg:
                        logger.warning("get_balance: authorize error %s", msg["error"])
                        return None
                    bal = (msg.get("authorize") or {}).get("balance")
                    return float(bal) if bal is not None else None
                bal_msg = await self._send_recv(ws, {"balance": 1})
                if "error" in bal_msg:
                    logger.warning("get_balance: ws balance error %s", bal_msg["error"])
                    return None
                bal = (bal_msg.get("balance") or {}).get("balance")
                return float(bal) if bal is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_balance failed: %s", exc)
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
        NEVER raises: callers (the /trade endpoint, the CF loop) must get a
        structured rejection, not a 500 or a dead task.
        """
        try:
            return await self._execute_trade(
                symbol=symbol, contract_type=contract_type, amount=amount,
                duration=duration, api_token=api_token,
                duration_unit=duration_unit, digit=digit,
            )
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "step": "execute", "error": str(exc)}

    async def _execute_trade(
        self,
        symbol: str,
        contract_type: str,
        amount: float,
        duration: int,
        api_token: str,
        duration_unit: str = "t",
        digit: Optional[int] = None,
    ) -> dict:
        try:
            contract_fields = deriv_contract_params(contract_type, digit)
        except ValueError as exc:
            return {"status": "error", "step": "validate", "error": str(exc)}

        api_token = (api_token or await VAULT.get() or self.settings.deriv_api_token or "").strip()
        if not api_token:
            return {"status": "error", "step": "connect", "error": "No Deriv token configured"}
        try:
            url = await self._url(api_token)
        except Exception as exc:  # noqa: BLE001 — a failed connect is a clean rejection, never a 500
            return {"status": "error", "step": "connect", "error": str(exc)}
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
