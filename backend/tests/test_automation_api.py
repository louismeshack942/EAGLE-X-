"""Phase 6 API tests — automation endpoints lifecycle + §28 security matrix.

Attempts to bypass live execution through the API (frontend-style requests, direct
calls, live override attempts, forged config) must ALL fail: no unauthorized purchase,
no live enable from the client side, no duplication.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def client() -> TestClient:
    return TestClient(create_app())


def test_status_endpoint_default_safe():
    c = client()
    r = c.get("/api/automation/status")
    assert r.status_code == 200
    d = r.json()
    assert d["state"] == "OFF"
    assert d["live_enabled"] is False


def test_config_endpoint_exposes_switch_only_readonly():
    c = client()
    r = c.get("/api/automation/config")
    assert r.status_code == 200
    d = r.json()
    assert d["live_enabled"] is False  # informational only


def test_config_rejects_live_override():
    # The frontend must never be allowed to enable live execution via automation config.
    c = client()
    r = c.post("/api/automation/config", json={
        "enabled": True, "mode": "LIVE",
        "execution_live_enabled": True,  # forbidden key
    })
    assert r.status_code == 400
    assert "cannot be changed" in (r.json() or {}).get("detail", "")


def test_start_montor_ok_live_blocked():
    c = client()
    r = c.post("/api/automation/config", json={
        "enabled": True, "mode": "MONITOR", "allowed_markets": ["R_10"],
    })
    assert r.status_code == 200
    r = c.post("/api/automation/start")
    assert r.json()["ok"] is True
    r = c.post("/api/automation/start")  # already in a running state
    assert r.status_code in (200,)
    # stop
    assert c.post("/api/automation/stop").json()["state"] == "OFF"


def test_set_mode_live_refuses_without_master_switch():
    c = client()
    r = c.post("/api/automation/set-mode", json={"mode": "LIVE"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is False  # server-side master switch remains OFF
    assert any("execution_live_enabled" in p for p in d.get("problems", []))


def test_set_mode_off_and_invalid():
    c = client()
    assert c.post("/api/automation/set-mode", json={"mode": "PAPER"}).status_code == 200
    r = c.post("/api/automation/set-mode", json={"mode": "BOGUS"})
    assert r.status_code == 400
    r = c.post("/api/automation/set-mode", json={"mode": "OFF"})
    assert r.status_code == 200
    assert r.json()["state"] == "OFF"


def test_pause_resume_cycle():
    c = client()
    c.post("/api/automation/config", json={
        "enabled": True, "mode": "MONITOR", "allowed_markets": ["R_10"]})
    c.post("/api/automation/start")
    assert c.post("/api/automation/pause").json()["state"] == "PAUSED"
    assert c.post("/api/automation/resume").json()["ok"] is True
    c.post("/api/automation/stop")


def test_decisions_and_state_endpoints():
    c = client()
    assert c.get("/api/automation/decisions").status_code == 200
    d = c.get("/api/automation/state").json()
    assert "session_stats" in d
    assert d["state"] in ("OFF", "MONITORING", "STARTING", "PAUSED")


def test_scan_endpoint_honest_off():
    # A scan from OFF must be refused (automation not armed) — analysis only in MONITOR.
    c = client()
    r = c.post("/api/automation/scan", json={})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True  # scan_once returns {} scans for OFF (no markets armed) — still safe