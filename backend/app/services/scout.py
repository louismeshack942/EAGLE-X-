"""Table Scout — find the best table before you sit down.

A trader's real edge is table selection. The CF used to watch whatever
symbols he was given; the Scout grades every table (symbol) and says where
the money is — or says the honest thing: every table is fair, sit on your
hands today.

Grades per table:
- chi_square: whole-distribution skew vs fair (10 digits, 10% each).
  A single hot digit on a fair table is a mirage; a skewed TABLE is a story.
- best DIFFERS z per table (the only sustainable contract)
- Dirichlet posterior means per digit (alpha=1 prior) — cleaner shrinkage
- momentum: is the starving digit's frequency RISING recently? skip it
- multi-window confirmation: under-hit at 100 AND 300 AND 1000 ticks

Also learns from the journal:
- calibration: predicted confidence vs realized win rate per bucket
- hot hours / hot days: win rate by hour-of-day and weekday (min sample)
"""
import math
import time
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Optional

from app.core.queue import tick_queue
from app.services.persistence import journal_engine

FAIR_P = 0.10
SIGNIFICANCE_Z = 1.96
MIN_HOT_SAMPLE = 10   # hot-hours needs at least this many trades to speak
STALE_FEED_S = 15.0   # a table with no tick for 15s is a dead table

# z-trend memory: (symbol, digit) -> deque of (ts, z)
_z_history: dict = {}
_Z_HISTORY_LEN = 120


def _note_z(symbol: str, digit: int, z: float) -> None:
    key = (symbol, digit)
    if key not in _z_history:
        _z_history[key] = deque(maxlen=_Z_HISTORY_LEN)
    _z_history[key].append((time.time(), z))


def z_age_s(symbol: str, digit: int) -> float:
    """How long the digit has been continuously significant (z <= -1.96).

    An edge that appeared 3 seconds ago is noise; one that has held for a
    minute has survived its own regression test.
    """
    hist = _z_history.get((symbol, digit))
    if not hist:
        return 0.0
    age = 0.0
    now = time.time()
    for ts, z in reversed(hist):
        if z <= -SIGNIFICANCE_Z:
            age = now - ts
        else:
            break
    return round(age, 1)


def feed_health(symbols: list[str], queue=None) -> dict:
    """Is the tick feed alive? A stale feed makes every stat a lie."""
    queue = queue or tick_queue
    out = {}
    now = datetime.now(timezone.utc)
    for symbol in symbols:
        ticks = queue.recent(symbol, limit=1)
        if not ticks:
            out[symbol] = {"age_s": None, "stale": True, "ticks": 0}
            continue
        try:
            ts = ticks[-1].timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
        except Exception:  # noqa: BLE001
            age = None
        out[symbol] = {
            "age_s": round(age, 1) if age is not None else None,
            "stale": age is None or age > STALE_FEED_S,
            "ticks": len(queue.recent(symbol, limit=100)),
        }
    stale = [s for s, v in out.items() if v["stale"]]
    return {
        "symbols": out,
        "all_fresh": not stale,
        "stale_symbols": stale,
        "note": "all feeds fresh" if not stale else f"stale feeds: {', '.join(stale)}",
    }


def chi_square_digit(digits: list[int]) -> dict:
    """Chi-square goodness-of-fit of the digit stream vs the fair 10% prior.

    With 9 degrees of freedom, the 95% critical value is 16.92 and the 99%
    value is 21.67. Above that, the table itself is skewed — not one digit.
    """
    n = len(digits)
    if n < 30:
        return {"chi2": 0.0, "df": 9, "skewed": False, "level": "insufficient_data", "n": n}
    counts = Counter(digits)
    expected = n * FAIR_P
    chi2 = sum((counts.get(d, 0) - expected) ** 2 / expected for d in range(10))
    if chi2 >= 21.67:
        level = "strongly_skewed"
    elif chi2 >= 16.92:
        level = "skewed"
    else:
        level = "fair"
    return {"chi2": round(chi2, 2), "df": 9, "skewed": chi2 >= 16.92, "level": level, "n": n}


def dirichlet_estimates(digits: list[int], alpha: float = 1.0) -> dict:
    """Posterior mean per digit under a symmetric Dirichlet(alpha) prior.

    Cleaner than the ad-hoc pseudo-count: each digit's estimate is
    (count + alpha) / (n + 10*alpha). Small samples collapse to fair 10%.
    """
    n = len(digits)
    counts = Counter(digits)
    total = n + 10 * alpha
    return {str(d): round((counts.get(d, 0) + alpha) / total * 100, 3) for d in range(10)}


def _digit_z(digits: list[int]) -> dict:
    n = len(digits)
    counts = Counter(digits)
    den = math.sqrt(n * FAIR_P * (1 - FAIR_P)) if n else 1.0
    return {d: (counts.get(d, 0) - n * FAIR_P) / den for d in range(10)}


def momentum_rising(digits: list[int], digit: int, recent: int = 30) -> bool:
    """Is the digit's recent frequency RISING vs the full window?

    A starving digit whose frequency is climbing is already regressing to
    the mean — the DIFFERS edge is evaporating. Skip it.
    """
    if len(digits) < recent * 2:
        return False
    tail = digits[-recent:]
    head = digits[:-recent]
    tail_f = tail.count(digit) / len(tail)
    head_f = head.count(digit) / len(head) if head else FAIR_P
    return tail_f > head_f + 0.02


def multi_window_confirmed(symbol: str, digit: int, queue=None) -> dict:
    """The digit must be under-hit at 100 AND 300 AND 1000 ticks.

    Small windows alone manufacture fake edges; a digit starving at every
    scale is a real story. Queue depth permitting — missing windows report
    as unconfirmed, not as pass.
    """
    queue = queue or tick_queue
    windows = (100, 300, 1000)
    zs = {}
    for w in windows:
        ticks = queue.recent(symbol, limit=w)
        digits = [t.digit for t in ticks]
        if len(digits) < w * 0.5:   # not enough data for this window
            zs[w] = None
            continue
        zs[w] = round(_digit_z(digits)[digit], 2)
    measured = [z for z in zs.values() if z is not None]
    confirmed = bool(measured) and all(z <= -SIGNIFICANCE_Z for z in measured)
    return {"digit": digit, "windows": zs, "confirmed": confirmed}


def scan_tables(symbols: list[str], window: int = 100, queue=None) -> dict:
    """Grade every table and rank them. The CF should only sit at the best.

    For each symbol: chi-square skew, the best (most starving) digit, its
    z-score, DIFFERS EV at the standard 1.1 payout, momentum check, and a
    plain-English verdict.
    """
    queue = queue or tick_queue
    feed = feed_health(symbols, queue)
    track = {s: v for s, v in journal_breakdown().get("by_symbol", {}).items()}
    tables = []
    for symbol in symbols:
        if feed["symbols"].get(symbol, {}).get("stale"):
            tables.append({
                "symbol": symbol, "tradeable": False, "score": 0.0,
                "verdict": "feed is stale — no fresh ticks, every stat on this table is a lie",
                "chi2": None, "best_digit": None, "best_z": None, "differs_ev": None,
            })
            continue
        ticks = queue.recent(symbol, limit=window)
        digits = [t.digit for t in ticks]
        if len(digits) < 30:
            tables.append({
                "symbol": symbol, "tradeable": False, "score": 0.0,
                "verdict": "not enough ticks yet",
                "chi2": None, "best_digit": None, "best_z": None, "differs_ev": None,
            })
            continue
        chi = chi_square_digit(digits)
        zs = _digit_z(digits)
        posterior = dirichlet_estimates(digits)
        # Most starving digit = most negative z
        best_digit = min(range(10), key=lambda d: zs[d])
        best_z = zs[best_digit]
        p_diff = 1.0 - posterior[str(best_digit)] / 100.0
        differs_ev = round(p_diff * 1.1 - 1.0, 4)
        rising = momentum_rising(digits, best_digit)
        mw = multi_window_confirmed(symbol, best_digit, queue)
        _note_z(symbol, best_digit, best_z)
        age = z_age_s(symbol, best_digit)
        tradeable = (
            best_z <= -SIGNIFICANCE_Z
            and chi["skewed"]
            and not rising
            and mw["confirmed"]
            and differs_ev > 0
        )
        score = round(max(0.0, -best_z) * 10 + (chi["chi2"] or 0) * 0.5, 1)
        rec = track.get(symbol)
        rec_txt = (
            f" (your record here: {rec['win_rate']}% WR over {rec['trades']}, ${rec['pnl']:+.2f})"
            if rec and rec["trades"] >= 3 else ""
        )
        if tradeable:
            verdict = f"HOT: digit {best_digit} starving at z={best_z:.2f}, table skewed, all windows confirm{rec_txt}"
        elif best_z <= -SIGNIFICANCE_Z and not chi["skewed"]:
            verdict = f"digit {best_digit} looks hot but the TABLE is fair — mirage, skip"
        elif rising:
            verdict = f"digit {best_digit} edge is evaporating (frequency rising) — skip"
        elif best_z <= -SIGNIFICANCE_Z and not mw["confirmed"]:
            verdict = f"digit {best_digit} hot only on the short window — not confirmed, skip"
        else:
            verdict = "fair table — no edge, correctly no trade"
        tables.append({
            "symbol": symbol,
            "tradeable": tradeable,
            "score": score,
            "verdict": verdict,
            "chi2": chi,
            "best_digit": best_digit,
            "best_z": round(best_z, 2),
            "z_age_s": age,
            "differs_ev": differs_ev,
            "momentum_rising": rising,
            "multi_window": mw,
            "posterior": posterior,
            "track_record": rec,
        })
    tables.sort(key=lambda t: t["score"], reverse=True)
    hot = [t for t in tables if t["tradeable"]]
    if hot:
        summary = f"Best table: {hot[0]['symbol']} — {hot[0]['verdict']}"
    else:
        summary = "All tables fair right now. No trade IS the correct position."
    return {
        "tables": tables,
        "best_table": hot[0] if hot else None,
        "summary": summary,
        "scanned": len(tables),
    }


def heatmap(symbols: list[str], window: int = 100, queue=None) -> dict:
    """Digit x symbol z-score matrix for the dashboard heatmap widget.

    Green (negative z) = starving digit = good for DIFFERS.
    """
    queue = queue or tick_queue
    grid = {}
    for symbol in symbols:
        ticks = queue.recent(symbol, limit=window)
        digits = [t.digit for t in ticks]
        if len(digits) < 10:
            grid[symbol] = {str(d): 0.0 for d in range(10)}
            continue
        zs = _digit_z(digits)
        grid[symbol] = {str(d): round(zs[d], 2) for d in range(10)}
    return {"window": window, "z_scores": grid, "significance_z": SIGNIFICANCE_Z}


# ---------------- learning from the journal ----------------

def _journal_entries(limit: int = 2000) -> list[dict]:
    return journal_engine.list_entries(limit=limit)


def calibration() -> dict:
    """Predicted confidence vs realized win rate, per bucket.

    The CF says '92%'. Does he win 92%? Buckets the journal by the
    confidence recorded at entry and compares. >5pp off = miscalibrated.
    """
    entries = _journal_entries()
    buckets: dict[str, dict] = {}
    for e in entries:
        snap = e.get("analysis_snapshot") or {}
        conf = snap.get("confidence")
        if conf is None:
            continue
        b = int(float(conf) // 10 * 10)
        key = f"{b}-{b + 10}"
        buckets.setdefault(key, {"predicted_mid": b + 5, "trades": 0, "wins": 0})
        buckets[key]["trades"] += 1
        if e["result"] == "win":
            buckets[key]["wins"] += 1
    out = []
    for key, b in sorted(buckets.items()):
        realized = round(b["wins"] / b["trades"] * 100, 1) if b["trades"] else 0.0
        out.append({
            "bucket": key,
            "predicted_mid": b["predicted_mid"],
            "realized_win_rate": realized,
            "trades": b["trades"],
            "gap": round(realized - b["predicted_mid"], 1),
        })
    over = [r for r in out if r["trades"] >= 5 and r["gap"] < -10]
    verdict = (
        "CF is OVERCONFIDENT — shrink his numbers" if over else
        "calibration looks honest" if out else "not enough journaled trades yet"
    )
    return {"buckets": out, "verdict": verdict, "overconfident": bool(over)}


def performance_by_hour() -> dict:
    """Win rate + P&L by hour-of-day and weekday from the journal.

    Synthetics have no market clock, but the CF's own record is real data:
    if 03:00-05:00 has been a graveyard over 40 trades, stop trading it.
    """
    entries = _journal_entries()
    by_hour: dict[int, dict] = {}
    by_day: dict[int, dict] = {}
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["timestamp"])
        except Exception:  # noqa: BLE001
            continue
        for bucket, key in ((by_hour, ts.hour), (by_day, ts.weekday())):
            b = bucket.setdefault(key, {"trades": 0, "wins": 0, "pnl": 0.0})
            b["trades"] += 1
            b["wins"] += 1 if e["result"] == "win" else 0
            b["pnl"] += e.get("pnl", 0.0)
    def fmt(bucket):
        return {
            str(k): {
                "trades": v["trades"],
                "win_rate": round(v["wins"] / v["trades"] * 100, 1),
                "pnl": round(v["pnl"], 2),
                "enough_data": v["trades"] >= MIN_HOT_SAMPLE,
            }
            for k, v in sorted(bucket.items())
        }
    hours = fmt(by_hour)
    days = fmt(by_day)
    good_hours = [int(h) for h, v in hours.items() if v["enough_data"] and v["win_rate"] >= 55 and v["pnl"] > 0]
    bad_hours = [int(h) for h, v in hours.items() if v["enough_data"] and (v["win_rate"] < 45 or v["pnl"] < 0)]
    now = datetime.now(timezone.utc)
    current = hours.get(str(now.hour))
    current_ok = None if not current or not current["enough_data"] else now.hour not in bad_hours
    return {
        "by_hour_utc": hours,
        "by_weekday": days,
        "good_hours_utc": sorted(good_hours),
        "bad_hours_utc": sorted(bad_hours),
        "current_hour_utc": now.hour,
        "current_window_ok": current_ok,  # None = not enough data to judge
        "note": "Synthetics run 24/7 — this is the CF's own track record, not market hours.",
    }


def journal_breakdown() -> dict:
    """Performance by symbol, contract type, and streaks from the journal."""
    entries = _journal_entries()
    by_symbol: dict[str, dict] = {}
    by_contract: dict[str, dict] = {}
    longest_win = longest_loss = cur_win = cur_loss = 0
    for e in entries:  # newest first -> walk in reverse for streaks
        pass
    for e in reversed(entries):
        s = by_symbol.setdefault(e["market"], {"trades": 0, "wins": 0, "pnl": 0.0})
        s["trades"] += 1; s["wins"] += e["result"] == "win"; s["pnl"] += e.get("pnl", 0.0)
        c = by_contract.setdefault(e["contract"], {"trades": 0, "wins": 0, "pnl": 0.0})
        c["trades"] += 1; c["wins"] += e["result"] == "win"; c["pnl"] += e.get("pnl", 0.0)
        if e["result"] == "win":
            cur_win += 1; cur_loss = 0
        else:
            cur_loss += 1; cur_win = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)
    def fmt(b):
        return {k: {"trades": v["trades"], "win_rate": round(v["wins"] / v["trades"] * 100, 1), "pnl": round(v["pnl"], 2)} for k, v in sorted(b.items())}
    gross_win = sum(e["pnl"] for e in entries if e.get("pnl", 0) > 0)
    gross_loss = abs(sum(e["pnl"] for e in entries if e.get("pnl", 0) < 0))
    return {
        "by_symbol": fmt(by_symbol),
        "by_contract": fmt(by_contract),
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "trades": len(entries),
    }
