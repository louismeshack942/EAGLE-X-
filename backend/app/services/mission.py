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
    MIN_EDGE_PP,
    CockpitEngine,
)

logger = logging.getLogger(__name__)

DEFAULT_WINDOW = 250
DEFAULT_TARGET_RUNS = 5
MAX_TARGET_RUNS = 50
MAX_MARKETS = 25
# Cap on rows spelled out in a scan answer; the full board is always in `rows`.
MAX_REPORT_ROWS = 25
# Run counts a scan projects by default when none were asked for.
DEFAULT_RUN_OPTIONS = (5, 10)


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

    # A scan asks for several run horizons at once ("5 or 10 runs", "5, 10 or
    # 20 runs"). Parse the run clause as a unit - numbers joined by or/and/
    # commas - so the barrier digit in "over 4 ... for 5 or 10 runs" cannot be
    # mistaken for a horizon.
    runs_options: List[int] = []
    for m in re.finditer(
            r"((?:\d{1,2}\s*(?:,|/|or|and|&)\s*)*\d{1,2})\s*"
            r"(?:profitable\s+|winning\s+|consecutive\s+)?"
            r"(?:runs?|rounds?|wins?)\b", q):
        for n in re.findall(r"\d{1,2}", m.group(1)):
            v = int(n)
            if 1 <= v <= MAX_TARGET_RUNS and v not in runs_options:
                runs_options.append(v)
    runs_options.sort()

    # A confidence floor is OPT-IN. `confidence` is a Wilson lower bound, so a
    # flat percentage against it means a different margin on every payout -
    # leaving it as the default stack a 7.5-sigma demand on OVER 4. Only set it
    # when the user actually typed one ("70% confidence").
    floor = None
    floor_match = re.search(r"(\d{2}(?:\.\d+)?)\s*%\s*(?:confidence|conf|floor)", q)
    if floor_match:
        floor = max(50.0, min(99.0, float(floor_match.group(1))))

    return {
        "predictions": predictions,
        "target_runs": target_runs,
        "runs_options": runs_options,
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


def _best_entry(legs: List[dict]) -> Optional[dict]:
    """The strongest ENTRY POINT across a market's legs.

    An entry point is the digit adjacent to the barrier (OVER 4 -> 5, UNDER 7
    -> 6). Ranks by the barrier's own confidence, tie-break its edge, so a
    "best entry point" question gets an answer even on a market where nothing
    is playable - the point still EXISTS, it just is not an edge.

    `digit_validated` is the honest qualifier: the band's edge can be real
    while the single entry digit is not itself overweight. When it is False the
    entry point is a WEAK reading and the card says so rather than presenting
    it as a signal.
    """
    rows = [lg for lg in (legs or []) if lg.get("entry")]
    if not rows:
        return None
    top = max(rows, key=lambda r: (r.get("confidence", 0.0), r.get("edge_pp", 0.0)))
    entry = top["entry"]
    validated = bool(entry.get("validated", entry.get("inside", False)))
    return {
        "side": top.get("side"),
        "barrier": top.get("barrier"),
        "digit": entry.get("digit"),
        "digit_pct": entry.get("pct"),
        "digit_wilson_lb": entry.get("wilson_lb"),
        "digit_fair_pct": entry.get("fair_pct"),
        "digit_edge_pp": entry.get("edge_pp"),
        "digit_validated": validated,
        "inside": validated,
        "observed_pct": top.get("observed_pct"),
        "confidence": top.get("confidence"),
        "breakeven_pct": top.get("breakeven_pct"),
        "edge_pp": top.get("edge_pp"),
        # Both bases, named. `confidence_margin_pp` is the one the gate uses.
        "raw_edge_pp": round((top.get("observed_pct") or 0.0)
                             - (top.get("breakeven_pct") or 0.0), 2),
        "confidence_margin_pp": round((top.get("confidence") or 0.0)
                                      - (top.get("breakeven_pct") or 0.0), 2),
        "ev": top.get("ev"),
        "payout": top.get("payout"),
        "playable": bool(top.get("playable")),
        # True only when BOTH the band pays and the entry digit is real.
        "sharp": bool(top.get("playable")) and validated,
    }


def _best_of(scanned: List[dict]) -> Optional[dict]:
    """Best entry point across every scanned market, labelled with its symbol."""
    rows = [s for s in (scanned or []) if s.get("best_entry")]
    if not rows:
        return None
    top = max(rows, key=lambda s: (s["best_entry"].get("confidence", 0.0),
                                   s["best_entry"].get("edge_pp", 0.0)))
    return {**top["best_entry"], "symbol": top["symbol"], "n": top.get("n")}


def _best_entry_from_rows(rows: List[dict]) -> Optional[dict]:
    """The strongest entry point across scan probability rows.

    Ranks purely on how often the entry digit lands - that is what an entry
    point IS. Playability is reported alongside (`band_playable`) rather than
    used as a filter, so a market with a strong entry and a thin band still
    appears with both facts stated.
    """
    if not rows:
        return None
    top = max(rows, key=lambda r: ((r.get("entry") or {}).get("pct") or 0.0,
                                   r.get("observed_pct") or 0.0))
    entry = top.get("entry") or {}
    return {
        "symbol": top.get("symbol"),
        "side": top.get("side"), "barrier": top.get("barrier"),
        "digit": entry.get("digit"),
        "digit_pct": entry.get("pct"),
        "digit_edge_pp": entry.get("edge_pp"),
        "digit_validated": entry.get("validated"),
        "band_observed_pct": top.get("observed_pct"),
        "band_breakeven_pct": top.get("breakeven_pct"),
        "band_edge_pp": top.get("edge_pp"),
        "payout": top.get("payout"),
        "band_playable": bool(top.get("playable")),
        "n": top.get("n"),
    }


def _qualifies(leg: dict, min_confidence: Optional[float]) -> bool:
    """The row's own breakeven gate, plus an explicit user floor if given.

    `min_confidence` is None when the user named no floor - then the row's own
    gate decides. An absolute floor is opt-in, never the default.
    """
    if not leg.get("playable"):
        return False
    if min_confidence is None:
        return True
    return leg.get("confidence", 0) >= min_confidence


class MissionPlanner:
    """Builds the copilot's multi-market answer card. Advisory only."""

    def __init__(self) -> None:
        self._cockpit = CockpitEngine()

    def scan(self, symbols: List[str], predictions: List[dict],
             target_runs: int = DEFAULT_TARGET_RUNS, window: int = DEFAULT_WINDOW,
             min_confidence: Optional[float] = None,
             stake: float = 1.0,
             balance: Optional[float] = None) -> dict:
        """Check every market against the user's OWN barriers, then rank.

        `min_confidence` is an OPTIONAL user override. When None (the default)
        a leg is judged by `barrier_row`'s own gate - its Wilson lower bound
        beating that contract's breakeven by MIN_EDGE_PP. An absolute floor is
        only applied when the caller explicitly asks for one, because a flat
        percentage against a Wilson bound demands wildly different margins on
        different payouts (OVER 4 needed a 7.5-sigma tape; UNDER 8 could pass
        on a losing one).
        """
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
                    # Two margins, named for their basis. The gate decides on
                    # `confidence` (the Wilson bound), so a report that pairs a
                    # bound with a raw margin reads as a contradiction. Keep
                    # both, labelled, so raw and bound can never be conflated.
                    "raw_edge_pp": round(lg["observed_pct"] - lg["breakeven_pct"], 2),
                    "confidence_margin_pp": round(
                        lg["confidence"] - lg["breakeven_pct"], 2),
                    "payout": lg["payout"],
                    "winning_digits": lg["winning_digits"],
                    "playable": bool(lg.get("playable")),
                    "entry": lg.get("entry"),
                })

            playable = [lg for lg in card["legs"] if _qualifies(lg, min_confidence)]
            scanned.append({
                "symbol": symbol, "n": card["n"],
                "playable_legs": len(playable),
                "best_confidence": max(
                    [lg.get("confidence", 0) for lg in card["legs"]] or [0.0]),
                "best_edge_pp": max(
                    [lg.get("edge_pp", 0.0) for lg in card["legs"]] or [0.0]),
                # The single best ENTRY POINT on this market, playable or not,
                # so a "best entry point" question is answerable everywhere.
                "best_entry": _best_entry(card["legs"]),
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
                    "edge_pp": best["edge_pp"],
                    # The basis the gate used, carried so the card can state its
                    # own margin instead of leaving the reader to subtract.
                    "observed_pct": best["observed_pct"],
                    "breakeven_pct": best["breakeven_pct"],
                    "confidence_margin_pp": round(
                        best["confidence"] - best["breakeven_pct"], 2),
                    "raw_edge_pp": round(
                        best["observed_pct"] - best["breakeven_pct"], 2),
                    "entry_digit": (best.get("entry") or {}).get("digit"),
                    "entry_share_pct": (best.get("entry") or {}).get("pct"),
                    "entry_inside": (best.get("entry") or {}).get("inside"),
                    # The entry digit's own Wilson lower bound vs the 10% a
                    # single digit fairs at - the honest sharpness test.
                    "entry_validated": (best.get("entry") or {}).get("validated"),
                    "entry_wilson_lb": (best.get("entry") or {}).get("wilson_lb"),
                    "entry_edge_pp": (best.get("entry") or {}).get("edge_pp"),
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

    def scan_report(self, question: str, symbols: List[str],
                    predictions: Optional[List[dict]] = None,
                    window: int = DEFAULT_WINDOW,
                    symbol: Optional[str] = None,
                    runs: Optional[List[int]] = None) -> dict:
        """List every market with its best entry point and run projections.

        This is the SCAN path, and it is a LISTING. Each market with tape is
        reported with the barrier that reads strongest there, the entry digit
        that barrier points at, the measured win rate, the breakeven its payout
        implies, the payout, and what that win rate compounds to over N runs.

        Nothing is filtered and nothing is advised: an unplayable market is
        listed exactly like a playable one, with its own numbers. There is no
        gate verdict and no recommendation - the board is the answer.
        """
        parsed = parse_request(question)
        preds = predictions if predictions else parsed["predictions"]
        targets = runs or parsed.get("runs_options") or DEFAULT_RUN_OPTIONS
        targets = [t for t in targets if 1 <= t <= MAX_TARGET_RUNS] or list(DEFAULT_RUN_OPTIONS)
        base = {
            "question": question,
            "requested_predictions": preds,
            "target_runs_options": targets,
            "window": window,
            "provider": "deriv_live",
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "SCAN",
        }
        if not preds:
            return {**base, "verdict": "NEED_PREDICTIONS", "rows": [], "board": [],
                    "markets": [], "answer": (
                        "Tell me which barriers to scan - for example "
                        "\"scan all markets, over 4, entry digit, 5 runs\".")}

        scan_symbols = [symbol] if symbol else symbols
        rows = self.scan(scan_symbols, preds, window=window)["probabilities"]
        if not rows:
            return {**base, "verdict": "NO_TAPE", "rows": [], "board": [],
                    "markets": [], "answer": (
                        "No market has live tape to measure yet. Nothing is "
                        "reported until there is real data.")}

        rows.sort(key=lambda r: (r["observed_pct"], r["edge_pp"]), reverse=True)
        markets = self._market_listings(rows, targets)

        # The window is a PROMISE. A freshly reconnected feed holds far fewer
        # ticks than the request, and printing "window 250" over a 63-tick tape
        # would quietly lie about the strength of every number beneath it.
        depths = [r.get("n") or 0 for r in rows]
        window_effective = min(depths) if depths else 0
        window_effective = min(window_effective, window)
        sample_complete = bool(depths) and all((r.get("n") or 0) >= window
                                               for r in rows)

        lines = []
        if sample_complete:
            lines.append(f"{len(markets)} markets with live tape "
                         f"(window {window}). Best entry point per market, "
                         f"{' / '.join(str(t) + '-run' for t in targets)} "
                         f"projection.")
        else:
            # Say the real depth, name the markets that are short, and say it
            # matters - these rates are early reads, not measurements.
            short = sorted({r["symbol"] for r in rows
                            if (r.get("n") or 0) < window})
            lines.append(f"{len(markets)} markets with live tape. Only "
                         f"{window_effective} ticks available (asked for "
                         f"{window}) - the feed is still warming up on "
                         f"{len(short)} market(s), so these rates are EARLY "
                         f"READS, not full measurements.")
        lines.append(f"window_requested {window}, window_effective "
                     f"{window_effective}.")
        for m in markets:
            e = m["best_entry"]
            flag = "" if (m.get("n") or 0) >= window else f" \u00b7 n={m.get('n')} THIN"
            seg = (f"{m['symbol']} \u00b7 {e['side']} {e['barrier']} \u2192 "
                   f"entry digit {e['digit']} ({e['digit_pct']:.1f}%) \u00b7 "
                   f"{e['band_observed_pct']:.1f}% win rate vs "
                   f"{e['breakeven_pct']:.1f}% breakeven ({e['edge_pp']:+.1f}pp) "
                   f"\u00b7 {e['payout']:.2f}x")
            for r in m["runs"]:
                seg += (f" \u00b7 {r['target']} runs {r['p_all_runs'] * 100:.2f}%")
            seg += flag
            lines.append(seg)

        return {
            **base,
            "verdict": "SCAN",
            "rows": rows,
            "board": rows[:MAX_REPORT_ROWS],
            "markets": markets,
            "best_entry": (markets[0]["best_entry"] if markets else None),
            "window_requested": window,
            "window_effective": window_effective,
            "n": window_effective,
            "sample_complete": sample_complete,
            "thin_markets": sorted({r["symbol"] for r in rows
                                    if (r.get("n") or 0) < window}),
            "answer": "\n".join(lines),
        }

    def _market_listings(self, rows: List[dict],
                         targets: List[int]) -> List[dict]:
        """One entry per market: its strongest barrier, entry digit and runs.

        The requested barriers are ranked per market by measured win rate, so
        "best entry point" picks the best of the barriers the user actually
        asked about rather than inventing one they did not. Playability is
        carried on the row as a field rather than used to exclude anything.
        """
        by_symbol: dict = {}
        for r in rows:
            by_symbol.setdefault(r["symbol"], []).append(r)

        out: List[dict] = []
        for sym, rs in by_symbol.items():
            ranked = sorted(
                rs,
                key=lambda r: (r.get("observed_pct") or 0.0,
                               (r.get("entry") or {}).get("pct") or 0.0),
                reverse=True)
            top = ranked[0]
            entry = top.get("entry") or {}
            p_win = (top.get("observed_pct") or 0.0) / 100.0
            out.append({
                "symbol": sym,
                "n": top.get("n"),
                "provider": top.get("provider"),
                "live": bool(top.get("live")),
                "best_entry": {
                    "side": top.get("side"),
                    "barrier": top.get("barrier"),
                    "digit": entry.get("digit"),
                    "digit_pct": entry.get("pct"),
                    "digit_edge_pp": entry.get("edge_pp"),
                    "digit_validated": entry.get("validated"),
                    "band_observed_pct": top.get("observed_pct"),
                    "breakeven_pct": top.get("breakeven_pct"),
                    "edge_pp": top.get("edge_pp"),
                    "ev": top.get("ev"),
                    "payout": top.get("payout"),
                    "playable": bool(top.get("playable")),
                },
                "runs": [{"target": t, **_runs_math(p_win, t)} for t in targets],
                "all_barriers": [{
                    "side": r["side"], "barrier": r["barrier"],
                    "observed_pct": r["observed_pct"],
                    "breakeven_pct": r["breakeven_pct"],
                    "edge_pp": r["edge_pp"], "payout": r["payout"],
                    "entry_digit": (r.get("entry") or {}).get("digit"),
                    "entry_pct": (r.get("entry") or {}).get("pct"),
                    "playable": bool(r.get("playable")),
                } for r in ranked],
            })
        # Rank markets by their measured win rate for the asked barrier - the
        # number the run projection is built from. Ties break on entry-digit
        # frequency.
        out.sort(key=lambda m: (m["best_entry"].get("band_observed_pct") or 0.0,
                                m["best_entry"].get("digit_pct") or 0.0),
                 reverse=True)
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

        # A SCAN is a LISTING. It lists every market with its best entry point
        # and the run projections asked for ("5 or 10 runs"), and it never
        # advises. It must not enter the gated ladder flow, which filters on
        # playability and answers a question the user did not ask ("should I
        # trade?") instead of the one they did ("what are these markets
        # doing?"). Naming run counts does not change that - the projection is
        # a column in the listing. Only an explicit force="plan" builds a ladder.
        wants_scan = parsed["wants_scan_all"] or parsed["wants_entry_digit"]
        if (preds and wants_scan and predictions is None
                and (force is None or force == "scan")):
            named = parsed.get("symbol")
            opts = parsed.get("runs_options") or None
            if named and named in symbols:
                return self.scan_report(question, symbols, preds,
                                        window=window, symbol=named, runs=opts)
            return self.scan_report(question, symbols, preds,
                                    window=window, runs=opts)

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
            # Rank on the margin that DECIDED - the Wilson bound vs breakeven -
            # and report that same number. Printing the raw margin here made
            # the board look like it disagreed with its own verdict.
            rows = sorted(scan["probabilities"],
                          key=lambda r: (r.get("confidence_margin_pp", -99.0),
                                         r["edge_pp"]),
                          reverse=True)
            board = rows[:10]
            floor_txt = (f" against your {floor:.0f}% floor"
                         if floor is not None else "")
            # Publish the board rather than dead-ending: the owner asked to SEE
            # the markets, so the ranking is the answer, not a refusal.
            board_txt = " ".join(
                f"{r['symbol']} {r['side']} {r['barrier']}: "
                f"{r['observed_pct']:.1f}% observed, "
                f"{r['confidence']:.1f}% confidence vs "
                f"{r['breakeven_pct']:.1f}% breakeven "
                f"({r.get('confidence_margin_pp', 0.0):+.1f}pp)."
                for r in board[:5]) if board else ""
            # The single best row on the DECIDING basis, so the headline cannot
            # mix one row's confidence with another row's margin.
            top = board[0] if board else None
            entry_txt = ""
            if top is not None:
                margin = top.get("confidence_margin_pp", 0.0)
                verdict_txt = ("clears" if margin >= MIN_EDGE_PP else
                               "falls short of")
                entry_txt = (
                    f" Best on the board: {top['symbol']} {top['side']} "
                    f"{top['barrier']} - {top['confidence']:.1f}% confidence vs "
                    f"{top['breakeven_pct']:.1f}% breakeven "
                    f"({margin:+.1f}pp), which {verdict_txt} the "
                    f"{MIN_EDGE_PP:.0f}pp margin the gate requires.")
            return {**base, "verdict": "NO_EDGE_FOUND", "candidates": [],
                    "scanned": scan["scanned"],
                    "probabilities": scan["probabilities"],
                    "board": board,
                    "selected": [], "runs": None,
                    "best_entry": _best_of(scan["scanned"]),
                    "answer": (
                        f"I scanned {len(scan['scanned'])} markets for {pred_txt}. "
                        f"No market's confidence lower bound cleared its own "
                        f"breakeven by the {MIN_EDGE_PP:.0f}pp margin the gate "
                        f"requires{floor_txt}, so there is no edge to take. "
                        f"A strong raw rate that does not survive its confidence "
                        f"bound is variance, not edge - the two are shown "
                        f"separately below. Here is the board: "
                        f"{board_txt}{entry_txt} "
                        "That is the full measurement - the markets are all "
                        "listed above, so you can see exactly what each one is "
                        "doing.")}

        probs = [c["play"]["confidence"] / 100.0 for c in selected]
        runs_math = _runs_math(min(probs), runs) if probs else None

        verdict = ("FULL_LADDER" if len(selected) >= runs
                   else "PARTIAL_LADDER")

        gate_txt = (f"at {floor:.0f}%+ confidence" if floor is not None
                    else "each clearing its own breakeven by a real margin")
        if len(live) == 1 and floor is None:
            gate_txt = "clearing its own breakeven by a real margin"
        lines = [
            f"I found {len(live)} market"
            f"{'s' if len(live) != 1 else ''} supporting {pred_txt} {gate_txt}."
        ]
        for i, c in enumerate(selected, 1):
            pl = c["play"]
            entry_txt = ""
            if pl.get("entry_digit") is not None:
                entry_txt = f", entry digit {pl['entry_digit']}"
                if pl.get("entry_validated") is False:
                    entry_txt += " (not itself validated)"
            lines.append(
                f"Run {i}: {c['symbol']} - {pl['side']} {pl['barrier']}"
                f"{entry_txt} - {pl['confidence']:.1f}% confidence vs "
                f"{pl['breakeven_pct']:.1f}% breakeven "
                f"({pl.get('confidence_margin_pp', 0.0):+.1f}pp), "
                f"{pl['payout']:.2f}x, EV {pl['ev']:+.3f}/$.")
        if len(selected) < runs:
            lines.append(
                f"That is {len(selected)} run"
                f"{'s' if len(selected) != 1 else ''} available, not the {runs} "
                "you asked for - the other markets did not clear it.")
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
