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
async def test_trader_url_prefers_pat_and_falls_back_legacy(monkeypatch):
    """Trader picks the stored OTP URL when minting fails; otherwise legacy."""
    trader = DerivTrader()
    try:
        await VAULT.set(
            "pat_abc123",
            account_id="CR999",
            ws_url="wss://api.derivws.com/trading/v1/options/ws/real?otp=xyz",
            app_id="4521",
        )
        async def no_mint(self, token):
            return None
        monkeypatch.setattr(DerivTrader, "_mint_fresh_otp", no_mint)
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
async def test_trader_url_prefers_fresh_otp(monkeypatch):
    """A freshly minted OTP URL wins over the stale stored one."""
    trader = DerivTrader()
    try:
        await VAULT.set(
            "pat_abc123",
            account_id="CR999",
            ws_url="wss://api.derivws.com/trading/v1/options/ws/real?otp=STALE",
            app_id="4521",
        )
        async def fresh_mint(self, token):
            return "wss://api.derivws.com/trading/v1/options/ws/real?otp=FRESH"
        monkeypatch.setattr(DerivTrader, "_mint_fresh_otp", fresh_mint)
        url = await trader._url("pat_abc123")
        assert "otp=FRESH" in url
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_mint_fresh_otp_uses_stored_account(monkeypatch):
    """OTP minting posts to the account endpoint with Bearer + app id headers."""
    trader = DerivTrader()
    calls = []

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": {"url": "wss://api.derivws.com/trading/v1/options/ws/real?otp=NEW"}}

    class FakeClient:
        def __init__(self, timeout=None):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, headers=None):
            calls.append((url, headers))
            return FakeResp()

    monkeypatch.setattr("app.services.deriv_trader.httpx.AsyncClient", FakeClient)
    try:
        await VAULT.set("pat_abc123", account_id="CR999", app_id="4521")
        url = await trader._mint_fresh_otp("pat_abc123")
        assert url == "wss://api.derivws.com/trading/v1/options/ws/real?otp=NEW"
        assert calls[0][0].endswith("/options/accounts/CR999/otp")
        assert calls[0][1]["Authorization"] == "Bearer pat_abc123"
        assert calls[0][1]["Deriv-App-ID"] == "4521"
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_mint_fresh_otp_retries_then_gives_up(monkeypatch):
    """Retryable statuses are retried; persistent failure returns None."""
    trader = DerivTrader()
    attempts = []

    class FakeResp:
        status_code = 503
        def json(self):
            return {}

    class FakeClient:
        def __init__(self, timeout=None):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, headers=None):
            attempts.append(url)
            return FakeResp()

    async def no_sleep(*args):
        return None

    monkeypatch.setattr("app.services.deriv_trader.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.services.deriv_trader.asyncio.sleep", no_sleep)
    try:
        await VAULT.set("pat_abc123", account_id="CR999")
        url = await trader._mint_fresh_otp("pat_abc123")
        assert url is None
        assert len(attempts) == 3
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


def test_oauth_login_default_id_shows_setup_not_dead_redirect():
    """With the retired shared id (1089) the button must NOT redirect to
    Deriv's dead authorize page — it shows setup instructions instead."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/auth/deriv/login")
        assert r.status_code == 200
        assert "developers.deriv.com" in r.text
        assert "auth.deriv.com" not in r.text
        assert "/auth/deriv/callback" in r.text


def test_oauth_login_registered_id_redirects_with_pkce(monkeypatch):
    """With a registered app id the login redirects to auth.deriv.com with
    client_id, redirect_uri, state and a PKCE challenge."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.config import get_settings
    monkeypatch.setenv("DERIV_APP_ID", "77777")
    get_settings.cache_clear()
    try:
        with TestClient(app) as c:
            r = c.get("/auth/deriv/login", follow_redirects=False)
            assert r.status_code in (302, 307)
            loc = r.headers["location"]
            assert loc.startswith("https://auth.deriv.com/oauth2/auth?")
            assert "client_id=77777" in loc
            assert "response_type=code" in loc
            assert "redirect_uri=" in loc and "auth%2Fderiv%2Fcallback" in loc
            assert "state=" in loc
            assert "code_challenge=" in loc
            assert "code_challenge_method=S256" in loc
    finally:
        get_settings.cache_clear()


def test_extract_accounts_all_shapes():
    """The exact crash the user hit: data as a bare list must parse."""
    from app.api.auth import _extract_accounts
    accts = [{"account_id": "CR1"}]
    assert _extract_accounts({"data": {"accounts": accts}}) == accts
    assert _extract_accounts({"data": accts}) == accts
    assert _extract_accounts(accts) == accts
    assert _extract_accounts({"data": {"items": accts}}) == accts
    assert _extract_accounts({"data": {}}) == []
    assert _extract_accounts({}) == []
    assert _extract_accounts("garbage") == []


def test_extract_ws_url_shapes():
    from app.api.auth import _extract_ws_url
    assert _extract_ws_url({"data": {"url": "wss://x?otp=1"}}) == "wss://x?otp=1"
    assert _extract_ws_url({"data": {}}) is None
    assert _extract_ws_url({"data": ["not", "a", "dict"]}) is None


@pytest.mark.asyncio
async def test_pat_validate_requires_app_id():
    from app.api.auth import _pat_validate
    with pytest.raises(ValueError, match="app id is required"):
        await _pat_validate("pat_x", None)


@pytest.mark.asyncio
async def test_switch_account_flow(tmp_path):
    """Switching moves the active account, clears the stale OTP URL, and
    persists across vault instances."""
    vault = TokenVault(path=tmp_path / "vault.json")
    await vault.set(
        "pat_abc123",
        loginid="CR999",
        currency="USD",
        account_id="CR999",
        ws_url="wss://x?otp=old",
        app_id="4521",
        accounts=[
            {"account_id": "CR999", "loginid": "CR999", "currency": "USD", "balance": 50.0, "is_virtual": False},
            {"account_id": "VRTC111", "loginid": "VRTC111", "currency": "USD", "balance": 10000.0, "is_virtual": True},
        ],
    )
    assert [a["account_id"] for a in await vault.get_accounts()] == ["CR999", "VRTC111"]
    await vault.switch_account("VRTC111", loginid="VRTC111", currency="USD")
    assert await vault.get_account_id() == "VRTC111"
    assert await vault.get_ws_url() is None  # stale OTP discarded
    st = await vault.status()
    assert st["loginid"] == "VRTC111"
    vault2 = TokenVault(path=tmp_path / "vault.json")
    assert await vault2.get_account_id() == "VRTC111"
    assert len(await vault2.get_accounts()) == 2


def test_switch_endpoint_validates_target():
    from fastapi.testclient import TestClient
    from app.main import app
    import asyncio as _asyncio

    async def setup():
        await VAULT.set(
            "pat_abc123", account_id="CR999", app_id="4521",
            accounts=[
                {"account_id": "CR999", "loginid": "CR999", "currency": "USD", "is_virtual": False},
                {"account_id": "VRTC111", "loginid": "VRTC111", "currency": "USD", "is_virtual": True},
            ],
        )
    _asyncio.run(setup())
    try:
        with TestClient(app) as c:
            r = c.get("/auth/accounts")
            assert r.status_code == 200
            body = r.json()
            assert body["connected"] is True
            assert body["active_account_id"] == "CR999"
            assert len(body["accounts"]) == 2
            assert "pat_abc123" not in str(body)  # token never exposed

            r = c.post("/auth/account/switch", json={"account_id": "VRTC111"})
            assert r.json()["switched"] is True
            assert r.json()["is_virtual"] is True

            r = c.post("/auth/account/switch", json={"account_id": "NOPE1"})
            assert r.json()["switched"] is False
            assert "not visible" in r.json()["error"]
    finally:
        _asyncio.run(VAULT.clear())


def test_oauth_app_id_ui_override_activates_button():
    """Saving an OAuth app id through the endpoint must flip the button from
    the setup page to the real Deriv redirect — no restart needed."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services.persistence import settings_store
    try:
        with TestClient(app) as c:
            r = c.get("/auth/oauth-app")
            assert r.json()["custom"] is False

            r = c.post("/auth/oauth-app", json={"app_id": "abc-123!"})
            assert r.json()["saved"] is False

            r = c.post("/auth/oauth-app", json={"app_id": "abc123XYZ"})
            assert r.json()["saved"] is True
            assert r.json()["custom"] is True

            r = c.get("/auth/deriv/login", follow_redirects=False)
            assert r.status_code in (302, 307)
            assert "client_id=abc123XYZ" in r.headers["location"]

            r = c.post("/auth/oauth-app", json={"app_id": ""})
            assert r.json()["custom"] is False
            r = c.get("/auth/deriv/login")
            assert r.status_code == 200  # back to setup page
    finally:
        settings_store.set("deriv_oauth_app_id", None)


def test_is_virtual_detection():
    """Options-platform ids (DOT*/ROT*) must be classified without an
    is_virtual field — DOT94300575-style demo, ROT92532523-style real."""
    from app.api.auth import _is_virtual
    assert _is_virtual({"account_id": "DOT94300575"}) is True
    assert _is_virtual({"account_id": "ROT92532523"}) is False
    assert _is_virtual({"loginid": "VRTC12345"}) is True
    assert _is_virtual({"account_id": "CR123"}) is False
    assert _is_virtual({"account_id": "X", "is_virtual": True}) is True
    assert _is_virtual({"account_id": "UNKNOWN9"}) is False
