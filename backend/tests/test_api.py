"""API surface tests — assert routes exist and respond."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.queue import tick_queue
from app.services.demo_generator import DemoGenerator


@pytest.fixture(scope="module")
def client():
    demo = DemoGenerator(interval_ms=1)

    async def fill():
        n = 0
        async for tick in demo.stream("R_100"):
            tick_queue.push(tick)
            n += 1
            if n >= 300:
                break

    asyncio.run(fill())
    with TestClient(app) as c:
        yield c


def test_root_and_health(client):
    r = client.get("/")
    assert r.status_code == 200
    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["status"] == "healthy"


def test_market_data(client):
    r = client.get("/ticks/R_100?limit=50")
    assert r.status_code == 200
    assert r.json()["count"] > 0
    s = client.get("/stats/R_100")
    assert s.status_code == 200
    t = client.get("/tick-timer/R_100")
    assert t.status_code == 200


def test_intelligence_routes(client):
    for path in [
        "/intelligence/R_100",
        "/most-likely/R_100",
        "/market-master/R_100",
        "/scan-all",
        "/quick-signals",
        "/signal/R_100",
        "/regime/R_100",
        "/streak/R_100",
        "/anomalies/R_100",
        "/quality/R_100",
        "/technical/R_100",
    ]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_digit_routes(client):
    for path in [
        "/digits/R_100",
        "/digits/R_100/psychology",
        "/digits/R_100/contract",
        "/digits/R_100/gaps",
        "/digits/R_100/predictor",
        "/digits/R_100/multi-window",
        "/digits/R_100/ldp",
    ]:
        r = client.get(path)
        assert r.status_code == 200, path


def test_strategy_lifecycle(client):
    body = {"config": {"name": "T", "strategy_type": "DIGIT_MATCH"}}
    created = client.post("/strategies/create", json=body)
    assert created.status_code == 200
    sid = created.json()["id"]
    assert client.post(f"/strategies/{sid}/start").status_code == 200
    assert client.post(f"/strategies/{sid}/evaluate", json={"analysis": 5.0, "signal": "STRONG_DATA_SUPPORT", "quality": 90}).status_code == 200
    assert client.post(f"/strategies/{sid}/record", json={"won": True, "payout": 1.9}).status_code == 200
    assert client.post(f"/strategies/{sid}/stop").status_code == 200


def test_backtest_and_replay(client):
    r = client.post("/backtest", json={"symbol": "R_100", "count": 100})
    assert r.status_code == 200
    assert "win_rate" in r.json()


def test_copy_social_leaderboards_rooms(client):
    leader = client.post("/copy/leaders", json={"name": "Alpha"})
    assert leader.status_code == 200
    leaders = client.get("/copy/leaders")
    assert leaders.status_code == 200

    post = client.post("/social/posts", json={"content": "hello"})
    assert post.status_code == 200
    posts = client.get("/social/posts")
    assert posts.status_code == 200

    lb = client.get("/leaderboards")
    assert lb.status_code == 200

    room = client.post("/rooms", json={"name": "room"})
    assert room.status_code == 200


def test_portfolio_and_risk(client):
    asset = client.post("/portfolio/assets", json={"symbol": "BTC", "asset_class": "crypto", "quantity": 1, "entry_price": 100})
    assert asset.status_code == 200
    p = client.get("/portfolio")
    assert p.status_code == 200
    div = client.get("/portfolio/diversification")
    assert div.status_code == 200
    var = client.get("/risk/var")
    assert var.status_code == 200


def test_copilot(client):
    r = client.post("/ai-copilot/ask", json={"question": "Should I trade MATCHES on 6?"})
    assert r.status_code == 200
    assert "answer" in r.json()


def test_auto_trader_lifecycle(client):
    start = client.post("/auto-trader/start", json={"mode": "paper"})
    assert start.status_code == 200
    status = client.get("/auto-trader/status")
    assert status.status_code == 200
    stop = client.post("/auto-trader/stop")
    assert stop.status_code == 200


def test_club_endpoints(client):
    manager = client.get("/club/manager")
    assert manager.status_code == 200
    m = manager.json()
    assert m["morale"] and m["formation"] and m["briefing"]
    assert isinstance(m["directives"], list)

    news = client.get("/club/news")
    assert news.status_code == 200
    assert "headlines" in news.json()

    fans = client.get("/club/fans")
    assert fans.status_code == 200
    assert fans.json()["chant"] and fans.json()["crowd"]

    board = client.get("/club/board")
    assert board.status_code == 200
    assert board.json()["sponsors"] and board.json()["statement"]

    alerts = client.get("/club/alerts")
    assert alerts.status_code == 200
    assert "count" in alerts.json()

    squad = client.get("/club/squad")
    assert squad.status_code == 200
    s = squad.json()
    assert len(s["players"]) == 10
    assert all(40 <= p["rating"] <= 99 for p in s["players"])
    assert s["tier"] in ("WORLD CLASS", "ELITE", "PROFESSIONAL", "DEVELOPING")

    overview = client.get("/club")
    assert overview.status_code == 200
    body = overview.json()
    for k in ("manager", "board", "news", "fans", "alerts", "squad"):
        assert k in body
