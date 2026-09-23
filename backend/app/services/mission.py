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
        "explicit_runs": bool(run_match),
        "symbol": _find_symbol(question),
        "wants_entry_digit": "entry" in q or "digit" in q,
        "wants_scan_all": "all market" in q or "all the market" in q or "scan" in q,
        # A question that asks for a PROBABILITY rather than a trade plan. These
        # are answered with the live measured rate per market, not a ladder.
        "wants_probability": any(w in q for w in (
            "probabilit", "chance", "odds", "how likely", "likely", "what are the",
            "percentage", "percent", "how often", "win rate", "winrate",
        )),
    }


def _find_symbol(question: str) -> Optional[str]:
    """Find an explicitly named market in the text, e.g. "over 5 on R_100".

    Without this, "probability of over 5 on R_100" would be measured across
    every market and answered with whichever happened to read highest - the
    user asked about ONE index and must get that index. Matches the code
    (R_100) and the spoken name ("volatility 100"). Returns None when the
    question names no market, which means "scan them all".
    """
    q = (question or "").lower()
    m = re.search(r"\b(r_\d+|r\d{2,3}|1hz\d+v|jd\d+|rdbear|rdbull)\b", q)
    if m:
        code = m.group(1).upper()
        # "r100" / "r 100" both mean R_100.
        if re.fullmatch(r"R\d{2,3}", code):
            code = f"R_{code[1:]}"
        return code
    # Spoken names: "volatility 100", "jump 50", "bear market", "bull market".
    if "bear market" in q:
        return "RDBEAR"
    if "bull market" in q:
        return "RDBULL"
    m = re.search(r"\b(?:jump|jd)\s*(\d{1,3})\b", q)
    if m:
        return f"JD{m.group(1)}"
    m = re.search(r"\bvol(?:atility)?\s*(\d{1,3})\b", q)
    if m:
        n = m.group(1)
        if "1s" in q or "1-s" in q or "one second" in q:
            return f"1HZ{n}V"
        return f"R_{n}"
    return None


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
        probabilities: List[dict] = []

        for symbol in symbols[:MAX_MARKETS]:
            try:
                card = self._cockpit.legs(symbol, predictions, window=window)
            except Exception as exc:  # a bad market must never kill the scan
                logger.warning("mission scan failed for %s: %s", symbol, exc)
                continue

            # Every leg with real tape gets a probability row, playable or not.
            # A question like "what is the probability of over 5" must be
            # answerable from the measurement, never gated on clearing a floor
            # the user did not ask about.
            for lg in card["legs"]:
                if lg.get("observed_pct") is None:
                    continue
                probabilities.append({
                    "symbol": symbol,
                    "n": card["n"],
                    "provider": card.get("provider"),
                    "live": card.get("provider") == "deriv_live" and card["n"] > 0,
                    "side": lg["side"], "barrier": lg["barrier"],
                    "observed_pct": lg["observed_pct"],
                    "confidence": lg["confidence"],
                    "breakeven_pct": lg["breakeven_pct"],
                    "edge_pp": lg["edge_pp"], "ev": lg["ev"],
                    "payout": lg["payout"],
                    "winning_digits": lg["winning_digits"],
                    "playable": bool(lg.get("playable")),
                    "entry": lg.get("entry"),
                })

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
        # Probability rows rank by measured rate - the direct answer to
        # "what is the probability of ...", best market first.
        probabilities.sort(key=lambda r: r["observed_pct"], reverse=True)
        return {"candidates": candidates, "scanned": scanned,
                "probabilities": probabilities}

    def probability(self, question: str, symbols: List[str],
                    predictions: Optional[List[dict]] = None,
                    window: int = DEFAULT_WINDOW,
                    symbol: Optional[str] = None) -> dict:
        """Answer "what is the probability of over 5?" with the measured rate.

        This is the typed-question path: the user asks for a probability, not a
        trade plan, so nothing here is gated on a confidence floor or on EV.
        Every market carrying tape reports its OBSERVED rate for the requested
        barrier, plus the fair breakeven that payout implies. The distinction
        that matters: `observed_pct` is what the tape actually did, and
        `probability` is that same number stated as the answer. A rate above
        breakeven is a measured edge; below it is a losing proposition, and
        both are reported plainly.
        """
        parsed = parse_request(question)
        preds = predictions if predictions else parsed["predictions"]
        base = {
            "question": question,
            "requested_predictions": preds,
            "window": window,
            "provider": "deriv_live",
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "PROBABILITY",
        }
        if not preds:
            return {**base, "verdict": "NEED_PREDICTIONS", "probabilities": [],
                    "best": None, "answer": (
                        "Tell me the barrier you want the probability for - for "
                        "example \"what is the probability of over 5 on R_100\".")}

        scan_symbols = [symbol] if symbol else symbols
        rows = self.scan(scan_symbols, preds, window=window)["probabilities"]
        if not rows:
            return {**base, "verdict": "NO_TAPE", "probabilities": [],
                    "best": None, "answer": (
                        "No market has enough live tape to measure that yet. "
                        "Nothing is reported until there is real data.")}

        # The headline row: the market with the highest measured chance of the
        # FIRST requested barrier winning.
        first = preds[0]
        for_first = [r for r in rows
                     if r["side"] == first["side"] and r["barrier"] == first["barrier"]]
        best = for_first[0] if for_first else rows[0]

        pred_txt = "{} {}".format(first["side"], first["barrier"])

        # A single window manufactures flukes - some digit always looks overfed
        # by chance, which is exactly how the phantom-edge session happened.
        # So the rate is confirmed across three windows before this card calls
        # anything an EDGE. One window alone can only ever be UNCONFIRMED.
        windows = self._multi_window(best, preds)
        filled = [w for w in windows if w["satisfied"]]
        confirmed = (len(filled) >= 2
                     and all(w["edge_pp"] > 0 for w in filled))
        if best["edge_pp"] > 0 and best["ev"] > 0:
            verdict = "EDGE" if confirmed else "UNCONFIRMED"
        else:
            verdict = "NO_EDGE"

        lines = [
            f"{pred_txt} has a {best['observed_pct']:.1f}% measured chance of "
            f"winning on {best['symbol']} over the last {best['n']} ticks "
            f"({best['winning_digits']}/10 digits win, {best['payout']:.2f}x "
            f"payout)."
        ]
        if verdict == "EDGE":
            lines.append(
                f"That is {best['edge_pp']:+.1f}pp above the "
                f"{best['breakeven_pct']:.1f}% breakeven this payout needs, "
                f"EV {best['ev']:+.3f} per $1, and it holds on every window "
                "checked (" + ", ".join(
                    f"{w['window']}:{w['edge_pp']:+.1f}pp" for w in windows) + ").")
        elif verdict == "UNCONFIRMED":
            lines.append(
                f"That is {best['edge_pp']:+.1f}pp above the "
                f"{best['breakeven_pct']:.1f}% breakeven this payout needs, but "
                "it does NOT hold across windows ("
                + ", ".join(f"{w['window']}:{w['edge_pp']:+.1f}pp" for w in windows)
                + ") - one window can fluke, so this is not yet an edge.")
        else:
            lines.append(
                f"Its payout needs {best['breakeven_pct']:.1f}% to break even, "
                f"so the tape is {best['edge_pp']:+.1f}pp against it - no edge "
                f"here, EV {best['ev']:+.3f} per $1.")
        others = [r for r in for_first[1:4]]
        if others:
            lines.append("Other markets: " + "; ".join(
                f"{r['symbol']} {r['observed_pct']:.1f}%" for r in others) + ".")

        return {
            **base,
            "verdict": verdict,
            "probabilities": rows,
            "best": best,
            "windows": windows,
            "probability": best["observed_pct"],
            "answer": " ".join(lines),
        }

    def _multi_window(self, row: dict, preds: List[dict],
                      windows=(100, 250, 1000)) -> List[dict]:
        """Re-measure the SAME barrier on other windows for the same market.

        A rate that only exists at one window length is a fluke. Each window is
        measured independently off the live tape.

        `satisfied` marks whether the tape was actually deep enough to fill
        that window. This is what makes the check real: on a 36-tick tape all
        three windows read the identical 36 ticks, so they agree trivially and
        would "confirm" anything. A window that cannot be filled is not
        evidence, so only satisfied windows may confirm an edge.
        """
        out: List[dict] = []
        for w in windows:
            try:
                card = self._cockpit.legs(
                    row["symbol"],
                    [{"side": row["side"], "barrier": row["barrier"]}],
                    window=w)
            except Exception as exc:
                logger.warning("window check failed for %s: %s", row["symbol"], exc)
                continue
            legs = card.get("legs") or []
            if not legs or legs[0].get("observed_pct") is None:
                continue
            lg = legs[0]
            out.append({
                "window": w, "n": card["n"],
                "satisfied": card["n"] >= w,
                "observed_pct": lg["observed_pct"],
                "breakeven_pct": lg["breakeven_pct"],
                "edge_pp": lg["edge_pp"], "ev": lg["ev"],
            })
        return out

    def plan(self, question: str, symbols: List[str],
             predictions: Optional[List[dict]] = None,
             target_runs: Optional[int] = None,
             window: int = DEFAULT_WINDOW,
             min_confidence: Optional[float] = None,
             stake: float = 1.0,
             balance: Optional[float] = None,
             force: Optional[str] = None) -> dict:
        """The full mission card answering the owner's plain-English request.

        `force` lets the caller override intent detection: "plan" pins the run
        ladder even when the wording reads like a probability question, and
        "probability" pins the measurement.
        """
        parsed = parse_request(question)
        preds = predictions if predictions else parsed["predictions"]
        runs = target_runs if target_runs else parsed["target_runs"]
        floor = min_confidence if min_confidence is not None else parsed["min_confidence"]

        # A question that asks for a probability, with no run target and no
        # scan request, is a measurement question - answer it directly with the
        # live rate instead of building a trade ladder nobody asked for.
        wants_prob = parsed["wants_probability"] if force is None else force == "probability"
        if (preds and wants_prob
                and not parsed["explicit_runs"] and not parsed["wants_scan_all"]
                and target_runs is None and predictions is None):
            # Honour a market named in the question ("... on R_100"). Only fall
            # back to scanning every market when none was named.
            named = parsed.get("symbol")
            if named and named in symbols:
                return self.probability(question, symbols, preds,
                                        window=window, symbol=named)
            return self.probability(question, symbols, preds, window=window)

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
            "probabilities": scan["probabilities"],
            "selected": selected,
            "runs": runs_math,
            "runs_available": len(live),
            "runs_short_by": max(0, runs - len(live)),
            "answer": " ".join(lines),
        }


mission_planner = MissionPlanner()
