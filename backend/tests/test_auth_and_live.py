"""Tests for the token vault, auth endpoints, and live-mode client logic."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.services.token_vault import TokenVault, VAULT
from app.services.deriv_client import DerivClient, GeoRestrictedError, LiveState
from app.services.deriv_trader import DerivTrader
from app.models.tick import Tick


@pytest.mark.asyncio
async def test_vault_set_get_clear(tmp_path):
    vault = TokenVault(path=tmp_path / "vault.json")
    assert await vault.get() is None
    st = await vault.status()
    assert st["connected"] is False

    await vault.set("tok123", loginid="CR999", currency="USD")
    assert await vault.get() == "tok123"
    st = await vault.status()
    assert st["connected"] is True
    assert st["loginid"] == "CR999"
    assert st["currency"] == "USD"
    # token never appears in status
    assert "tok123" not in json.dumps(st)

    # persists across instances
    vault2 = TokenVault(path=tmp_path / "vault.json")
    assert await vault2.get() == "tok123"

    # file is not world-readable
    mode = oct((tmp_path / "vault.json").stat().st_mode & 0o777)
    assert mode == "0o600"

    await vault2.clear()
    assert await vault2.get() is None


@pytest.mark.asyncio
async def test_vault_stores_pat_fields(tmp_path):
    """Vault keeps token + app_id + account_id + ws_url for PAT tokens."""
    vault = TokenVault(path=tmp_path / "vault.json")
    await vault.set(
        "pat_abc123",
        loginid="CR999",
        currency="USD",
        account_id="CR999",
        ws_url="wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz",
        app_id="4521",
    )
    assert await vault.get() == "pat_abc123"
    assert await vault.get_ws_url() == "wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz"
    assert await vault.get_app_id() == "4521"
    assert await vault.get_account_id() == "CR999"
    st = await vault.status()
    assert st["account_id"] == "CR999"
    assert "pat_abc123" not in json.dumps(st)  # token never exposed

    vault2 = TokenVault(path=tmp_path / "vault.json")
    assert await vault2.get_ws_url() == "wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz"

    await vault2.clear()
    assert await vault2.get() is None
    assert await vault2.get_ws_url() is None
    assert await vault2.get_app_id() is None


@pytest.mark.asyncio
async def test_trader_url_prefers_pat_and_falls_back_legacy():
    """Trader picks the stored OTP URL for the vault token; otherwise legacy."""
    trader = DerivTrader()
    try:
        await VAULT.set(
            "pat_abc123",
            account_id="CR999",
            ws_url="wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz",
            app_id="4521",
        )
        url = await trader._url("pat_abc123")
        assert url == "wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz"
        await VAULT.set("legacy123")
        url = await trader._url("legacy123")
        assert "websockets/v3/websocket" in url
        await VAULT.set("legacy123")
        other = await trader._url("another-token")
        assert "websockets/v3/websocket" in other
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_client_builds_correct_ws_url():
    client = DerivClient()
    assert client.settings.deriv_ws_url.endswith("/websockets/v3")
    url = f"{client.settings.deriv_ws_url}?app_id={client.settings.deriv_app_id}&l=EN"
    assert "websockets/v3?app_id=" in url


@pytest.mark.asyncio
async def test_geo_restriction_raises_when_all_symbols_invalid():
    """Simulate a Deriv server that rejects every symbol (US client)."""
    client = DerivClient()

    # Queue: two InvalidSymbol errors (one per subscribed symbol)
    errors = asyncio.Queue()
    for s in ["R_10", "R_25"]:
        errors.put_nowait(json.dumps({
            "echo_req": {"ticks": s},
            "error": {"code": "InvalidSymbol", "message": "nope"},
        }))

    class FakeWS:
        async def send(self, data):
            pass

        async def recv(self):
            return await errors.get()

    async def _true():
        return True
    client.ws = FakeWS()
    client._connect = _true  # type: ignore
    client._detect_country = lambda: asyncio.sleep(0)  # type: ignore
    client.authorize = _true  # type: ignore

    with pytest.raises(GeoRestrictedError):
        async for _ in client.stream(["R_10", "R_25"]):
            pass


@pytest.mark.asyncio
async def test_client_streams_valid_tick():
    """A valid tick message is converted into a deriv_live Tick."""
    client = DerivClient()

    msgs = asyncio.Queue()
    msgs.put_nowait(json.dumps({
        "tick": {"symbol": "R_100", "quote": 1618.5, "epoch": 1700000000}
    }))

    class FakeWS:
        async def send(self, data):
            pass

        async def recv(self):
            return await msgs.get()

    async def _true():
        return True
    client.ws = FakeWS()
    client._connect = _true  # type: ignore
    client._detect_country = lambda: asyncio.sleep(0)  # type: ignore
    client.authorize = _true  # type: ignore

    gen = client.stream(["R_100"])
    tick = await gen.__anext__()
    assert isinstance(tick, Tick)
    assert tick.symbol == "R_100"
    assert tick.provider == "deriv_live"
    assert tick.timestamp.tzinfo == timezone.utc


def test_live_state_to_dict_labels():
    state = LiveState()
    d = state.to_dict()
    assert d["mode"] == "demo"
    assert d["data_label"] == "DEMO DATA"
    assert d["trading_enabled"] is False
    state.mode = "live"
    state.connected = True
    state.authorized = True
    d = state.to_dict()
    assert d["is_live"] is True
    assert d["trading_enabled"] is True
