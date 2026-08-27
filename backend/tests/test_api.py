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


# ---- Phase 2 analysis API ------------------------------------------------
def test_analysis_empty_before_data(client):
    r = client.get("/api/analysis/R_05")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "R_05"
    assert body["source"] == ""
    assert body["multi_window"]["state"] == "INSUFFICIENT_DATA"


def test_analysis_invalid_window(client):
    r = client.get("/api/analysis/R_10?window=77")
    assert r.status_code == 400


def test_analysis_subresources_empty_safe(client):
    for path in ["/api/analysis/R_10/digits", "/api/analysis/R_10/gaps",
                 "/api/analysis/R_10/streaks", "/api/analysis/R_10/parity",
                 "/api/analysis/R_10/over-under", "/api/analysis/R_10/matches-differs",
                 "/api/analysis/R_10/quality", "/api/analysis/R_10/windows"]:
        r = client.get(path)
        assert r.status_code == 200


def test_analysis_realtime_harness_reflected(client):
    # Connect harness long enough to collect >=25 ticks, then check analysis is live.
    client.post("/api/connect", json={"symbol": "R_75", "mode": "harness"})
    import time
    # Harness emits ~1 tick/500ms; poll until we have meaningful data (bounded).
    snap = {"source": ""}
    for _ in range(40):
        r = client.get("/api/analysis/R_75")
        snap = r.json()
        if snap["source"] == "harness":
            break
        time.sleep(0.12)
    assert snap["source"] == "harness"
    # The 25-window should have accrued >= MIN window data.
    wins = snap.get("windows", {})
    # JSON serialization turns int keys into strings
    assert "25" in wins and "50" in wins and "100" in wins
    # once n>=25 the quality is DATA_READY (fresh) — STALE only if test is slow
    if wins["25"]["n"] >= 25:
        assert wins["25"]["data_quality"]["state"] in ("DATA_READY", "STALE")


# ---- Phase 3 API --------------------------------------------------------
def test_contracts_list(client):
    r = client.get("/api/contracts")
    assert r.status_code == 200
    fams = r.json()["families"]
    assert {f["family"] for f in fams} == {"MATCHES", "DIFFERS", "ODD", "EVEN", "OVER", "UNDER"}
    assert {f["contract_type"] for f in fams} == {
        "DIGITMATCH", "DIGITDIFF", "DIGITODD", "DIGITEVEN", "DIGITOVER", "DIGITUNDER"
    }


def test_contract_field_map(client):
    r = client.get("/api/contracts/R_10")
    assert r.status_code == 200
    board = r.json()["board"]
    assert len(board) == 6
    assert r.json()["duration_unit"] == "t"
    bad = client.get("/api/contracts/R_10?duration_ticks=20")
    assert bad.status_code == 400


def test_proposal_flow_indicator(client):
    r = client.get("/api/proposal-flow")
    assert r.status_code == 200
    assert r.json()["mode"] in ("LIVE", "HARNESS")


def test_quick_analysis_endpoint(client):
    r = client.get("/api/quick-analysis",
                   params={"symbol": "R_10", "family": "DIFFERS", "barrier": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["readonly_note"]
    assert "proposal_source" in body
    assert body["recommendation"]["state"] in (
        "QUALIFIED", "WATCH", "NO TRADE", "INSUFFICIENT DATA"
    )


def test_quick_analysis_invalid_family(client):
    r = client.get("/api/quick-analysis", params={"symbol": "R_10", "family": "NOPE"})
    assert r.status_code == 400


def test_scan_endpoint(client):
    r = client.get("/api/scan/R_10")
    assert r.status_code == 200
    body = r.json()
    assert body["readonly_note"]
    assert isinstance(body["recommendations"], list)
    assert len(body["recommendations"]) == 4 * 10 + 2  # 42 contracts
    for rec in body["recommendations"]:
        assert rec["state"] in ("QUALIFIED", "WATCH", "NO TRADE", "INSUFFICIENT DATA")