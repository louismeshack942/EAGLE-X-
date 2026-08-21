"""Contract timer — start and settle simulated contract timers."""
import threading
import time
import uuid
from typing import Optional

_lock = threading.Lock()
_contracts: dict[str, dict] = {}


def start_contract(symbol: str, contract_type: str, stake: float, duration_seconds: int, digit: Optional[int] = None) -> dict:
    cid = str(uuid.uuid4())
    with _lock:
        _contracts[cid] = {
            "id": cid,
            "symbol": symbol,
            "contract_type": contract_type,
            "digit": digit,
            "stake": stake,
            "duration_seconds": duration_seconds,
            "start_time": time.time(),
            "end_time": time.time() + duration_seconds,
            "status": "running",
            "result": None,
            "pnl": 0.0,
        }
    return _contracts[cid]


def settle_contract(contract_id: str, result: str, pnl: float) -> Optional[dict]:
    with _lock:
        c = _contracts.get(contract_id)
        if not c:
            return None
        c["status"] = "settled"
        c["result"] = result
        c["pnl"] = pnl
        return c


def get_contract(contract_id: str) -> Optional[dict]:
    with _lock:
        c = _contracts.get(contract_id)
        if not c:
            return None
        remaining = max(0.0, c["end_time"] - time.time())
        return {**c, "remaining_seconds": round(remaining, 2)}
