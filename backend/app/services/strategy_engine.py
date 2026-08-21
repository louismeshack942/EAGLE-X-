"""Strategy Engine — create, run and evaluate trading strategies."""
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class StrategyType(str, Enum):
    DIGIT_MATCH = "DIGIT_MATCH"
    DIGIT_DIFF = "DIGIT_DIFF"
    OVER_UNDER = "OVER_UNDER"
    ODD_EVEN = "ODD_EVEN"
    TREND_FOLLOW = "TREND_FOLLOW"
    VOLATILITY_BREAKOUT = "VOLATILITY_BREAKOUT"


class TradingMode(str, Enum):
    MANUAL = "MANUAL"
    AUTO = "AUTO"


class MoneyMethod(str, Enum):
    FIXED = "FIXED"
    PERCENT = "PERCENT"
    MARTINGALE = "MARTINGALE"


class StrategyConfig(BaseModel):
    name: str = "Default"
    strategy_type: StrategyType = StrategyType.DIGIT_MATCH
    mode: TradingMode = TradingMode.MANUAL
    symbol: str = "R_100"
    stake: float = 1.0
    duration_seconds: int = 60
    min_evidence_deviation: float = 4.0
    min_data_quality: float = 60.0
    min_confidence: float = 40.0
    money_method: MoneyMethod = MoneyMethod.FIXED
    martingale_multiplier: float = 2.0
    max_stake: float = 50.0
    stop_loss: float = 20.0
    take_profit: float = 20.0
    max_consecutive_losses: int = 5
    version: int = 1
    is_public: bool = False


@dataclass
class TradeResult:
    id: str
    won: bool
    pnl: float
    stake: float
    timestamp: str


@dataclass
class StrategySession:
    id: str
    config: StrategyConfig
    status: str = "idle"
    trades: List[TradeResult] = field(default_factory=list)
    pnl: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "config": self.config.model_dump(),
            "status": self.status,
            "trades": [vars(t) for t in self.trades],
            "pnl": round(self.pnl, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_sessions: dict[str, StrategySession] = {}


class StrategyEngine:
    def create_session(self, config: StrategyConfig) -> StrategySession:
        sid = str(uuid.uuid4())
        session = StrategySession(id=sid, config=config)
        _sessions[sid] = session
        return session

    def list_sessions(self) -> list[dict]:
        return [s.to_dict() for s in _sessions.values()]

    def get(self, session_id: str) -> Optional[StrategySession]:
        return _sessions.get(session_id)

    def start(self, session_id: str) -> Optional[dict]:
        s = _sessions.get(session_id)
        if not s:
            return None
        s.status = "running"
        s.updated_at = time.time()
        return {"session_id": session_id, "status": "running"}

    def stop(self, session_id: str) -> Optional[dict]:
        s = _sessions.get(session_id)
        if not s:
            return None
        s.status = "stopped"
        s.updated_at = time.time()
        return {"session_id": session_id, "status": "stopped"}

    def evaluate(
        self,
        session_id: str,
        analysis: float,
        signal: str,
        quality: float,
    ) -> Optional[dict]:
        """Decide whether this strategy would trigger a trade."""
        s = _sessions.get(session_id)
        if not s:
            return None
        if quality < s.config.min_data_quality:
            return {"trigger": False, "reason": "data quality below threshold"}
        if analysis < s.config.min_evidence_deviation:
            return {"trigger": False, "reason": "evidence deviation below threshold"}
        strong = "STRONG" in signal
        return {
            "trigger": strong,
            "reason": "signal meets criteria" if strong else "signal too weak",
            "stake": s.config.stake,
            "duration_seconds": s.config.duration_seconds,
            "symbol": s.config.symbol,
            "strategy_type": s.config.strategy_type.value,
        }

    def record_result(self, session_id: str, won: bool, payout: float) -> Optional[dict]:
        s = _sessions.get(session_id)
        if not s:
            return None
        stake = s.config.stake
        pnl = payout - stake if won else -stake
        s.pnl += pnl
        s.trades.append(TradeResult(
            id=str(uuid.uuid4()),
            won=won,
            pnl=round(pnl, 4),
            stake=stake,
            timestamp=str(time.time()),
        ))
        s.updated_at = time.time()
        return {"session_id": session_id, "pnl": round(s.pnl, 4), "trades": len(s.trades)}


strategy_engine = StrategyEngine()
