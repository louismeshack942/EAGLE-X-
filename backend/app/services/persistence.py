"""Persistence — journal, alerts, backtests, replay sessions.

Portable by default: uses in-memory + JSON file storage so the app runs with
no database. PostgreSQL/Redis are wired via env flags when available.
"""
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "store.json"
_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load() -> dict:
    if _STORE_PATH.exists():
        try:
            return json.loads(_STORE_PATH.read_text())
        except Exception:  # noqa: BLE001
            pass
    return {"journal": [], "alerts": [], "backtests": [], "replays": {}, "settings": {}}


def _save(data: dict) -> None:
    tmp = _STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str))
    tmp.replace(_STORE_PATH)


class JournalEngine:
    def add_entry(
        self,
        market: str,
        contract: str,
        digit: Optional[int],
        stake: float,
        result: str,
        pnl: float,
        data_quality: float,
        evidence_score: float,
        mode: str,
        analysis_snapshot: Optional[dict] = None,
        user_id: str = "default",
    ) -> dict:
        entry = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "market": market,
            "contract": contract,
            "digit": digit,
            "stake": stake,
            "result": result,
            "pnl": round(pnl, 4),
            "data_quality": data_quality,
            "evidence_score": evidence_score,
            "mode": mode,
            "analysis_snapshot": analysis_snapshot or {},
            "timestamp": _utcnow().isoformat(),
        }
        with _lock:
            data = _load()
            data["journal"].append(entry)
            _save(data)
        return entry

    def list_entries(self, limit: int = 50) -> List[dict]:
        with _lock:
            data = _load()
            return list(reversed(data["journal"][-limit:]))

    def dashboard(self) -> dict:
        with _lock:
            data = _load()
            today = _utcnow().date().isoformat()
            todays = [e for e in data["journal"] if e["timestamp"].startswith(today)]
            wins = [e for e in todays if e["result"] == "win"]
            losses = [e for e in todays if e["result"] == "loss"]
            return {
                "trades_today": len(todays),
                "wins": len(wins),
                "losses": len(losses),
                "net_pnl": round(sum(e["pnl"] for e in todays), 4),
                "win_rate": round(len(wins) / len(todays) * 100, 1) if todays else 0.0,
            }


class AlertsEngine:
    def add_alert(self, type: str, message: str, user_id: str = "default") -> dict:
        alert = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "type": type,
            "message": message,
            "read": False,
            "timestamp": _utcnow().isoformat(),
        }
        with _lock:
            data = _load()
            data["alerts"].append(alert)
            _save(data)
        return alert

    def list_alerts(self, limit: int = 50) -> List[dict]:
        with _lock:
            data = _load()
            return list(reversed(data["alerts"][-limit:]))

    def mark_read(self, alert_id: str) -> bool:
        with _lock:
            data = _load()
            for a in data["alerts"]:
                if a["id"] == alert_id:
                    a["read"] = True
                    _save(data)
                    return True
        return False


class BacktestEngine:
    def run(self, ticks: List[dict], rule: str = "OVERFED_MATCH", balance: float = 10.0, stake: float = 1.0) -> dict:
        """Run a simple tick-by-tick backtest over provided ticks."""
        wins = losses = 0
        pnl = 0.0
        equity = [balance]
        trades: list[dict] = []
        for i, tick in enumerate(ticks):
            if i < 10:
                continue
            digits = [t.get("digit", 0) for t in ticks[max(0, i - 10):i]]
            from collections import Counter
            c = Counter(digits)
            candidate, _cnt = c.most_common(1)[0]
            actual = tick.get("digit", 0)
            won = candidate == actual
            trade_pnl = stake * 9 if won else -stake
            pnl += trade_pnl
            equity.append(equity[-1] + trade_pnl)
            trades.append({
                "index": i,
                "candidate": candidate,
                "actual": actual,
                "won": won,
                "pnl": trade_pnl,
            })
            if won:
                wins += 1
            else:
                losses += 1
        total = wins + losses
        peak = equity[0]
        max_dd = 0.0
        for v in equity:
            peak = max(peak, v)
            max_dd = max(max_dd, peak - v)
        gross_win = sum(t["pnl"] for t in trades if t["won"])
        gross_loss = abs(sum(t["pnl"] for t in trades if not t["won"]))
        return {
            "id": str(uuid.uuid4()),
            "rule": rule,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 2) if total else 0.0,
            "net_profit": round(pnl, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else 0.0,
            "max_drawdown": round(max_dd, 2),
            "equity_curve": equity[:500],
            "trades": trades[:500],
        }


class ReplayEngine:
    def load(self, ticks: List[dict], name: str = "replay") -> dict:
        session_id = str(uuid.uuid4())
        with _lock:
            data = _load()
            data["replays"][session_id] = {
                "id": session_id,
                "name": name,
                "ticks": ticks,
                "position": 0,
                "playing": False,
                "speed": 1.0,
            }
            _save(data)
        return data["replays"][session_id]

    def control(self, session_id: str, action: str, speed: float = 1.0) -> Optional[dict]:
        with _lock:
            data = _load()
            session = data["replays"].get(session_id)
            if not session:
                return None
            if action == "play":
                session["playing"] = True
                session["speed"] = speed
            elif action == "pause":
                session["playing"] = False
            elif action == "step":
                session["position"] = min(session["position"] + 1, len(session["ticks"]) - 1)
            elif action == "reset":
                session["position"] = 0
                session["playing"] = False
            _save(data)
        return session

    def get(self, session_id: str) -> Optional[dict]:
        with _lock:
            return _load()["replays"].get(session_id)


class SettingsStore:
    def get(self, key: str, default=None):
        with _lock:
            return _load()["settings"].get(key, default)

    def set(self, key: str, value) -> None:
        with _lock:
            data = _load()
            data["settings"][key] = value
            _save(data)


journal_engine = JournalEngine()
alerts_engine = AlertsEngine()
backtest_engine = BacktestEngine()
replay_engine = ReplayEngine()
settings_store = SettingsStore()
