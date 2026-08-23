"""EAGLE-X backend application entrypoint."""
import asyncio
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import get_settings
from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services import timer as contract_timer
from app.services import community
from app.services import club as club_svc
from app.services import portfolio as portfolio_svc
from app.services import risk_analytics as risk_svc
from app.services.ai_copilot import ai_copilot
from app.services.analytics import analytics_engine
from app.services.analytics_advanced import digit_engine
from app.services.auto_trader import auto_trader
from app.services.demo_generator import DemoGenerator
from app.services.deriv_client import LIVE_STATE, stream_lifecycle
from app.services.deriv_trader import deriv_trader
from app.services.engines import (
    anomaly_engine,
    movement_engine,
    quality_engine,
    streak_engine,
    volatility_engine,
)
from app.services.intelligence import intelligence_engine
from app.services.market_master import market_master
from app.services.persistence import (
    alerts_engine,
    backtest_engine,
    journal_engine,
    replay_engine,
    settings_store,
)
from app.services.strategy_engine import StrategyConfig, strategy_engine
from app.services import scout as scout_svc
from app.services.risk_guard import risk_guard
from app.services.virtual_bank import virtual_bank
from app.services.telegram import telegram_notifier
from app.services.technical import technical_engine
from app.services.token_vault import VAULT
from app.api.auth import router as auth_router

logger = logging.getLogger(__name__)
logging.basicConfig(level=get_settings().log_level)

settings = get_settings()
_ingestion_task: Optional[asyncio.Task] = None
_account_snapshot: dict = {"connected": False, "loginid": None, "currency": None, "balance": None}


async def _refresh_account_snapshot() -> None:
    global _account_snapshot
    _account_snapshot = await VAULT.status()


def _on_tick(tick: Tick) -> None:
    tick_queue.push(tick)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _ingestion_task
    demo_factory = lambda: DemoGenerator(
        seed=settings.demo_seed,
        start_price=settings.demo_start_price,
        drift=settings.demo_drift,
        volatility=settings.demo_volatility,
        interval_ms=settings.demo_tick_interval_ms,
    )
    _ingestion_task = asyncio.create_task(
        stream_lifecycle(settings.active_symbols, _on_tick, demo_factory)
    )
    logger.info("ingestion started for %s", settings.active_symbols)
    yield
    if _ingestion_task:
        _ingestion_task.cancel()


app = FastAPI(
    title="EAGLE-X Backend",
    description="Trading intelligence platform for Deriv synthetic indices",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)


# ---------------- Request bodies ----------------
class AutoTraderStartBody(BaseModel):
    mode: Optional[str] = "paper"
    api_token: Optional[str] = None


class TradeBody(BaseModel):
    symbol: str
    direction: str = "CALL"
    amount: float = 1.0
    duration: int = 60
    api_token: Optional[str] = None
    duration_unit: str = "t"
    digit: Optional[int] = None


class BacktestBody(BaseModel):
    symbol: str = "R_100"
    count: int = 300
    rule: str = "OVERFED_MATCH"
    balance: float = 10.0
    stake: float = 1.0


class ReplayLoadBody(BaseModel):
    symbol: str = "R_100"
    count: int = 200
    name: str = "replay"


class ReplayControlBody(BaseModel):
    action: str
    speed: float = 1.0


class StrategyCreateBody(BaseModel):
    config: StrategyConfig


class EvaluateBody(BaseModel):
    analysis: float
    signal: str
    quality: float


class RecordBody(BaseModel):
    won: bool
    payout: float


class ContractStartBody(BaseModel):
    symbol: str
    contract_type: str
    stake: float
    duration_seconds: int = 60
    digit: Optional[int] = None


class ContractSettleBody(BaseModel):
    result: str
    pnl: float


# ---------------- Root / health / status ----------------
def _frontend_dir() -> Optional[Path]:
    if not settings.frontend_dir:
        return None
    p = Path(settings.frontend_dir)
    return p if (p / "index.html").is_file() else None


@app.get("/")
def root():
    fd = _frontend_dir()
    if fd is not None:
        return FileResponse(fd / "index.html")
    return {
        "service": "EAGLE-X Backend",
        "version": "1.0.0",
        "mode": LIVE_STATE.mode,
        "data_label": LIVE_STATE.to_dict()["data_label"],
        "disclaimer": "Statistical analysis tool — NOT a guaranteed-profit engine.",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "postgres": "available" if settings.database_available else "unavailable",
        "redis": "available" if settings.redis_available else "unavailable",
        "mode": LIVE_STATE.mode,
        "connected": LIVE_STATE.connected,
    }


@app.get("/status")
async def status():
    await _refresh_account_snapshot()
    state = LIVE_STATE.to_dict()
    active = []
    for sym in settings.active_symbols:
        n = tick_queue.count(sym)
        if n:
            active.append(sym)
    latest = None
    for sym in settings.active_symbols:
        t = tick_queue.latest(sym)
        if t:
            latest = t.to_dict()
            break
    return {
        **state,
        "last_tick": latest,
        "active_symbols": active,
        "counts": {sym: tick_queue.count(sym) for sym in settings.active_symbols},
        "deriv_account": _account_snapshot,
    }


# ---------------- Market data ----------------
@app.get("/ticks/{symbol}")
def ticks(symbol: str, limit: int = 50):
    items = tick_queue.recent(symbol, limit=min(limit, 2000))
    return {
        "symbol": symbol,
        "count": len(items),
        "provider": items[-1].provider if items else None,
        "is_live": bool(items and items[-1].provider == "deriv_live"),
        "ticks": [t.to_dict() for t in items],
    }


@app.get("/stats/{symbol}")
def stats(symbol: str, window: int = 100):
    return analytics_engine.get_stats(symbol, window=window)


@app.get("/tick-timer/{symbol}")
def tick_timer(symbol: str):
    return analytics_engine.time_to_next_tick(symbol)


# ---------------- Intelligence & analysis ----------------
@app.get("/intelligence/{symbol}")
def intelligence(symbol: str, window: int = 100):
    return intelligence_engine.analyze(symbol, window)


@app.get("/most-likely/{symbol}")
def most_likely(symbol: str, window: int = 100):
    return intelligence_engine.most_likely(symbol, window)


@app.get("/market-master/{symbol}")
def market_master_route(symbol: str, window: int = 100):
    return market_master.analyze(symbol, window)


@app.get("/scan-all")
def scan_all(window: int = 100):
    return intelligence_engine.scan_all(settings.active_symbols, window)


@app.get("/quick-signals")
def quick_signals(window: int = 100):
    scan = intelligence_engine.scan_all(settings.active_symbols, window)
    return {
        "window": window,
        "signals": [
            {"symbol": m["symbol"], "signal": m["signal"], "score": m["score"]}
            for m in scan["markets"]
        ],
    }


# ---------------- Digit analysis ----------------
@app.get("/digits/{symbol}")
def digits(symbol: str, window: int = 100):
    return digit_engine.get_digit_analysis(symbol, window)


@app.get("/digits/{symbol}/psychology")
def digits_psychology(symbol: str, window: int = 100):
    return digit_engine.get_psychology(symbol, window)


@app.get("/digits/{symbol}/contract")
def digits_contract(symbol: str, mode: Optional[str] = None, window: int = 100):
    return digit_engine.get_contract_analysis(symbol, mode=mode, window=window)


@app.get("/digits/{symbol}/gaps")
def digits_gaps(symbol: str, window: int = 100):
    return digit_engine.get_gap_analysis(symbol, window)


@app.get("/digits/{symbol}/predictor")
def digits_predictor(symbol: str, window: int = 100):
    return digit_engine.get_predictor(symbol, window)


@app.get("/digits/{symbol}/multi-window")
def digits_multi_window(symbol: str):
    return digit_engine.get_multi_window(symbol)


@app.get("/digits/{symbol}/ldp")
def digits_ldp(symbol: str, pattern_len: int = 2, window: int = 100):
    return digit_engine.get_ldp_patterns(symbol, pattern_len=pattern_len, window=window)


# ---------------- Engines ----------------
@app.get("/regime/{symbol}")
def regime(symbol: str, window: int = 100):
    return {
        "symbol": symbol,
        "volatility": volatility_engine.analyze(symbol, window),
        "movement": movement_engine.analyze(symbol, window),
    }


@app.get("/streak/{symbol}")
def streak(symbol: str, window: int = 100):
    return streak_engine.analyze(symbol, window)


@app.get("/anomalies/{symbol}")
def anomalies(symbol: str, window: int = 100):
    return anomaly_engine.detect(symbol, window)


@app.get("/quality/{symbol}")
def quality(symbol: str, window: int = 100):
    return quality_engine.score(symbol, window)


@app.get("/signal/{symbol}")
def signal(symbol: str, window: int = 100):
    intel = intelligence_engine.analyze(symbol, window)
    return {
        "symbol": symbol,
        "signal": intel["decision"],
        "data_quality": intel["data_quality"],
        "window": window,
    }


@app.get("/technical/{symbol}")
def technical(symbol: str, window: int = 100):
    return technical_engine.analyze(symbol, window)


# ---------------- Contract timers ----------------
@app.post("/contracts/start")
def contracts_start(body: ContractStartBody):
    c = contract_timer.start_contract(body.symbol, body.contract_type, body.stake, body.duration_seconds, body.digit)
    return {"status": "started", "contract": c}


@app.post("/contracts/{cid}/settle")
def contracts_settle(cid: str, body: ContractSettleBody):
    c = contract_timer.settle_contract(cid, body.result, body.pnl)
    if not c:
        raise HTTPException(404, "contract not found")
    return {"status": "settled", "contract": c}


@app.get("/contracts/{cid}")
def contracts_get(cid: str):
    c = contract_timer.get_contract(cid)
    if not c:
        raise HTTPException(404, "contract not found")
    return c


# ---------------- Auto Trader ----------------
@app.post("/auto-trader/start")
async def auto_trader_start(body: Optional[AutoTraderStartBody] = None):
    body = body or AutoTraderStartBody()
    return await auto_trader.start(mode=body.mode or "paper", api_token=body.api_token)


@app.post("/auto-trader/stop")
async def auto_trader_stop():
    return await auto_trader.stop()


@app.get("/auto-trader/status")
def auto_trader_status():
    return auto_trader.status()


# ---------------- Trade ----------------
@app.post("/trade")
async def trade(body: TradeBody):
    # Manual trades also go through the Guard: kill switch + tilt detector.
    guard_block = [v for v in risk_guard.check(auto_trader.daily_pnl) if v.startswith("KILL_SWITCH")]
    if guard_block:
        return {"status": "error", "error": guard_block[0]}
    risk_guard.record_trade(auto_trader.balance, manual=True)
    tilt = risk_guard.tilt_warning(
        "loss" if (auto_trader.last_trade or {}).get("result") == "LOSS" else None
    )
    token = body.api_token or await VAULT.get() or settings.deriv_api_token
    if not token:
        return {
            "status": "error",
            "error": "No Deriv account connected. Use POST /auth/token or the OAuth connect flow.",
        }
    result = await deriv_trader.place_trade(
        symbol=body.symbol,
        contract_type=body.direction,
        amount=body.amount,
        duration=body.duration,
        api_token=token,
        duration_unit=body.duration_unit,
        digit=body.digit,
    )
    if tilt:
        result["tilt_warning"] = tilt
    return result


# ---------------- Virtual Bank (the Treasurer) ----------------
class BankAmountBody(BaseModel):
    amount: float


class BankSplitBody(BaseModel):
    split_ratio: float


@app.get("/bank")
def bank_status():
    return virtual_bank.status()


@app.get("/bank/history")
def bank_history(limit: int = 20):
    return {"history": virtual_bank.recent_history(limit)}


@app.post("/bank/withdraw")
def bank_withdraw(body: BankAmountBody):
    return virtual_bank.withdraw(body.amount)


@app.post("/bank/deposit")
def bank_deposit(body: BankAmountBody):
    return virtual_bank.deposit(body.amount)


@app.post("/bank/split")
def bank_split(body: BankSplitBody):
    return virtual_bank.set_split(body.split_ratio)


# ---------------- Risk Guard (circuit breakers) ----------------
class GuardModeBody(BaseModel):
    mode: str


class ApprovalBody(BaseModel):
    approve: bool


@app.get("/guard")
def guard_status():
    return risk_guard.status()


@app.post("/guard/kill")
def guard_kill(reason: str = "manager pulled the kill switch"):
    result = risk_guard.kill(reason)
    telegram_notifier.send_risk_alert(f"KILL SWITCH: {reason}")
    return result


@app.post("/guard/release")
def guard_release():
    return risk_guard.release()


@app.post("/guard/mode")
def guard_mode(body: GuardModeBody):
    return risk_guard.set_mode(body.mode)


@app.get("/guard/limits")
def guard_limits_get():
    return risk_guard.status()


@app.post("/guard/limits")
def guard_limits_set(body: dict):
    return risk_guard.set_limits(
        daily_loss_limit=body.get("daily_loss_limit"),
        session_take_profit=body.get("session_take_profit"),
        max_trades_per_hour=body.get("max_trades_per_hour"),
        streak_halving=body.get("streak_halving"),
    )


@app.get("/guard/approvals")
def guard_approvals():
    return {"pending": [a for a in risk_guard.pending_approvals if a["status"] == "pending"]}


@app.post("/guard/approvals/{approval_id}")
def guard_approval_resolve(approval_id: str, body: ApprovalBody):
    resolved = risk_guard.resolve_approval(approval_id, body.approve)
    if not resolved:
        raise HTTPException(status_code=404, detail="approval not found or already resolved")
    return resolved


# ---------------- Table Scout ----------------
@app.get("/scout/tables")
def scout_tables(window: int = 100):
    return scout_svc.scan_tables(settings.active_symbols, window)


@app.get("/scout/heatmap")
def scout_heatmap(window: int = 100):
    return scout_svc.heatmap(settings.active_symbols, window)


@app.get("/scout/calibration")
def scout_calibration():
    return scout_svc.calibration()


@app.get("/scout/hot-hours")
def scout_hot_hours():
    return scout_svc.performance_by_hour()


@app.get("/scout/breakdown")
def scout_breakdown():
    return scout_svc.journal_breakdown()


# ---------------- Settings ----------------
@app.get("/settings")
def get_settings_route():
    return settings_store.get("user_settings", {})


@app.post("/settings")
def post_settings(body: dict):
    current = settings_store.get("user_settings", {})
    current.update(body)
    settings_store.set("user_settings", current)
    return {"status": "saved", "settings": current}


# ---------------- Journal ----------------
@app.get("/journal")
def journal(limit: int = 50):
    return {"entries": journal_engine.list_entries(limit), "dashboard": journal_engine.dashboard()}


@app.post("/journal")
def journal_add(entry: dict):
    return journal_engine.add_entry(
        market=entry.get("market", ""),
        contract=entry.get("contract", ""),
        digit=entry.get("digit"),
        stake=float(entry.get("stake", 0)),
        result=entry.get("result", "pending"),
        pnl=float(entry.get("pnl", 0)),
        data_quality=float(entry.get("data_quality", 0)),
        evidence_score=float(entry.get("evidence_score", 0)),
        mode=entry.get("mode", "paper"),
        analysis_snapshot=entry.get("analysis_snapshot"),
    )


# ---------------- Backtest ----------------
@app.post("/backtest")
def backtest(body: BacktestBody):
    ticks = tick_queue.recent(body.symbol, limit=min(body.count, 2000))
    tick_dicts = [t.to_dict() for t in ticks]
    return backtest_engine.run(tick_dicts, rule=body.rule, balance=body.balance, stake=body.stake)


# ---------------- Replay ----------------
@app.post("/replay/load")
def replay_load(body: ReplayLoadBody):
    ticks = tick_queue.recent(body.symbol, limit=min(body.count, 2000))
    session = replay_engine.load([t.to_dict() for t in ticks], name=body.name)
    return session


@app.post("/replay/{sid}/control")
def replay_control(sid: str, body: ReplayControlBody):
    session = replay_engine.control(sid, action=body.action, speed=body.speed)
    if not session:
        raise HTTPException(404, "replay session not found")
    return session


# ---------------- Alerts ----------------
@app.get("/alerts")
def alerts(limit: int = 50):
    return {"alerts": alerts_engine.list_alerts(limit)}


# ---------------- Strategies ----------------
@app.get("/strategies")
def strategies():
    return {"sessions": strategy_engine.list_sessions()}


@app.post("/strategies/create")
def strategies_create(body: StrategyCreateBody):
    session = strategy_engine.create_session(body.config)
    return session.to_dict()


@app.post("/strategies/{sid}/start")
def strategies_start(sid: str):
    result = strategy_engine.start(sid)
    if not result:
        raise HTTPException(404, "session not found")
    return result


@app.post("/strategies/{sid}/stop")
def strategies_stop(sid: str):
    result = strategy_engine.stop(sid)
    if not result:
        raise HTTPException(404, "session not found")
    return result


@app.post("/strategies/{sid}/evaluate")
def strategies_evaluate(sid: str, body: EvaluateBody):
    result = strategy_engine.evaluate(sid, body.analysis, body.signal, body.quality)
    if result is None:
        raise HTTPException(404, "session not found")
    return result


@app.post("/strategies/{sid}/record")
def strategies_record(sid: str, body: RecordBody):
    result = strategy_engine.record_result(sid, body.won, body.payout)
    if not result:
        raise HTTPException(404, "session not found")
    return result


# ---------------- AI Copilot ----------------
class CopilotBody(BaseModel):
    question: str
    symbol: Optional[str] = None


@app.post("/ai-copilot/ask")
def copilot_ask(body: CopilotBody):
    return ai_copilot.ask(body.question, body.symbol)


# ---------------- Copy Trading ----------------
class LeaderBody(BaseModel):
    name: str
    copy_ratio: float = 0.1
    bio: str = ""


class FollowBody(BaseModel):
    user_id: str = "default"
    leader_id: str
    allocation: float = 0.5


@app.get("/copy/leaders")
def copy_leaders():
    return {"leaders": community.list_leaders()}


@app.post("/copy/leaders")
def register_leader(body: LeaderBody):
    return community.register_leader(body.name, body.copy_ratio, body.bio)


@app.post("/copy/follow")
def copy_follow(body: FollowBody):
    result = community.follow_leader(body.user_id, body.leader_id, body.allocation)
    if not result:
        raise HTTPException(400, "leader not found")
    return result


@app.get("/copy/follows")
def copy_follows(user_id: Optional[str] = None):
    return {"follows": community.list_follows(user_id)}


# ---------------- Social Feed ----------------
class PostBody(BaseModel):
    content: str
    post_type: str = "post"
    user_id: str = "default"


class CommentBody(BaseModel):
    content: str
    user_id: str = "default"


@app.get("/social/posts")
def social_posts(limit: int = 50):
    return {"posts": community.list_posts(limit)}


@app.post("/social/posts")
def social_post_create(body: PostBody):
    return community.create_post(body.user_id, body.content, body.post_type)


@app.post("/social/posts/{post_id}/like")
def social_like(post_id: str):
    result = community.like_post(post_id)
    if not result:
        raise HTTPException(404, "post not found")
    return result


@app.post("/social/posts/{post_id}/comment")
def social_comment(post_id: str, body: CommentBody):
    result = community.comment_post(post_id, body.user_id, body.content)
    if not result:
        raise HTTPException(404, "post not found")
    return result


# ---------------- Leaderboards ----------------
@app.get("/leaderboards")
def leaderboards():
    leaders = community.list_leaders()
    for metric in ("pnl", "win_rate", "profit_factor"):
        for entry in leaders:
            entry.setdefault("pnl", entry.get("total_pnl", 0))
    return {"leaders": community.leaderboard(leaders, metric="pnl", limit=20)}


# ---------------- Trading Rooms ----------------
class RoomBody(BaseModel):
    name: str
    created_by: str = "default"
    is_private: bool = False
    password: str = ""


class JoinBody(BaseModel):
    user_id: str = "default"
    password: str = ""


class RoomMessageBody(BaseModel):
    message: str
    user_id: str = "default"


@app.get("/rooms")
def rooms():
    return {"rooms": community.list_rooms()}


@app.post("/rooms")
def rooms_create(body: RoomBody):
    return community.create_room(body.name, body.created_by, body.is_private, body.password)


@app.post("/rooms/{room_id}/join")
def rooms_join(room_id: str, body: JoinBody):
    room = community.join_room(room_id, body.user_id, body.password)
    if not room:
        raise HTTPException(403, "cannot join room")
    return room


@app.get("/rooms/{room_id}/messages")
def rooms_messages(room_id: str, limit: int = 50):
    messages = community.list_messages(room_id, limit)
    if messages is None:
        raise HTTPException(404, "room not found")
    return {"messages": messages}


@app.post("/rooms/{room_id}/messages")
def rooms_post_message(room_id: str, body: RoomMessageBody):
    msg = community.post_message(room_id, body.user_id, body.message)
    if not msg:
        raise HTTPException(404, "room not found")
    return msg


# ---------------- Club: team manager, board, news, fans, alerts ----------------
def _active_syms():
    return settings.active_symbols


@app.get("/club")
def club_overview(window: int = 100):
    return club_svc.overview(_active_syms(), window)


@app.get("/club/manager")
def club_manager(window: int = 100):
    return club_svc.manager_briefing(_active_syms(), window)


@app.get("/club/board")
def club_board():
    return club_svc.board_report()


@app.get("/club/news")
def club_news(window: int = 100):
    return club_svc.news_desk(_active_syms(), window)


@app.get("/club/fans")
def club_fans(window: int = 100):
    return club_svc.fan_standing(_active_syms(), window)


@app.get("/club/alerts")
def club_alerts(window: int = 100):
    return club_svc.market_alerts(_active_syms(), window)


@app.get("/club/squad")
def club_squad(window: int = 100):
    return club_svc.squad_ratings(_active_syms(), window)


# ---------------- Portfolio ----------------
class AssetBody(BaseModel):
    symbol: str
    asset_class: str
    quantity: float
    entry_price: float
    current_price: Optional[float] = None


class PriceUpdateBody(BaseModel):
    current_price: float


@app.get("/portfolio")
def portfolio():
    return {"summary": portfolio_svc.portfolio_summary(), "assets": portfolio_svc.list_assets()}


@app.post("/portfolio/assets")
def portfolio_add_asset(body: AssetBody):
    current = body.current_price if body.current_price is not None else body.entry_price
    return portfolio_svc.add_asset(body.symbol, body.asset_class, body.quantity, body.entry_price, current)


@app.get("/portfolio/assets/{asset_id}")
def portfolio_get_asset(asset_id: str):
    for a in portfolio_svc.list_assets():
        if a["id"] == asset_id:
            return a
    raise HTTPException(404, "asset not found")


@app.delete("/portfolio/assets/{asset_id}")
def portfolio_remove_asset(asset_id: str):
    if not portfolio_svc.remove_asset(asset_id):
        raise HTTPException(404, "asset not found")
    return {"status": "removed"}


@app.patch("/portfolio/assets/{asset_id}/price")
def portfolio_update_price(asset_id: str, body: PriceUpdateBody):
    updated = portfolio_svc.update_price(asset_id, body.current_price)
    if not updated:
        raise HTTPException(404, "asset not found")
    return updated


@app.get("/portfolio/diversification")
def portfolio_diversification():
    return portfolio_svc.diversification_score()


@app.get("/portfolio/tax-report")
def portfolio_tax(year: Optional[int] = None):
    return portfolio_svc.tax_report(year)


# ---------------- Risk Dashboard ----------------
@app.get("/risk/var")
def risk_var(confidence: float = 0.95):
    return risk_svc.value_at_risk(confidence)


@app.get("/risk/drawdown")
def risk_drawdown():
    return risk_svc.drawdown_analysis()


# ---------------- Performance Analytics ----------------
@app.get("/performance")
def performance_analytics_route():
    return risk_svc.performance_analytics()


# ---------------- Frontend (static SPA, same origin) ----------------
# The backend serves the statically-exported Next.js frontend from the same
# origin — one service, one port, no proxy. Specific API routes above win;
# this middleware only converts 404s for non-API GET requests into SPA pages
# or static files.
_HTML_PAGES = ("index", "dashboard", "learn", "splash", "videos")


def _resolve_frontend(path: str) -> Optional[Path]:
    fd = _frontend_dir()
    if fd is None:
        return None
    clean = path.strip("/")
    if not clean:
        return fd / "index.html"
    p = (fd / clean).resolve()
    if p.is_file() and p.is_relative_to(fd.resolve()):
        return p
    base = clean.split("/")[0].split("?")[0]
    if base in _HTML_PAGES:
        return fd / f"{base}.html"
    if (fd / "404.html").is_file():
        return fd / "404.html"
    return fd / "index.html"


@app.middleware("http")
async def serve_frontend(request, call_next):
    response = await call_next(request)
    if request.method != "GET" or response.status_code != 404:
        return response
    if request.url.path.startswith("/api/"):
        return response
    target = _resolve_frontend(request.url.path)
    if target is None:
        return response
    media_type, _ = mimetypes.guess_type(str(target))
    resp = FileResponse(target, media_type=media_type or "application/octet-stream")
    # HTML entry points must never be cached — a stale dashboard hid UI
    # updates before. Hashed _next assets stay cacheable.
    if target.suffix == ".html":
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp
