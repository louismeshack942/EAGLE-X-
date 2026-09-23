"""AI Copilot - Mission Planning.

Turns a plain-English trading request into a grounded, multi-market plan.

The owner's shape:

    "scan all markets favourable, i want to trade over 4 prediction,
     under 7 prediction, and tell me the entry digit at that market,
     i want to make 5 profitable runs"

Four things happen here:
  1. `parse_request()` reads the barriers and the run target out of the text.
  2. every requested market is checked against the live tape for the user's
     OWN barriers (not the engine's favourite ones).
  3. qualifying markets are ranked by confidence then EV, and priced with a
     capped quarter-Kelly stake.
  4. the run target is answered with real probability maths - never a promise.

HONESTY RULE: "5 profitable runs" is a target, not an entitlement. This module
never claims the target will be hit. It reports how many markets actually
offer a play, what each one is worth, and the compounded probability of
stringing the requested number of wins together. On a fair board the honest
answer is often "zero markets qualify" or "the ladder is shorter than you
asked for", and that is the correct answer.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from app.services.cockpit import (
    MIN_CONFIDENCE_PCT,
    CockpitEngine,
)

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = 250
DEFAULT_TARGET_RUNS = 5
MAX_TARGET_RUNS = 50
MAX_MARKETS = 25


def parse_request(question: str) -> dict:
    """Extract barriers, run target and confidence floor from plain English.

    Understands "over 4", "under 7", "o4"/"u7", and "5 profitable runs" /
    "3 wins" / "make 5 runs". Anything it cannot find is left at its default
    so the caller can decide, rather than silently inventing a barrier.
    """
    q = (question or "").lower()

    predictions: List[dict] = []
    seen = set()
    for m in re.finditer(r"\b(over|under|above|below|o|u)\s*([0-9])\b", q):
        word, digit = m.group(1), int(m.group(2))
        side = "OVER" if word in ("over", "above", "o") else "UNDER"
        # OVER 9 and UNDER 0 win on nothing - they can never pay.
        if side == "OVER" and digit >= 9:
            continue
        if side == "UNDER" and digit <= 0:
            continue
        key = (side, digit)
        if key not in seen:
            seen.add(key)
            predictions.append({"side": side, "barrier": digit})

    target_runs = DEFAULT_TARGET_RUNS
    run_match = re.search(
        r"(\d+)\s*(?:more\s+)?(?:profitable\s+|winning\s+|win\s+|green\s+|consecutive\s+)?"
        r"(?:runs?|wins?|trades?|tickets?|rounds?)", q)
    if run_match:
        target_runs = max(1, min(MAX_TARGET_RUNS, int(run_match.group(1))))

    floor = MIN_CONFIDENCE_PCT
    floor_match = re.search(r"(\d{2}(?:\.\d+)?)\s*%\s*(?:confidence|conf|floor)", q)
    if floor_match:
        floor = max(50.0, min(99.0, float(floor_match.group(1))))

    return {
        "predictions": predictions,
        "target_runs": target_runs,
        "min_confidence": floor,
        "wants_entry_digit": "entry" in q or "digit" in q,
        "wants_scan_all": "all market" in q or "all the market" in q or "scan" in q,
    }


def _compound(probs: List[float]) -> float:
    """P(all of these independent plays win) - the run-ladder probability."""
    out = 1.0
    for p in probs:
        out *= p
    return out


def _runs_math(p_win: float, target_runs: int) -> dict:
    """Honest expectation maths for a run target at a given win probability.

    `expected_runs_to_finish` is the attempt count you would sit through on
    average to bank `target_runs` wins - the inverse of the per-attempt win
    probability, multiplied by the target. It is a long-run average, never a
    schedule.
    """
    p_win = max(min(p_win, 0.999), 0.001)
    return {
        "p_single_run": round(p_win, 4),
        "p_all_runs": round(p_win ** target_runs, 6),
        "expected_attempts_to_target": int(round(target_runs / p_win)),
        "expected_runs_per_10_attempts": round(p_win * 10, 2),
        "note": (
            f"Each run is an independent trade. At {p_win * 100:.1f}% per run, "
            f"stringing {target_runs} in a row is {p_win ** target_runs * 100:.3f}% "
            f"- a target, not a promise."
        ),
    }


class MissionPlanner:
    """Builds the copilot's multi-market answer card. Advisory only."""

    def __init__(self) -> None:
        self._cockpit = CockpitEngine()

    def scan(self, symbols: List[str], predictions: List[dict],
             target_runs: int = DEFAULT_TARGET_RUNS, window: int = DEFAULT_WINDOW,
             min_confidence: float = MIN_CONFIDENCE_PCT,
             stake: float = 1.0,
             balance: Optional[float] = None) -> dict:
        """Check every market against the user's OWN barriers, then rank."""
        candidates: List[dict] = []
        scanned: List[dict] = []

        for symbol in symbols[:MAX_MARKETS]:
            try:
                card = self._cockpit.legs(symbol, predictions, window=window)
            except Exception as exc:  # a bad market must never kill the scan
                logger.warning("mission scan failed for %s: %s", symbol, exc)
                continue

            playable = [lg for lg in card["legs"] if lg.get("playable")
                        and lg.get("confidence", 0) >= min_confidence]
            scanned.append({
                "symbol": symbol, "n": card["n"],
                "playable_legs": len(playable),
                "best_confidence": max(
                    [lg.get("confidence", 0) for lg in card["legs"]] or [0.0]),
            })

            if not playable:
                continue

            best = max(playable, key=lambda r: (r["confidence"], r["ev"]))
            is_live = card.get("provider") == "deriv_live" and card["n"] > 0
            candidates.append({
                "symbol": symbol,
                "n": card["n"],
                "provider": card.get("provider"),
                "live": is_live,
                # every requested leg that cleared the floor on this market
                "legs": [{
                    "side": lg["side"], "barrier": lg["barrier"],
                    "confidence": lg["confidence"],
                    "observed_pct": lg["observed_pct"],
                    "breakeven_pct": lg["breakeven_pct"],
                    "edge_pp": lg["edge_pp"], "ev": lg["ev"],
                    "payout": lg["payout"],
                    "entry": lg.get("entry"),
                } for lg in playable],
                "play": {
                    "side": best["side"], "barrier": best["barrier"],
                    "confidence": best["confidence"], "ev": best["ev"],
                    "payout": best["payout"],
                    "entry_digit": (best.get("entry") or {}).get("digit"),
                    "entry_share_pct": (best.get("entry") or {}).get("pct"),
                    "entry_inside": (best.get("entry") or {}).get("inside"),
                },
            })

        # Rank by confidence, tie-break EV - a better-paying play of equal
        # confidence is the better run.
        candidates.sort(key=lambda c: (c["play"]["confidence"], c["play"]["ev"]),
                        reverse=True)
        return {"candidates": candidates, "scanned": scanned}

    def plan(self, question: str, symbols: List[str],
             predictions: Optional[List[dict]] = None,
             target_runs: Optional[int] = None,
             window: int = DEFAULT_WINDOW,
             min_confidence: Optional[float] = None,
             stake: float = 1.0,
             balance: Optional[float] = None) -> dict:
        """The full mission card answering the owner's plain-English request."""
        parsed = parse_request(question)
        preds = predictions if predictions else parsed["predictions"]
        runs = target_runs if target_runs else parsed["target_runs"]
        floor = min_confidence if min_confidence is not None else parsed["min_confidence"]

        base = {
            "question": question,
            "requested_predictions": preds,
            "target_runs": runs,
            "window": window,
            "min_confidence_pct": floor,
            "provider": "deriv_live",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        if not preds:
            return {**base, "verdict": "NEED_PREDICTIONS", "candidates": [],
                    "selected": [], "runs": None,
                    "answer": ("Tell me which barriers you want and I will check "
                               "them against every market - for example \"scan all "
                               "markets, over 4 and under 7, entry digit, 5 runs\".")}

        scan = self.scan(symbols, preds, target_runs=runs, window=window,
                         min_confidence=floor, stake=stake, balance=balance)
        candidates = scan["candidates"]
        live = [c for c in candidates if c["live"]]
        selected = live[:runs]

        pred_txt = ", ".join(
            "{} {}".format(p["side"], p["barrier"]) for p in preds)

        if not candidates:
            best_seen = max([s["best_confidence"] for s in scan["scanned"]] or [0.0])
            return {**base, "verdict": "NO_MARKET", "candidates": [],
                    "scanned": scan["scanned"], "selected": [], "runs": None,
                    "answer": (
                        f"I scanned {len(scan['scanned'])} markets for {pred_txt} "
                        f"and none supports it. Best reading anywhere was "
                        f"{best_seen:.1f}% against your {floor:.0f}% floor. "
                        "No trade is the correct answer here.")}

        probs = [c["play"]["confidence"] / 100.0 for c in selected]
        runs_math = _runs_math(min(probs), runs) if probs else None

        verdict = ("FULL_LADDER" if len(selected) >= runs
                   else "PARTIAL_LADDER")

        lines = [
            f"I found {len(live)} market"
            f"{'s' if len(live) != 1 else ''} supporting {pred_txt} "
            f"at {floor:.0f}%+ confidence."
        ]
        for i, c in enumerate(selected, 1):
            pl = c["play"]
            entry_txt = ""
            if pl.get("entry_digit") is not None:
                entry_txt = f", entry digit {pl['entry_digit']}"
            lines.append(
                f"Run {i}: {c['symbol']} - {pl['side']} {pl['barrier']}"
                f"{entry_txt} - {pl['confidence']:.1f}% confidence, "
                f"{pl['payout']:.2f}x, EV {pl['ev']:+.3f}/$.")
        if len(selected) < runs:
            lines.append(
                f"That is {len(selected)} run"
                f"{'s' if len(selected) != 1 else ''} available, not the {runs} "
                "you asked for - the other markets did not clear the floor.")
        if runs_math:
            lines.append(runs_math["note"])

        return {
            **base,
            "verdict": verdict,
            "candidates": candidates,
            "scanned": scan["scanned"],
            "selected": selected,
            "runs": runs_math,
            "runs_available": len(live),
            "runs_short_by": max(0, runs - len(live)),
            "answer": " ".join(lines),
        }


mission_planner = MissionPlanner()
