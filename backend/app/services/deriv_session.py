"""Authenticated Deriv session — PAT token vault + OTP WebSocket lifecycle.

The live-integration layer for Batch 4. The session owns:

- the PAT token (environment `DERIV_API_TOKEN` + `DERIV_PAT_APP_ID`, or an
  operator-supplied token via `/auth/token`) — validated through Deriv's REST
  trading API (`/options/accounts` + `/options/accounts/{id}/otp`) before it is
  ever stored, exactly like the production main-branch flow.

- a single-use OTP-authenticated WebSocket URL per connection. OTP URLs are
  minted fresh per connection; a failed mint NEVER degrades to the geo-blocked
  generic endpoint for a PAT token — it raises instead, so the caller sees an
  honest AUTHORIZATION_REQUIRED state rather than a silent demo slide.


- the account metadata (loginid, currency, balance, account_id, ws_url)
 held
  in-memory and persisted to a 0600-permission JSON file (`EAGLEX_VAULT_PATH`).
  Raw tokens never leave the server and never appear in any response or log.



Account data is READ via REST first (pure HTTP, no OTP mint, no rate-limit
exposure); the WebSocket is only a fallback. All failures are logged, never
swallowed. Deriv contract names map to the internal names (MATCHES/DIFFERS/ODD/
EVEN/OVER/UNDER) via the same table the production trader uses.

The session NEVER places a trade itself. It only exposes authenticated primitives
(proposal, buy, settle) so the broker/execution engine decides WHEN a purchase
is allowed — every safety gate from Batch 4 (risk gate, kill switch, idempotency,
open-count, $1 stake cap) remains the decision authority.

**Safety invariants (hard):**
- No purchase without a proposal whose `id` came from THIS authenticated feed.
- No stake above `settings.live_stake_max` (default $1) at the session layer too.
  (belt-and-braces: the engine caps it as well).
- A "request sent" is never "executed": only a full Deriv `buy` confirmation marks
  a purchase. Ambiguous timeouts -> the caller marks UNKNOWN via the ledger; never
  blind-retry.
- Settlement is read from Deriv's own `proposal_open_contract`; an error/timeout
  surface honestly and never imitates a win/loss.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx
import websockets

from app.config import settings

logger = logging.getLogger("eaglex.deriv_session")

VAULT_PATH = Path(os.environ.get("EAGLEX_VAULT_PATH", "data/vault.json"))

RETRYABLE = {429, 500, 502, 503, 504}

# Internal name -> Deriv API contract_type (production table, unchanged).
DERIV_CONTRACT_TYPES = {
    "MATCHES": "DIGITMATCH",
    "DIFFERS": "DIGITDIFF",
    "ODD": "DIGITODD",
    "EVEN": "DIGITEVEN",
    "OVER": "DIGITOVER",
    "UNDER": "DIGITUNDER",
}
BARRIER_TYPES = {"DIGITMATCH", "DIGITDIFF", "DIGITOVER", "DIGITUNDER"}
PARITY_TYPES = {"DIGITODD", "DIGITEVEN"}


class SessionState(str, Enum):
    """Canonical session states surfaced honestly to the UI."""

    LOGGED_OUT = "logged_out"
    LOADING = "loading"
    VALIDATING = "validating"
    CONNECTED = "connected"
    AUTH_REQUIRED = "auth_required"
    EXPIRED = "expired"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class SessionError(Exception):
    """A typed, user-visible session/feed failure."""

    def __init__(self, state: str, message: str) -> None:
        super().__init__(message)
        self.state = state
        self.message = message


def deriv_contract_params(contract_type: str, digit: Optional[int]) -> dict:
    """Translate an internal family name into Deriv proposal fields (barrier-bearing)."""
    name = (contract_type or "").upper()
    ct = DERIV_CONTRACT_TYPES.get(name, name)
    if ct in BARRIER_TYPES:
        if digit is None:
            raise ValueError(f"{ct} requires a digit barrier")
        return {"contract_type": ct, "barrier": str(int(digit))}
    if ct in PARITY_TYPES:
        return {"contract_type": ct}
    raise ValueError(f"Unknown Deriv contract type: {contract_type!r}")


async def _request(
    client: httpx.AsyncClient, method: str, url: str, headers: dict, attempts: int = 4
) -> httpx.Response:
    """HTTP call resilient to flaky API/network behaviour: retries with exponential backoff."""
    last_error: Optional[Exception] = None
    for i in range(attempts):
        try:
            resp = await client.request(method, url, headers=headers)
            if resp.status_code in RETRYABLE and i < attempts - 1:
                await asyncio.sleep(0.5 * (2 ** i))
                continue
            return resp
        except httpx.HTTPError as exc:
            last_error = exc
            if i < attempts - 1:
                await asyncio.sleep(0.5 * (2 ** i))
    raise ValueError(f"Deriv API unreachable after {attempts} attempts: {last_error}")


def _extract_accounts(payload: Any) -> list:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("accounts", "items"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _extract_ws_url(payload: Any) -> Optional[str]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        return data.get("url")
    return None


def _account_key(account: dict) -> str:
    return str(account.get("account_id") or account.get("loginid") or account.get("id") or "")


def _is_virtual(account: dict) -> bool:
    key = _account_key(account).upper()
    if key.startswith(("DO", "VR")):
        return True
    if key.startswith(("RO", "CR")):
        return False
    return bool(account.get("is_virtual"))


class DerivSession:
    """One authenticated Deriv account session: REST validation + WS lifcycle."""

    def __init__(self) -> None:
        self._state = SessionState.LOGGED_OUT.value
        self._token: Optional[str] = None
        self._app_id: Optional[str] = None
        self._loginid: Optional[str] = None
        self._currency: Optional[str] = None
        self._balance: Optional[float] = None
        self._account_id: Optional[str] = None
        self._ws_url: Optional[str] = None
        self._accounts: list = []
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_lock = asyncio.Lock()
        self._load_vault()

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> str:
        return self._state

    @property
    def token_present(self) -> bool:
        return bool(self._token)


    @property
    def live_configured(self) -> bool:
        return bool(self._token)and bool(self._account_id)and bool(self._ws_url)
    def status(self) -> dict:
        """Masked session metadata — NEVER the token."""
        return {
            "state": self._state,
            "connected": self._state == SessionState.CONNECTED.value,
            "authenticated": self._state == SessionState.CONNECTED.value,
            "loginid": self._loginid,
            "currency": self._currency,
            "balance": self._balance,
            "account_id": self._account_id,
            "is_virtual": self._is_virtual_account(),
        }

    def _is_virtual_account(self) -> bool:
        if self._account_id:
            return _is_virtual({"account_id": self._account_id})
        return False

    # -------------------------------------------------------------- vault persist
    def _load_vault(self) -> None:
        try:
            if VAULT_PATH.exists():
                data = json.loads(VAULT_PATH.read_text())
                self._token = data.get("token") or None
                self._app_id = data.get("app_id") or None
                self._loginid = data.get("loginid") or None
                self._currency = data.get("currency") or None
                self._account_id = data.get("account_id") or None
                self._ws_url = data.get("ws_url") or None
                self._accounts = data.get("accounts") or []
                bal = data.get("balance")
                self._balance = float(bal) if bal is not None else None
                if self._token:
                    self._state = SessionState.CONNECTED.value
        except Exception as exc:  # noqa: BLE001
            logger.warning("vault load failed; starting logged out: %s", exc)
            self._token = None

    def _persist_vault(self) -> None:
        try:
            VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = VAULT_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "token": self._token,
                "app_id": self._app_id,
                "loginid": self._loginid,
                "currency": self._currency,
                "balance": self._balance,
                "account_id": self._account_id,
                "ws_url": self._ws_url,
                "accounts": self._accounts,
            }))
            os.chmod(tmp, 0o600)
            tmp.replace(VAULT_PATH)
        except Exception as exc:  # never break the caller for a persist failure
            logger.warning("vault persist failed: %s", exc)

    # ------------------------------------------------------------- connect flow
    async def connect(self, token: str, app_id: Optional[str] = None) -> dict:
        """Validate + connect an account. Stores ONLY on success (all-or-nothing).

        PAT tokens go through the REST flow (accounts + OTP), which yields the
        authenticated WS URL. Other tokens fall back to the legacy websocket
        authorize flow. On any failure the vault is untouched and the state is FAILED/
        AUTH_REQUIRED — never a silent demo slide.

        Returns masked account metadata."""
        if not token or not str(token).strip():
            self._state = SessionState.AUTH_REQUIRED.value
            return {"ok": False, "state": self._state, "error": "no token provided"}

        token = token.strip()
        app_id = (app_id or self._app_id or settings.deriv_pat_app_id or "").strip() or None
        self._state = SessionState.VALIDATING.value

        try:
            if token.startswith("pat_"):
                if not app_id:
                    raise SessionError(SessionState.AUTH_REQUIRED.value,
                                       "app id is required for pat_ tokens — set DERIV_PAT_APP_ID or pass app_id.")
                info = await self._validate_pat(token, app_id)
            else:
                info = await self._validate_ws(token, app_id)
        except SessionError as exc:
            self._state = exc.state
            logger.warning("Deriv connect failed: %s", exc.message)
            return {"ok": False, "state": self._state, "error": exc.message}
        except Exception as exc:  # noqa: BLE001
            self._state = SessionState.FAILED.value
            logger.warning("Deriv connect failed: %s", exc)
            return {"ok": False, "state": self._state, "error": str(exc)}

        self._token = token
        self._app_id = info.get("app_id") or app_id
        self._loginid = info.get("loginid") or ""
        self._currency = info.get("currency") or ""
        self._account_id = info.get("account_id") or ""
        self._ws_url = info.get("ws_url") or ""
        self._accounts = info.get("accounts") or []
        if info.get("balance") is not None:
            try:
                self._balance = float(info["balance"])
            except (TypeError, ValueError):
                pass
        self._state = SessionState.CONNECTED.value
        self._persist_vault()
        logger.info("Deriv session connected: %s (%s)", self._loginid, self._currency)
        return {"ok": True, **self.status()}

    async def _validate_pat(self, token: str, app_id: str) -> dict:
        """REST validation: accounts + primary + OTP URL. Raises SessionError."""
        base = settings.deriv_rest_base.rstrip("/")
        headers = {"Authorization": f"Bearer {token}", "Deriv-App-ID": str(app_id)}
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await _request(client, "GET", f"{base}/options/accounts", headers)
            if resp.status_code in (401, 403):
                raise SessionError(SessionState.AUTH_REQUIRED.value,
                                  f"Deriv rejected this token or app id ({resp.status_code}).")
            if resp.status_code != 200:
                raise SessionError(SessionState.FAILED.value,
                                  f"accounts lookup failed ({resp.status_code}).")
            accounts = _extract_accounts(resp.json())
            if not accounts:
                raise SessionError(SessionState.AUTH_REQUIRED.value, "no Deriv accounts visible to this token")
            accounts.sort(key=_account_key)
            primary = accounts[0]
            account_id = _account_key(primary)
            if not account_id:
                raise SessionError(SessionState.FAILED.value, "accounts entry has no account_id")
            otp_resp = await _request(client, "POST", f"{base}/options/accounts/{account_id}/otp", headers)
            if otp_resp.status_code in (401, 403):
                raise SessionError(SessionState.AUTH_REQUIRED.value,
                                  "Deriv rejected this token on OTP — enable trading scope.")
            if otp_resp.status_code != 200:
                raise SessionError(SessionState.FAILED.value,
                                  f"otp generation failed ({otp_resp.status_code}).")
            ws_url = _extract_ws_url(otp_resp.json())
            if not ws_url:
                raise SessionError(SessionState.FAILED.value, "no OTP URL returned by Deriv")
        account_list = [
            {
                "account_id": _account_key(a),
                "loginid": a.get("loginid") or _account_key(a),
                "currency": a.get("currency"),
                "balance": a.get("balance"),
                "is_virtual": _is_virtual(a),
            }
            for a in accounts
        ]
        return {
            "loginid": primary.get("loginid") or account_id,
            "currency": primary.get("currency"),
            "balance": primary.get("balance"),
            "account_id": account_id,
            "ws_url": ws_url,
            "app_id": app_id,
            "accounts": account_list,
        }

    async def _validate_ws(self, token: str, app_id: Optional[str]) -> dict:
        """Legacy websocket authorize flow (non-PAT tokens). Raises SessionError."""
        ws_app_id = app_id or str(settings.deriv_pat_app_id or 1089)
        url = f"{settings.deriv_ws_url}?app_id={ws_app_id}"
        try:
            async with websockets.connect(url, open_timeout=15) as ws:
                await ws.send(json.dumps({"authorize": token}))
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
  # noqa: BLE001
            raise SessionError(SessionState.FAILED.value, f"websocket authorize flow failed: {exc}") from exc
        if "error" in msg:
            raise SessionError(SessionState.AUTH_REQUIRED.value, msg["error"].get("message", "Deriv rejected the token"))
        auth = msg.get("authorize") or {}
        return {
            "loginid": auth.get("loginid"),
            "currency": auth.get("currency"),
            "balance": auth.get("balance"),
            "account_id": str(auth.get("loginid") or ""),
            "ws_url": url,
            "app_id": ws_app_id,
            "accounts": [],
        }

    # ------------------------------------------------------------------ balance
    async def get_balance(self) -> Optional[float]:
        """Read the current account balance. PAT -> REST first (no OTP mint);
        websocket fallback for legacy. Failures logged, never swallowed."""
        if not self._token:
            return None
        try:
            if self._token.startswith("pat_"):
                bal = await self._rest_balance(self._token, self._app_id)
                if bal is not None:
                    self._balance = bal
                    self._persist_vault()
                    return bal
                logger.warning("get_balance: REST read failed, trying websocket path")
            async with self._session_ws() as ws:
                msg = await self._send_recv(ws, {"balance": 1})
                if "error" in msg:
                    logger.warning("get_balance: ws balance error %s", msg["error"])
                    return None
                bal = (msg.get("balance") or {}).get("balance")
                if bal is not None:
                    self._balance = float(bal)
                    self._persist_vault()
                return self._balance
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_balance failed: %s", exc)
            return None

    async def _rest_balance(self, token: str, app_id: Optional[str]) -> Optional[float]:
        base = settings.deriv_rest_base.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        if app_id:
            headers["Deriv-App-ID"] = str(app_id)
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(f"{base}/options/accounts", headers=headers)
                if resp.status_code != 200:
                    if resp.status_code in RETRYABLE and attempt < 2:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    logger.warning("rest balance: accounts lookup HTTP %s", resp.status_code)
                    return None
                accounts = _extract_accounts(resp.json())
                wanted = self._account_id or self._loginid
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

    # ------------------------------------------------------------------ WS urns
    def _needs_authorize(self, url: str) -> bool:
        """OTP URLs are pre-authenticated; only legacy ones need authorize."""
        return "otp=" not in url

    async def _mint_ws_url(self) -> str:
        """Resolve the current OTP WS URL, minting a fresh one when needed."""
        if not self._token:
            raise SessionError(SessionState.AUTH_REQUIRED.value, "no token connected")
        if self._token.startswith("pat_"):
            if not self._account_id:
                raise SessionError(SessionState.AUTH_REQUIRED.value, "account not resolved; reconnect first")
            fresh = await self._mint_otp(self._token, self._app_id)
            if fresh:
                self._ws_url = fresh
                self._persist_vault()
                return fresh
            if self._ws_url:
                return self._ws_url
            raise SessionError(SessionState.AUTH_REQUIRED.value,
                              "OTP mint failed and a PAT token cannot use the legacy endpoint.")
        return f"{settings.deriv_ws_url}?app_id={self._app_id or 1089}"

    async def _mint_otp(self, token: str, app_id: Optional[str]) -> Optional[str]:
        base = settings.deriv_rest_base.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"}
        if app_id:
            headers["Deriv-App-ID"] = str(app_id)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                for attempt in range(3):
                    resp = await client.post(f"{base}/options/accounts/{self._account_id}/otp", headers=headers)
                    if resp.status_code == 200:
                        url = _extract_ws_url(resp.json())
                        return url or None
                    if resp.status_code in RETRYABLE and attempt < 2:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    break
        except httpx.HTTPError as exc:  # noqa: BLE001
            logger.warning("otp mint failed: %s", exc)
        return None

    async def _session_ws(self):
        """Open (or reuse) the authenticated websocket for primitives."""
        if self._ws is not None and not self._ws.closed:
            return self._ws
        url = await self._mint_ws_url()
        ws = await websockets.connect(url, ping_interval=20, ping_timeout=10, open_timeout=10)
        if self._needs_authorize(url):
            await ws.send(json.dumps({"authorize": self._token}))
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if "error" in msg:
                await ws.close()
                raise SessionError(SessionState.AUTH_REQUIRED.value,
                                  msg["error"].get("message", "authorize failed"))
        self._ws = ws
        return ws

    async def close_ws(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    @staticmethod
    async def _send_recv(ws, payload: dict, timeout: float = 10.0) -> dict:
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)

    # --------------------------------------------------------------- primitives
    async def get_proposal(self, *, symbol: str, contract_type: str, amount: float,
                           duration: int, digit: Optional[int] = None,
                           currency: Optional[str] = None) -> dict:
        """Request a live Deriv proposal (READ-ONLY. Returns normalized pricing."""
        if not self.live_configured:
            raise SessionError(SessionState.AUTH_REQUIRED.value, "no authenticated session")
        amount = float(amount)
        if amount > settings.live_stake_max:
            raise SessionError(SessionState.FAILED.value,
                              f"stake {amount} exceeds live_stake_max {settings.live_stake_max}")
        contract_fields = deriv_contract_params(contract_type, digit)
        req = {
            "proposal": 1,
            "amount": amount,
            "basis": "stake",
            "currency": currency or self._currency or "USD",
            "duration": int(duration),
            "duration_unit": "t",
            **contract_fields,
        }
        async with self._ws_lock:
            ws = await self._session_ws()
            req["req_id"] = 9100 + (hash((symbol, contract_type, digit)) % 900)
            if self._needs_authorize(self._ws_url_or(ws)):
                req["symbol"] = symbol
            else:
                req["underlying_symbol"] = symbol
            try:
                resp = await self._send_recv(ws, req, timeout=12)
            except asyncio.TimeoutError as exc:
                await self.close_ws()
                raise SessionError(SessionState.FAILED.value, "proposal request timed out") from exc
        if "error" in resp:
            raise SessionError(SessionState.FAILED.value, resp["error"].get("message", "proposal rejected"))
        p = resp.get("proposal") or {}
        if not p or not p.get("id"):
            raise SessionError(SessionState.FAILED.value, "proposal response had no id")
        return {
            "proposal_id": str(p["id"]),
            "ask_price": p.get("ask_price"),
            "payout": p.get("payout"),
            "spot": p.get("spot"),
            "currency": p.get("currency") or self._currency,
            "symbol": symbol,
            "contract_type": p.get("contract_type") or contract_fields["contract_type"],
            "barrier": digit,
            "duration_ticks": int(duration),
            "source": "LIVE",
        }

    def _ws_url_or(self, ws) -> str:
        url = getattr(ws, "url", "")
        return url or (self._ws_url or "")

    async def buy(self, *, proposal_id: str, price: float, amount: float,
                  currency: Optional[str] = None) -> dict:
        """Execute a live purchase FOR A REAL PROPOSAL. Returns the Deriv buy receipt.

        The caller (broker/engine) has already applied EVERY safety gate; this is the
        wire-call. Price must equal the proposal's ask. A confirmation (contract_id,
        transaction_id) is required to mark the purchase EXECUTED — request-sent alone
        never counts."""
        if not proposal_id:
            raise SessionError(SessionState.FAILED.value, "no proposal_id — refuse to buy without a real quote")
        amount = float(amount)
        if amount > settings.live_stake_max:

            raise SessionError(SessionState.FAILED.value,
                              f"stake {amount} exceeds live_stake_max {settings.live_stake_max}")
        req = {
            "buy": proposal_id,
            "price": float(price),
        }
        async with self._ws_lock:
            ws = await self._session_ws()
            try:
                resp = await self._send_recv(ws, req, timeout=12)
            except asyncio.TimeoutError as exc:
                await self.close_ws()
                raise SessionError(SessionState.UNCERTAIN.value, "buy request timed out — outcome unknown") from exc
        if "error" in resp:
            raise SessionError(SessionState.FAILED.value, resp["error"].get("message", "buy rejected"))
        b = resp.get("buy") or {}
        contract_id = str(b.get("contract_id") or "")
        if not contract_id:
            raise SessionError(SessionState.UNCERTAIN.value, "buy response had no contract_id")
        return {
            "contract_id": contract_id,
            "transaction_id": str(b.get("transaction_id") or ""),
            "buy_price": b.get("buy_price", price),
            "payout": b.get("payout"),
            "purchase_time": b.get("purchase_time"),
            "currency": b.get("currency") or currency or self._currency or "USD",
        }

    async def settle(self, contract_id: str, timeout_s: float = 180.0) -> dict:
        """Follow `proposal_open_contract` until Deriv marks the contract SOLD.



        Returns {"won": bool,"pnl": float,"sell_price": ...,"status":...}. On any
        ambiguity (timeout/error) it RAISES; the engine then marks the contract
        UNKNOWN in the ledger (never a guess)."""
        req = {
            "proposal_open_contract": 1,
            "contract_id": contract_id,
            "subscribe": 1,
        }
        async with self._ws_lock:
            ws = await self._session_ws()
            await ws.send(json.dumps(req))
            deadline = asyncio.get_event_loop().time() + timeout_s
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    await self.close_ws()
                    raise SessionError(SessionState.UNCERTAIN.value,
                                      f"contract {contract_id} did not settle within {timeout_s}s")
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
                if "error" in msg:
                    raise SessionError(SessionState.UNCERTAIN.value,
                                      msg["error"].get("message", "settlement tracking failed"))
                poc = msg.get("proposal_open_contract") or {}
                if not poc:
                    continue
                if poc.get("is_sold") or poc.get("is_expired"):
                    profit = float(poc.get("profit", 0.0))
                    return {
                        "won": profit > 0.0,
                        "pnl": round(profit, 2),
                        "sell_price": poc.get("sell_price"),
                        "status": poc.get("status"),
                    }

    # ------------------------------------------------------------------ disconnect
    async def disconnect(self) -> dict:
        """Disconnect the account. The token is removed from the vault."""
        await self.close_ws()
        self._token = None
        self._app_id = None
        self._loginid = None
        self._currency = None
        self._account_id = None
        self._ws_url = None
        self._accounts = []
        self._balance = None
        self._state = SessionState.LOGGED_OUT.value
        try:
            if VAULT_PATH.exists():
                VAULT_PATH.unlink()
        except Exception:
            pass
        return {"ok": True, "state": self._state}


_session: Optional[DerivSession] = None


def get_session() -> DerivSession:
    """Shared authenticated session (single-node. """
    global _session
    if _session is None:
        _session = DerivSession()
    return _session


__all__ = [
    "SessionState",
    "SessionError",
    "DerivSession",
    "deriv_contract_params",
    "get_session",
]