"""Integration tests: health, markets, status, connect/harness, ticks, auth status."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_markets(client):
    r = client.get("/api/markets")
    assert r.status_code == 200
    data = r.json()["markets"]
    symbols = {m["symbol"] for m in data}
    assert "R_10" in symbols
    assert "R_100" in symbols


def test_status_does_not_fake_live(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "data_source" in body
    # When OAuth not configured, must not claim deriv_live.
    assert body["oauth_configured"] is False


def test_auth_status_unauthenticated(client):
    r = client.get("/auth/status")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_auth_login_not_configured(client):
    r = client.get("/auth/deriv/login")
    body = r.json()
    assert body["ok"] is False
    assert body["state"] == "NOT_CONFIGURED"


def test_connect_unknown_symbol(client):
    r = client.post("/api/connect", json={"symbol": "NOPE", "mode": "harness"})
    assert r.status_code == 404
    assert r.json()["state"] == "MARKET_UNAVAILABLE"


def test_connect_live_requires_auth(client):
    r = client.post("/api/connect", json={"symbol": "R_10", "mode": "live"})
    assert r.status_code == 503
    assert r.json()["state"] == "AUTHORIZATION_REQUIRED"


def test_connect_harness_and_ticks(client):
    r = client.post("/api/connect", json={"symbol": "R_25", "mode": "harness"})
    assert r.status_code == 200
    tr = client.get("/api/ticks/R_25?limit=5")
    assert tr.status_code == 200
    ticks = tr.json()["ticks"]
    # may be 0 ticks if the task hasn't emitted yet; if present they must be harness-tagged
    for t in ticks:
        assert t["provider"] == "harness"
        assert 0 <= t["digit"] <= 9