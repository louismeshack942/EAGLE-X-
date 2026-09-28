"""The Sniper - three named targets, one shot, one run.

A special department, kept deliberately narrow. It scans ONLY:

  1. MATCHES      - an exact digit, priced at 10x, fair at 10%.
  2. OVER 4       - wins 5..9 (5 digits), priced at 2x, fair at 50%.
  3. UNDER 6      - wins 0..5 (6 digits), priced at 1.67x, fair at 60%.

Nothing else. No ladder, no martingale, no hedging, no second shot. One pull
of the trigger is one run, and the card states exactly what that single run
pays and how often the tape says it lands.

A shot is only taken when the target survives ALL of the following, and the
card always shows which rule decided it:

  * the barrier must be more than a one-window fluke - its edge has to hold
    across 100 / 250 / 1000 ticks, and a window that cannot be filled is not
    evidence;
  * the sample must be deep enough to mean anything;
  * MATCHES must clear its own 10% fair share on the WILSON LOWER BOUND, not
    on a raw frequency that a short tape can manufacture;
  * OVER 4 / UNDER 6 must clear their own breakeven on both the raw rate and
    the Wilson lower bound, with the standard edge margin - a contract that
    only wins on the lucky side of its own confidence interval is not a shot.

This is the honest part, and it is the whole point of a sniper: most of the
time there is no shot. The department reports HOLDING_FIRE with the measured
numbers rather than inventing a target. Every kill it does take is a single
run whose win probability came off the live tape.

Advisory only - like every other service here, it arms a card and places
nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.services.cockpit import (
    DIGIT_FAIR_PCT,
    MIN_EDGE_PP,
    barrier_row,
    entry_digit,
    live_digit_counts,
    wilson_lb,
)

logger = logging.getLogger(__name__)

# The three targets the department is cleared to engage. Side/barrier pairs;
# MATCHES carries an `exact` flag because it is a single-digit contract, not a
# band.
TARGETS = (
    {"key": "MATCHES", "side": "MATCHES", "barrier": None, "exact": True},
    {"key": "OVER_4", "side": "OVER", "barrier": 4, "exact": False},
    {"key": "UNDER_6", "side": "UNDER", "barrier": 6, "exact": False},
)

# One shot, one run. These are constants, not options - the department does not
# scale in and does not fire twice.
SHOT_RUNS = 1
SHOT_STAKE = 1.0

# The windows a target's edge must survive. A single window flukes; this is the
# rule that killed the phantom-edge session.
CONFIRM_WINDOWS = (100, 250, 1000)
# Below this the tape cannot support a verdict at all.
MIN_SAMPLE = 100
# A band must beat its own breakeven by this much, on BOTH the raw rate and the
# Wilson lower bound. Mirrors the cockpit gate.
SNIPER_MIN_EDGE_PP = MIN_EDGE_PP
# MATCHES must beat its own fair share by this much on the Wilson lower bound.
MATCHES_MIN_EDGE_PP = 1.0

# How much room the Wilson lower bound has over the target's OWN breakeven -
# i.e. how much of the measured edge survives the worst case. A kill at 1pp is
# a digit a hair above fair; a kill at 10pp is a target that holds even if the
# tape is unkind. Reported as a tier so a marginal shot is never read as a
# certainty.
TIER_SNIPER_PP = 10.0
TIER_SOLID_PP = 5.0


def _tier(lb_margin_pp: float) -> str:
    """Grade a kill by how much edge survives the confidence interval."""
    if lb_margin_pp >= TIER_SNIPER_PP:
        return "SNIPER"
    if lb_margin_pp >= TIER_SOLID_PP:
        return "SOLID"
    return "MARGINAL"


def _matches_row(counts: List[int], n: int, digit: int) -> dict:
    """One MATCHES target: digit `digit` at 10x, fair at 10%.

    Priced on the same 10/winning-digit basis as every other contract, so
    MATCHES on one digit is 10x and breaks even at 10%. The raw frequency a
    short tape produces is reported, but the VERDICT rides on the Wilson lower
    bound - that is the number that cannot be manufactured by luck.
    """
    c = counts[digit] if 0 <= digit <= 9 else 0
    p = c / n if n else 0.0
    lb = wilson_lb(p, n) * 100.0
    be = 100.0 / 10.0            # 10x payout -> 10% breakeven
    edge = p * 100 - DIGIT_FAIR_PCT
    return {
        "side": "MATCHES", "digit": digit, "barrier": digit,
        "winning_digits": 1,
        "payout": 10.0,
        "observed_pct": round(p * 100, 2),
        "wilson_lb_pct": round(lb, 2),
        "breakeven_pct": be,
        "edge_pp": round(edge, 2),
        "wilson_edge_pp": round(lb - be, 2),
        "ev": round(p * 10.0 - 1.0, 4),
        "sample": n,
        "entry": {
            "digit": digit, "pct": round(p * 100, 2),
            "wilson_lb": round(lb, 2), "fair_pct": DIGIT_FAIR_PCT,
            "edge_pp": round(edge, 2),
            "validated": lb > DIGIT_FAIR_PCT,
            "inside": lb > DIGIT_FAIR_PCT,
            "available": True,
        },
    }


def _shot_math(p_win: float, payout: float,
               stake: float = SHOT_STAKE) -> dict:
    """What ONE run costs, pays and expects.

    p_miss is stated plainly: a sniper card that only showed the win case would
    be advertising, not reporting.
    """
    p = max(0.0, min(1.0, p_win))
    return {
        "runs": SHOT_RUNS,
        "stake": round(stake, 2),
        "payout": round(payout, 4),
        "p_win": round(p, 4),
        "p_miss": round(1.0 - p, 4),
        "win_credit": round(stake * payout - stake, 2),
        "loss_debit": round(stake, 2),
        "ev_per_run": round(p * payout - 1.0, 4),
        "ev_dollars": round((p * payout - 1.0) * stake, 4),
        "break_even_pct": round(100.0 / payout, 2) if payout else None,
    }


class Sniper:
    """The special department. Scans three targets, fires once, reports honestly."""

    def _engage(self, symbol: str, target: dict) -> Optional[dict]:
        """Measure one target on one market. Returns None when unusable."""
        side = target["side"]
        exact = target["exact"]

        counts, n = live_digit_counts(symbol, max(CONFIRM_WINDOWS))
        if n <= 0:
            return None

        if exact:
            # MATCHES is a digit lottery: every digit is a candidate, and the
            # shot is taken at whichever digit the tape overweights most.
            rows = [_matches_row(counts, n, d) for d in range(10)]
            best = max(rows, key=lambda r: (r["wilson_lb_pct"], r["observed_pct"]))
            row = best
        else:
            row = barrier_row(counts, n, side, int(target["barrier"]))
            if row is None:
                return None
            row = dict(row)
            row["entry"] = entry_digit(counts, n, side, int(target["barrier"]))

        # Per-window re-measure of the SAME target - a fluke at one window
        # length is not a target.
        windows = []
        for w in CONFIRM_WINDOWS:
            cw, wn = live_digit_counts(symbol, w)
            satisfied = wn >= w
            if wn <= 0:
                windows.append({"window": w, "satisfied": False,
                                "observed_pct": None, "edge_pp": None})
                continue
            if exact:
                d = row["digit"]
                wr = _matches_row(cw, wn, d)
                windows.append({
                    "window": w, "satisfied": satisfied, "n": wn,
                    "observed_pct": wr["observed_pct"],
                    "edge_pp": wr["edge_pp"],
                    "wilson_lb_pct": wr["wilson_lb_pct"],
                })
            else:
                wrow = barrier_row(cw, wn, side, int(target["barrier"]))
                windows.append({
                    "window": w, "satisfied": satisfied, "n": wn,
                    "observed_pct": wrow["observed_pct"] if wrow else None,
                    "edge_pp": wrow["edge_pp"] if wrow else None,
                    "wilson_lb_pct": (
                        round(wrow["confidence"], 2) if wrow else None),
                })

        filled = [w for w in windows
                  if w["satisfied"] and w.get("edge_pp") is not None]
        stable = (len(filled) >= 2
                  and all(w["edge_pp"] > 0 for w in filled))

        # The gate. Every rule must pass; there is no scoring that overrides it.
        if exact:
            raw_ok = row["edge_pp"] >= MATCHES_MIN_EDGE_PP
            lb_ok = row["wilson_edge_pp"] >= MATCHES_MIN_EDGE_PP
            # The margin that SURVIVES the confidence interval.
            lb_margin = row["wilson_edge_pp"]
            gate = "MATCHES: Wilson lower bound above the 10% fair share"
            required_pp = MATCHES_MIN_EDGE_PP
        else:
            raw_ok = row["edge_pp"] >= SNIPER_MIN_EDGE_PP
            lb_margin = row["confidence"] - row["breakeven_pct"]
            lb_ok = lb_margin >= SNIPER_MIN_EDGE_PP
            gate = (f"{side} {target['barrier']}: Wilson lower bound above "
                    f"breakeven by {SNIPER_MIN_EDGE_PP}pp")
            required_pp = SNIPER_MIN_EDGE_PP

        sample_ok = n >= MIN_SAMPLE
        ev_ok = row["ev"] > 0
        kill = bool(sample_ok and raw_ok and lb_ok and ev_ok and stable)

        shot = _shot_math(row["observed_pct"] / 100.0, row["payout"])

        return {
            "target": target["key"],
            "side": side,
            "barrier": target["barrier"],
            "symbol": symbol,
            "n": n,
            "killed": kill,
            "gate": gate,
            "required_edge_pp": required_pp,
            "wilson_margin_pp": round(lb_margin, 2),
            "tier": _tier(lb_margin) if kill else None,
            "rules": {
                "sample_ok": sample_ok,
                "raw_edge_ok": bool(raw_ok),
                "wilson_edge_ok": bool(lb_ok),
                "positive_ev": bool(ev_ok),
                "multi_window_stable": bool(stable),
            },
            "windows": windows,
            "row": row,
            "entry": row.get("entry"),
            "one_shot": shot,
        }

    def scan(self, symbols: List[str]) -> dict:
        """Engage all three targets across the markets. One shot per target.

        Returns the full board even when nothing qualifies - a department that
        goes silent is worse than one that reports "no shot".
        """
        ts = datetime.now(timezone.utc).isoformat()
        targets_scan: List[dict] = []
        for target in TARGETS:
            best: Optional[dict] = None
            board: List[dict] = []
            for sym in symbols:
                try:
                    r = self._engage(sym, target)
                except Exception as exc:  # a bad market must not stop the scan
                    logger.warning("sniper scan failed for %s %s: %s",
                                   sym, target["key"], exc)
                    continue
                if r is None:
                    continue
                board.append(r)
                if best is None or (
                        r["row"]["observed_pct"], r["row"]["edge_pp"]
                ) > (best["row"]["observed_pct"], best["row"]["edge_pp"]):
                    best = r
            kills = [b for b in board if b["killed"]]
            # The shot is the best KILL, not the best row: a target only fires
            # when one of its markets passed every rule.
            shot_src = None
            if kills:
                shot_src = max(
                    kills,
                    key=lambda r: (r["row"]["observed_pct"], r["row"]["edge_pp"]))
            targets_scan.append({
                "target": target["key"],
                "side": target["side"],
                "barrier": target["barrier"],
                "markets_scanned": len(board),
                "kills": len(kills),
                "acquired": shot_src is not None,
                "shot": shot_src,
                "board": sorted(
                    board,
                    key=lambda r: (r["row"]["observed_pct"], r["row"]["edge_pp"]),
                    reverse=True),
                "best": best,
            })

        acquired = [t for t in targets_scan if t["acquired"]]
        if not symbols:
            verdict = "NO_TARGET"
            note = "No markets are being streamed. Nothing to scan."
        elif not any(t["markets_scanned"] for t in targets_scan):
            verdict = "NO_TARGET"
            note = ("No target has live tape. Nothing is called until there is "
                    "real data.")
        elif not acquired:
            verdict = "HOLDING_FIRE"
            note = ("All three targets measured; none survived every rule. No "
                    "shot is taken - a single run on an unproven target is how "
                    "a session bleeds.")
        else:
            verdict = "TARGET_ACQUIRED"
            note = (f"{len(acquired)} of {len(TARGETS)} targets acquired. "
                    f"One run each, then done.")

        return {
            "department": "SNIPER",
            "doctrine": ("Three targets only. One shot, one run, then done. "
                         "No ladder, no hedging, no second shot."),
            "mission": [t["key"] for t in TARGETS],
            "shot_runs": SHOT_RUNS,
            "stake": SHOT_STAKE,
            "min_edge_pp": SNIPER_MIN_EDGE_PP,
            "matches_min_edge_pp": MATCHES_MIN_EDGE_PP,
            "confirm_windows": list(CONFIRM_WINDOWS),
            "verdict": verdict,
            "note": note,
            "acquired": [t["target"] for t in acquired],
            "targets": targets_scan,
            "ts": ts,
        }

    def card(self, symbols: List[str]) -> dict:
        """The one-screen card: the shot on each target, or why it held fire."""
        scan = self.scan(symbols)
        lines = [f"SNIPER \u00b7 {scan['verdict']} \u00b7 {scan['note']}"]
        for t in scan["targets"]:
            if t["acquired"]:
                s = t["shot"]
                shot = s["one_shot"]
                if s["side"] == "MATCHES":
                    call = f"MATCHES digit {s['entry']['digit']}"
                else:
                    call = (f"{s['side']} {s['barrier']} "
                            f"\u2192 entry digit {s['entry']['digit']}")
                lines.append(
                    f"{t['target']} \u2192 FIRE {s['symbol']} \u00b7 {call} "
                    f"at {shot['payout']:.2f}x \u00b7 {shot['p_win'] * 100:.1f}% to land "
                    f"\u00b7 1 run, ${shot['stake']:.2f} \u00b7 EV {shot['ev_per_run']:+.3f} "
                    f"\u00b7 {s['tier']} ({s['wilson_margin_pp']:+.1f}pp worst case)")
            else:
                b = t["best"]
                if b:
                    lines.append(
                        f"{t['target']} \u2192 HOLD \u00b7 best "
                        f"{b['symbol']} at {b['row']['observed_pct']:.1f}% "
                        f"({b['row']['edge_pp']:+.1f}pp) \u00b7 "
                        f"failed: " + ", ".join(
                            k for k, v in b["rules"].items() if not v))
                else:
                    lines.append(f"{t['target']} \u2192 NO TAPE")
        return {**scan, "card": " | ".join(lines)}


sniper = Sniper()
