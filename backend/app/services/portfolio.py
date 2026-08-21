"""Portfolio manager — multi-asset tracking, allocation, attribution, tax reports."""
import math
import statistics
import time
import uuid
from collections import defaultdict
from typing import List, Optional

_assets: dict[str, dict] = {}


class Asset(dict):
    pass


def add_asset(
    symbol: str,
    asset_class: str,
    quantity: float,
    entry_price: float,
    current_price: float,
    asset_id: Optional[str] = None,
) -> dict:
    aid = asset_id or str(uuid.uuid4())
    _assets[aid] = {
        "id": aid,
        "symbol": symbol,
        "asset_class": asset_class,
        "quantity": quantity,
        "entry_price": entry_price,
        "current_price": current_price,
        "value": round(quantity * current_price, 2),
        "pl": round((current_price - entry_price) * quantity, 2),
        "pl_pct": round((current_price / entry_price - 1) * 100, 2) if entry_price else 0.0,
        "created_at": time.time(),
    }
    return _assets[aid]


def remove_asset(asset_id: str) -> bool:
    return _assets.pop(asset_id, None) is not None


def update_price(asset_id: str, current_price: float) -> Optional[dict]:
    asset = _assets.get(asset_id)
    if not asset:
        return None
    asset["current_price"] = current_price
    asset["value"] = round(asset["quantity"] * current_price, 2)
    asset["pl"] = round((current_price - asset["entry_price"]) * asset["quantity"], 2)
    asset["pl_pct"] = round((current_price / asset["entry_price"] - 1) * 100, 2) if asset["entry_price"] else 0.0
    return asset


def list_assets() -> List[dict]:
    return list(_assets.values())


def portfolio_summary() -> dict:
    assets = list(_assets.values())
    total_value = sum(a["value"] for a in assets)
    total_pl = sum(a["pl"] for a in assets)
    by_class: dict[str, float] = defaultdict(float)
    for a in assets:
        by_class[a["asset_class"]] += a["value"]
    allocation = {
        cls: {"value": round(v, 2), "pct": round(v / total_value * 100, 2) if total_value else 0.0}
        for cls, v in by_class.items()
    }
    best = max(assets, key=lambda a: a["pl_pct"]) if assets else None
    worst = min(assets, key=lambda a: a["pl_pct"]) if assets else None
    return {
        "total_value": round(total_value, 2),
        "total_pl": round(total_pl, 2),
        "asset_count": len(assets),
        "allocation": allocation,
        "best_performer": {"symbol": best["symbol"], "pl_pct": best["pl_pct"]} if best else None,
        "worst_performer": {"symbol": worst["symbol"], "pl_pct": worst["pl_pct"]} if worst else None,
    }


def diversification_score() -> dict:
    """0-100 where 100 means perfect equal-weight diversification."""
    assets = list(_assets.values())
    if not assets:
        return {"score": 0.0, "grade": "NO_ASSETS", "concentration_risk": False}
    total = sum(a["value"] for a in assets)
    if total <= 0:
        return {"score": 0.0, "grade": "NO_VALUE", "concentration_risk": False}
    weights = [a["value"] / total for a in assets]
    # normalized entropy
    n = len(assets)
    entropy = -sum(w * math.log(w) for w in weights if w > 0)
    max_entropy = math.log(n) if n > 1 else 1
    score = round(entropy / max_entropy * 100, 1) if max_entropy else 100.0
    max_weight = max(weights)
    concentration_risk = max_weight > 0.5
    grade = (
        "EXCELLENT" if score >= 90 else
        "GOOD" if score >= 70 else
        "MODERATE" if score >= 50 else
        "LOW" if score >= 30 else "POOR"
    )
    return {
        "score": score,
        "grade": grade,
        "concentration_risk": concentration_risk,
        "largest_holding_pct": round(max_weight * 100, 2),
        "assets": n,
    }


def tax_report(year: Optional[int] = None) -> dict:
    """Realised capital gains attribution by asset class."""
    assets = list(_assets.values())
    realized = [a for a in assets if a["pl"] != 0]
    short_term = [a for a in realized if time.time() - a["created_at"] < 365 * 24 * 3600]
    long_term = [a for a in realized if a not in short_term]
    return {
        "year": year or time.strftime("%Y"),
        "realized_gains": round(sum(a["pl"] for a in realized if a["pl"] > 0), 2),
        "realized_losses": round(sum(a["pl"] for a in realized if a["pl"] < 0), 2),
        "short_term_pl": round(sum(a["pl"] for a in short_term), 2),
        "long_term_pl": round(sum(a["pl"] for a in long_term), 2),
    }
