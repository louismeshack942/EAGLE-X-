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
        assert "websockets/v3?app_id=" in url
        await VAULT.set("legacy123")
        other = await trader._url("another-token")
        assert "websockets/v3?app_id=" in other
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


def test_auto_play_duration_is_short():
    """The CF must hold possession briefly — regression for the 'decades'
    complaint. Auto-trader plays are capped at 5 seconds."""
    from app.services import market_master
    import inspect
    src = inspect.getsource(market_master)
    assert '"duration_seconds": 5' in src
    assert '"duration_seconds": 60' not in src


@pytest.mark.asyncio
async def test_trader_url_refuses_generic_when_pat_flow_active(monkeypatch):
    """PAT flow active but OTP mint failing: the geo-blocked generic endpoint
    must NOT be used — that's the silent slide into demo data. Raise and let
    the caller retry instead."""
    trader = DerivTrader()
    try:
        await VAULT.set("pat_abc123", account_id="CR999", app_id="4521")  # no stored ws_url
        async def no_mint(self, token):
            return None
        monkeypatch.setattr(DerivTrader, "_mint_fresh_otp", no_mint)
        with pytest.raises(ConnectionError):
            await trader._url("pat_abc123")
    finally:
        await VAULT.clear()


_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(_seconds):
    await _REAL_SLEEP(0)


@pytest.mark.asyncio
async def test_stream_lifecycle_never_emits_demo_when_token_configured(monkeypatch):
    """With a token configured, a live-stream failure must produce an honest
    reconnect — never demo ticks."""
    from app.services import deriv_client

    async def fake_token():
        return "pat_abc123"
    monkeypatch.setattr(deriv_client, "resolve_token", fake_token)

    async def boom(self, symbols):
        raise ConnectionError("no live")
        yield  # pragma: no cover - makes this an async generator
    monkeypatch.setattr(deriv_client.DerivClient, "stream", boom)
    monkeypatch.setattr(deriv_client.asyncio, "sleep", _fast_sleep)

    ticks, demo_built = [], []

    def demo_factory():
        demo_built.append(True)
        raise AssertionError("demo generator must never be built when a token is configured")

    task = asyncio.create_task(deriv_client.stream_lifecycle(["R_100"], ticks.append, demo_factory))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert ticks == []
    assert demo_built == []
    assert deriv_client.LIVE_STATE.mode == "live"
    assert "reconnecting to live feed" in (deriv_client.LIVE_STATE.last_error or "")


@pytest.mark.asyncio
async def test_stream_lifecycle_uses_demo_without_token(monkeypatch):
    """No token anywhere -> demo fallback remains (nothing else is possible)."""
    from app.services import deriv_client

    async def no_token():
        return ""
    monkeypatch.setattr(deriv_client, "resolve_token", no_token)

    async def boom(self, symbols):
        raise ConnectionError("no live")
        yield
    monkeypatch.setattr(deriv_client.DerivClient, "stream", boom)

    class FakeDemo:
        async def stream(self, symbol):
            raise TimeoutError("probe")  # ends _demo_with_live_retry fast
            yield

    ticks = []
    task = asyncio.create_task(deriv_client.stream_lifecycle(["R_100"], ticks.append, lambda: FakeDemo()))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert deriv_client.LIVE_STATE.mode == "demo"


@pytest.mark.asyncio
async def test_bootstrap_env_token_connects_pat_at_boot(monkeypatch):
    """The permanent fix: DERIV_API_TOKEN in the environment fills the vault
    at boot — restarts no longer boot tokenless into demo data."""
    from app import main as main_module
    await VAULT.clear()
    monkeypatch.setattr(main_module.settings, "deriv_api_token", "pat_envtoken")
    monkeypatch.setattr(main_module.settings, "deriv_pat_app_id", "4521")

    async def fake_validate(token, app_id):
        assert token == "pat_envtoken" and app_id == "4521"
        return {
            "loginid": "DOT94300575", "currency": "USD", "balance": "100.0",
            "account_id": "DOT94300575",
            "ws_url": "wss://api.derivws.com/trading/v1/options/ws/demo?otp=boot",
            "app_id": "4521", "accounts": [],
        }
    monkeypatch.setattr("app.api.auth._pat_validate", fake_validate)
    try:
        await main_module._bootstrap_env_token()
        assert await VAULT.get() == "pat_envtoken"
        assert await VAULT.get_account_id() == "DOT94300575"
        assert await VAULT.get_ws_url() is not None
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_bootstrap_env_token_noop_without_env(monkeypatch):
    from app import main as main_module
    await VAULT.clear()
    monkeypatch.setattr(main_module.settings, "deriv_api_token", "")
    await main_module._bootstrap_env_token()
    assert await VAULT.get() is None


@pytest.mark.asyncio
async def test_trader_url_resolves_env_token_when_none_passed(monkeypatch):
    """A PAT token from the env must take the OTP path even when _url is
    called with no token at all — never the legacy generic endpoint."""
    trader = DerivTrader()
    try:
        await VAULT.set(
            "pat_envtok",
            account_id="CR999",
            ws_url="wss://api.derivws.com/trading/v1/options/ws/real?otp=zzz",
            app_id="4521",
        )
        monkeypatch.setattr(trader.settings, "deriv_api_token", "pat_envtok")
        async def no_mint(self, token):
            return None
        monkeypatch.setattr(DerivTrader, "_mint_fresh_otp", no_mint)
        url = await trader._url(None)
        assert url == "wss://api.derivws.com/trading/v1/options/ws/real?otp=zzz"
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_trader_url_revalidates_pat_when_vault_cleared(monkeypatch):
    """If the vault lost the PAT fields (ephemeral FS), _url re-validates
    via the REST flow instead of degrading to the geo-blocked endpoint."""
    trader = DerivTrader()
    calls = {}
    try:
        await VAULT.clear()
        async def fake_validate(token, app_id):
            calls["token"] = token
            return {
                "loginid": "CR999", "currency": "USD", "account_id": "CR999",
                "ws_url": "wss://api.derivws.com/trading/v1/options/ws/real?otp=rv",
                "app_id": "4521", "accounts": [],
            }
        monkeypatch.setattr("app.api.auth._pat_validate", fake_validate)
        async def no_mint(self, token):
            return None
        monkeypatch.setattr(DerivTrader, "_mint_fresh_otp", no_mint)
        url = await trader._url("pat_revive")
        assert url.endswith("otp=rv")
        assert calls["token"] == "pat_revive"
        assert await VAULT.get() == "pat_revive"
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_place_trade_never_raises(monkeypatch):
    """place_trade returns a structured error dict even when connecting
    fails outright — /trade must answer JSON, never a 500."""
    trader = DerivTrader()
    try:
        await VAULT.clear()
        async def boom(self, token=None):
            raise ConnectionError("OTP mint failed")
        monkeypatch.setattr(DerivTrader, "_url", boom)
        result = await trader.place_trade(
            symbol="R_100", contract_type="DIGITDIFF", amount=1.0,
            duration=5, api_token="pat_x", digit=0,
        )
        assert result["status"] == "error"
        assert result["step"] == "connect"
        assert "OTP mint failed" in result["error"]
        result = await trader.place_trade(
            symbol="R_100", contract_type="DIGITDIFF", amount=1.0,
            duration=5, api_token="", digit=0,
        )
        assert result["status"] == "error"
        assert result["step"] == "connect"
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_vault_preserves_pat_fields_on_same_token_reset():
    """The stream's authorize() re-sets the vault with only loginid/currency.
    That must NOT wipe the PAT fields or the balance — losing account_id
    mid-session broke OTP minting and refused live starts."""
    try:
        await VAULT.set(
            "pat_keep",
            loginid="DOT94300575",
            currency="USD",
            account_id="DOT94300575",
            ws_url="wss://api.derivws.com/trading/v1/options/ws/demo?otp=aa",
            app_id="4521",
        )
        await VAULT.set_balance(12143.02)
        await VAULT.set("pat_keep", loginid="DOT94300575", currency="USD")
        assert await VAULT.get_account_id() == "DOT94300575"
        assert await VAULT.get_app_id() == "4521"
        assert await VAULT.get_ws_url() is not None
        st = await VAULT.status()
        assert st["balance"] == 12143.02
        assert st["loginid"] == "DOT94300575"
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_vault_new_token_still_resets_everything():
    try:
        await VAULT.set(
            "pat_old", account_id="DOT1", ws_url="wss://x?otp=1", app_id="1",
        )
        await VAULT.set_balance(100.0)
        await VAULT.set("pat_new", loginid="DOT2")
        st = await VAULT.status()
        assert st["account_id"] is None
        assert st["balance"] is None
        assert await VAULT.get_ws_url() is None
        assert await VAULT.get_app_id() is None
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_vault_balance_survives_restart(tmp_path):
    """set_balance must persist — an ephemeral-FS restart keeps the last
    known balance instead of going blind."""
    path = tmp_path / "vault.json"
    v1 = TokenVault(path=path)
    await v1.set("pat_x", loginid="DOT94300575", account_id="DOT94300575")
    await v1.set_balance(12143.02)
    v2 = TokenVault(path=path)
    st = await v2.status()
    assert st["balance"] == 12143.02


@pytest.mark.asyncio
async def test_get_balance_rest_first_for_pat(monkeypatch):
    """PAT balance reads go through the REST accounts endpoint — no OTP
    mint, no websocket. The websocket path must not even be attempted."""
    trader = DerivTrader()
    try:
        await VAULT.set("pat_bal", account_id="DOT94300575", app_id="4521")
        async def fake_rest(self, token):
            return 12143.02
        monkeypatch.setattr(DerivTrader, "_rest_balance", fake_rest)
        async def ws_would_fail(self, token=None):
            raise AssertionError("websocket path must not be attempted")
        monkeypatch.setattr(DerivTrader, "_url", ws_would_fail)
        bal = await trader.get_balance("pat_bal")
        assert bal == 12143.02
        assert (await VAULT.status())["balance"] == 12143.02
    finally:
        await VAULT.clear()


@pytest.mark.asyncio
async def test_main_loop_never_dies_on_error(monkeypatch):
    """An exploding scan session must not kill the CF: the wrapper logs,
    regroups and re-enters. The loop only ends when running goes False."""
    from app.services.auto_trader import AutoTrader
    at = AutoTrader()
    at.running = True
    calls = {"n": 0}

    async def exploding_session(self, api_token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        self.running = False  # second entry exits cleanly

    monkeypatch.setattr(AutoTrader, "_scan_session", exploding_session)
    await asyncio.wait_for(at._main_loop(None), timeout=10)
    assert calls["n"] == 2  # recovered once, then exited on stop
    assert any("never stops" in line for line in at.log)


@pytest.mark.asyncio
async def test_autostart_cf_retries_until_started(monkeypatch):
    """CF_AUTOSTART=live must put the CF back on the pitch after a restart,
    retrying until the balance read succeeds."""
    from app import main as main_module
    from app.services.auto_trader import auto_trader
    monkeypatch.setattr(main_module.settings, "cf_autostart", "live")
    calls = {"n": 0}

    async def fake_start(mode="paper", api_token=None):
        calls["n"] += 1
        if calls["n"] < 2:
            return {"status": "error", "message": "no balance yet"}
        auto_trader.running = True
        return {"status": "started"}

    monkeypatch.setattr(auto_trader, "start", fake_start)
    auto_trader.running = False
    try:
        await asyncio.wait_for(main_module._autostart_cf(), timeout=30)
        assert calls["n"] == 2
        assert auto_trader.running
    finally:
        auto_trader.running = False


@pytest.mark.asyncio
async def test_autostart_cf_disabled_by_default(monkeypatch):
    from app import main as main_module
    from app.services.auto_trader import auto_trader
    monkeypatch.setattr(main_module.settings, "cf_autostart", "")
    called = {"n": 0}

    async def fake_start(mode="paper", api_token=None):
        called["n"] += 1
        return {"status": "started"}

    monkeypatch.setattr(auto_trader, "start", fake_start)
    await asyncio.wait_for(main_module._autostart_cf(), timeout=5)
    assert called["n"] == 0
