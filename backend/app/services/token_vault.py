"""Runtime Deriv token vault.

The user's personal Deriv API token arrives either via the OAuth callback or
via the secure POST /auth/token endpoint. It is held in memory and persisted
to a 0600-permission JSON file so a restart does not disconnect the account.
It is never logged and never returned by any endpoint — only masked metadata
(loginid, currency, balance) leaves the vault.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Optional

_VAULT_PATH = Path(os.environ.get("EAGLEX_VAULT_PATH", "data/vault.json"))


class TokenVault:
    def __init__(self, path: Path = _VAULT_PATH) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._token: Optional[str] = None
        self._loginid: Optional[str] = None
        self._currency: Optional[str] = None
        self._balance: Optional[float] = None
        self._account_id: Optional[str] = None
        self._ws_url: Optional[str] = None
        self._app_id: Optional[str] = None
        self._accounts: list = []
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                self._token = data.get("token")
                self._loginid = data.get("loginid")
                self._currency = data.get("currency")
                self._account_id = data.get("account_id")
                self._ws_url = data.get("ws_url")
                self._app_id = data.get("app_id")
                self._accounts = data.get("accounts") or []
        except Exception:
            self._token = None

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "token": self._token,
            "loginid": self._loginid,
            "currency": self._currency,
            "account_id": self._account_id,
            "ws_url": self._ws_url,
            "app_id": self._app_id,
            "accounts": self._accounts,
        }))
        os.chmod(tmp, 0o600)
        tmp.replace(self._path)

    async def set(self, token: str, loginid: Optional[str] = None, currency: Optional[str] = None,
                  account_id: Optional[str] = None, ws_url: Optional[str] = None,
                  app_id: Optional[str] = None, accounts: Optional[list] = None) -> None:
        async with self._lock:
            self._token = token.strip()
            self._loginid = loginid
            self._currency = currency
            self._balance = None
            self._account_id = account_id
            self._ws_url = ws_url
            self._app_id = app_id
            if accounts is not None:
                self._accounts = accounts
            self._persist()

    async def clear(self) -> None:
        async with self._lock:
            self._token = None
            self._loginid = None
            self._currency = None
            self._balance = None
            self._account_id = None
            self._ws_url = None
            self._app_id = None
            self._accounts = []
            self._persist()

    async def get_accounts(self) -> list:
        async with self._lock:
            return list(self._accounts)

    async def switch_account(self, account_id: str, loginid: Optional[str] = None,
                             currency: Optional[str] = None) -> None:
        """Point the connection at a different account of the same token.
        Clears the cached OTP URL — the trader mints a fresh one per
        connection against the new account."""
        async with self._lock:
            self._account_id = account_id
            self._loginid = loginid or account_id
            self._currency = currency
            self._balance = None
            self._ws_url = None
            self._persist()

    async def get(self) -> Optional[str]:
        async with self._lock:
            return self._token

    async def get_ws_url(self) -> Optional[str]:
        async with self._lock:
            return self._ws_url

    async def get_app_id(self) -> Optional[str]:
        async with self._lock:
            return self._app_id

    async def get_account_id(self) -> Optional[str]:
        async with self._lock:
            return self._account_id

    async def set_balance(self, balance: float) -> None:
        async with self._lock:
            self._balance = balance

    async def status(self) -> dict:
        async with self._lock:
            return {
                "connected": bool(self._token),
                "loginid": self._loginid,
                "currency": self._currency,
                "balance": self._balance,
                "account_id": self._account_id,
            }


VAULT = TokenVault()
