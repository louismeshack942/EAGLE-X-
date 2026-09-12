"""Edge Cockpit - one glance answers "is there a trade here, right now?"

Draws the existing brains(market master, digit engine, super ensemble) into a
single decision card for one symbol/window/family. It observes the three headline
numbers(observed vs breakeven vs EV), a plain-English line,,per-family edge
ladder, andreusable place/scheme payloads. Advisory only - no trades..
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FAMILIES = ["ODD", "EVEN", "MATCHES", "DIFFERS", "OVER", "UNDER"]
WINDOWS = [100, 250, 1000]
MIN_OBSERVED_PCT = 51.0
MIN_EDGE_PCT = 3.0
MIN_EV = 0.0
BREAKEVEN_MARGIN = 1.0
_TICK_DIR = Path(__file__).resolve().parent / "data" / "ticks"


class CockpitEngine:
    def __init__(self) -> None:
        self._market_master = None
        self._digit_engine = None
        self._super_engine = None

    def _brains(self) -> Tuple:


        if self._market_master is None:
            from app.services.market_master import market_master
            from app.services.analytics_advanced import digit_engine
            from app.services.super_profit import super_profit_engine
            self._market_master, self._digit_engine, self._super_engine = (
                market_master, digit_engine, super_profit_engine)
        return self._market_master, self._digit_engine, self._super_engine

    def _super_decision(self, symbol: str) -> Optional[dict]:
        try:
            return self._super_engine.decide(symbol, user_id="default")
        except Exception:
            logger.exception("cockpit super decide failed for %s", symbol)
            return None

    def _explain(self, family: str, observed: float, breakeven: float, ev: float, windows_ok: bool) -> str:

        if ev > MIN_EV and windows_ok:
            return (
                f"{family} {observed:.1f}% vs fair {breakeven:.1f}% - clears its own "
                f"breakeven by {observed - breakeven:.1f}pp; EV +{ev * 100:.2f}% per dollar. Real small edge."
            )
        if windows_ok:
            spread = observed - breakeven

            return (
                f"{family} {observed:.1f}% needs {breakeven:.1f}% to break even - only "
                f"{spread:+.1f}pp, EV {ev * 100:+.2f}per cent. Stand down."
            )
        return (
            f"{family} {observed:.1f}% needs {breakeven:.1f}%to break even - window tape is "
            f"inconsistent(100/250/1000 disagree), so no proven edge. NO TRADE."
        )

    def ladder(self, symbol: str) -> Dict[str, List[dict]]:


        mm, _, _ = self._brains()
        rows: Dict[str, List[dict]] = {}
        for fam in FAMILIES:

            rows[fam] = []
        for window in WINDOWS:
            try:
                board = mm.analyze(symbol, window)
            except Exception:
                logger.warning("cockpit ladder failed %s w=%s", symbol, window)
                continue
            seen: Dict[str, dict] = {}
            for c in board.get("all_contracts", []):
                fam = c.get("type")
                if fam not in FAMILIES or fam in seen:
                    continue
                payout = c.get("payout") or 1
                breakeven = (100.0 / payout) if payout else 50.0
                seen[fam] = {
                    "family": fam,
                    "name": c.get("name") or fam,
                    "window": window,
                    "observed_pct": c.get("observed_pct"),
                    "breakeven_pct": round(breakeven, 2),
                    "edge_pp": round((c.get("observed_pct") or 0) - breakeven, 2),
                    "z": c.get("z"),
                    "significant": bool(c.get("significant")),
                    "ev": c.get("ev"),
                    "sample_n": c.get("sample_n"),
                }
            for fam, entry in seen.items():
                rows[fam].append(entry)
        return rows

    def decision(self, symbol: str, window: int = 250, family: str = "EVEN",
                  duration: str = "5t", stake: float = 1.0) -> dict:



        window = int(window)
        family_upper = (family or "EVEN").upper()
        if family not in FAMILIES:

            family = "EVEN"
        mm, digit_engine, _ = self._brains()

        window_proof: List[dict] = []
        candidate = None
        for w in WINDOWS:
            try:
                ca = digit_engine.get_contract_analysis(symbol, mode=family, window=w)
            except Exception:
                logger.warning("cockpit contract analysis failed %s w=%s", symbol, w)
                ca = {}
            modes = ca.get("modes") or {}
            mo = modes.get(family)
            if mo:
                window_proof.append({
                    "window": w,
                    "observed": mo.get("observed"),
                    "deviation": mo.get("deviation"),
                })
            if candidate is None:
                candidate = ca.get("candidate")


        board = {}
        try:
            board = mm.analyze(symbol, window)
        except Exception:
            logger.warning("cockpit market board failed %s w=%s", symbol, window)
        best = None
        for c in board.get("all_contracts", []):
            if (c.get("type") or "").upper() == family:
                best = c
                break
        if best is None:
            best = board.get("top_recommendation")


        if best is None:
            return {
                "symbol": symbol,
                "window": window,
                "family": family,
                "duration": duration,
                "verdict": "FAIR",
                "top": None,
                "explain": f"No {family} contracts on the board for {symbol} right now. Stand down.",
                "gates": {"data_quality": False, "windows_ok": False, "significant": False, "breakeven_ok": False, "ev_ok": False, "edge_ok": False, "super": False},
                "failed_gates": ["data_quality", "windows_ok", "significant", "breakeven_ok", "ev_ok", "edge_ok", "super"],
                "super": {"final": None, "contract": None, "ev": None, "failed_gates": []},
                "ladder": self.ladder(symbol),
                "window_proof": [],
                "place_payload": None,
                "scheme_entry": None,
                "data_quality": 0,
                "signal": "",
                "anomaly_count": 0,
            }


        observed = best.get("observed_pct") if best else None
        fair = best.get("fair_pct") if best else None
        payout = best.get("payout") if best else None
        breakeven = (100.0 / payout) if payout else (fair or 50.0)
        ev = best.get("ev")
        edge_raw = (observed or fair or 0.0) - breakeven
        edge_pp = round(edge_raw, 2) if observed else None
        significant = bool(best.get("significant"))
        z = best.get("z")
        data_quality = board.get("data_quality") or 0
        anomaly_count = board.get("anomaly_count") or 0
        signal = board.get("signal") or ""

        ok_windows = [p for p in window_proof if (p.get("observed") or 0) >= MIN_OBSERVED_PCT]
        windows_ok = len(ok_windows) >= 2
        all_windows_seen = (len(window_proof) == len(WINDOWS))
        if all_windows_seen and (len(ok_windows) == len(WINDOWS)):
            windows_ok = True




        super_d = self._super_decision(symbol)
        super_final = (super_d or {}).get("final")
        super_final_str = str(super_final).upper() if super_final else "" 
        super_final_ok = ("EXECUTE" in super_final_str) or ("STRIKE" in super_final_str)
        failed_gates = (super_d or {}).get("failed_gates", [])


        gates_ok = {
            "data_quality": data_quality >= 70,
            "windows_ok": windows_ok,
            "significant": significant,
            "breakeven_ok": observed is not None and observed >= (breakeven + BREAKEVEN_MARGIN),
            "ev_ok": (ev is not None)and (ev > MIN_EV),
            "edge_ok":(edge_pp is not None)and (edge_pp >= MIN_EDGE_PCT),
            "super": super_final_ok,
        }
        failed = [k for k, v in gates_ok.items() if not v]
        verdict = "EDGE" if not failed else ("TRAP" if significant and (observed is not None)and (observed < breakeven) else "FAIR")
        if not windows_ok:
            verdict = "FAIR"

        dur_num, dur_unit = self._parse_duration(duration)
        place_payload = None
        if best is not None:
            place_payload = {
                "symbol": symbol,
                "contract_type": self._deriv_contract_type(family, best.get("digit")),
                "duration": dur_num,
                "duration_unit": dur_unit,
                "amount": round(stake, 2),
                "currency": "USD",
                "digit": best.get("digit"),
            }

        scheme_entry = None
        if verdict == "EDGE" and best is not None:
            scheme_entry = {
                "symbol": symbol,
                "contract_type": self._deriv_contract_type(family, best.get("digit")),
                "candidate": best.get("digit"),
                "stake": round(stake, 2),
                "duration_ticks": dur_num if dur_unit == "t" else None,
                "duration_seconds": dur_num if dur_unit == "s" else None,
                "direction": family,
                "derived_at": datetime.now(timezone.utc).isoformat(),
                "evidence": {
                    "observed_pct": round(observed, 2) if observed is not None else None,
                    "fair_pct": round(fair,  2) if fair is not None else None,
                    "breakeven_pct": round(breakeven,  2),
                    "edge_pp": edge_pp,
                    "ev": ev,
                    "z": z,
                    "significant": significant,
                    "windows_ok": window_proof,
                    "super_final": super_final,
                },
                "eaglex_gate": "STRIKE",
            }

        return {
            "symbol": symbol,
            "window": window,
            "family": family,
            "duration": duration,
            "verdict": verdict,
            "top": {
                "name": best.get("name") if best else None,
                "family": family,
                "digit": best.get("digit") if best else None,
                "observed_pct": observed,
                "fair_pct": fair,
                "breakeven_pct": round(breakeven, 2),
                "edge_pp": edge_pp,
                "ev": ev,
                "z": z,
                "significant": significant,
                "sample_n": best.get("sample_n") if best else None,
                "payout": payout,
            },
            "explain": self._explain(family, observed or (fair or 0.0), breakeven, ev or  0.0, windows_ok),
            "gates": gates_ok,
            "failed_gates": failed,
            "super": {
                "final": super_final,
                "contract": super_d.get("contract") if super_d else None,
                "ev": super_d.get("ev") if super_d else None,
                "failed_gates": failed_gates,
            },
            "ladder": self.ladder(symbol),
            "window_proof": window_proof,
            "place_payload": place_payload,
            "scheme_entry": scheme_entry,
            "data_quality": data_quality,
            "signal": signal,
            "anomaly_count": anomaly_count,
        }


    def predict(self, symbol: str, window: int = 250, duration: str = "5t", stake: float = 1.0) -> dict:
        """Separate space for OVER / UNDER predictions plus an entry read.
        Advisory only - reuses the same gates as decision() but never fires."""
        family = "OVER"
        over = self.decision(symbol, window=window, family="OVER", duration=duration, stake=stake)
        under = self.decision(symbol, window=window, family="UNDER", duration=duration, stake=stake)
        entry = self.decision(symbol, window=window, family=family, duration=duration, stake=stake)
        tape = self._load_tape(symbol)
        last = tape[-1] if tape else None
        last_quote = last.get("quote") if last else None

        def _side(side_payload: dict, side: str) -> dict:
            top = side_payload.get("top") or {}
            ber = top.get("breakeven_pct")
            obs = top.get("observed_pct")
            ev = top.get("ev")
            edge = top.get("edge_pp")
            return {
                "family": side,
                "verdict": side_payload.get("verdict"),
                "observed_pct": obs,
                "breakeven_pct": ber,
                "edge_pp": edge,
                "ev": ev,
                "significant": bool(top.get("significant")),
                "z": top.get("z"),
                "digit": top.get("digit"),
                "sample_n": top.get("sample_n"),
                "playable": side_payload.get("verdict") == "EDGE" and bool(ev and ev > 0),
                "failed_gates": side_payload.get("failed_gates") or [],
            }

        over = _side(over, "OVER")
        under = _side(under, "UNDER")
        scheme = entry.get("scheme_entry")

        return {
            "symbol": symbol,
            "window": window,
            "duration": duration,
            "stake": stake,
            "over": over,
            "under": under,
            "entry": {
                "available": bool(scheme),
                "direction": (scheme or {}).get("direction"),
                "contract_type": (scheme or {}).get("contract_type"),
                "candidate": (scheme or {}).get("candidate"),
                "stake": (scheme or {}).get("stake"),
                "duration_ticks": (scheme or {}).get("duration_ticks"),
                "last_quote": last_quote,
                "edge_pp": (scheme or {}).get("evidence", {}).get("edge_pp"),
                "ev": (scheme or {}).get("evidence", {}).get("ev"),
                "explain": entry.get("explain") if not scheme else
                    f"Entry available: {scheme.get('direction')} on {scheme.get('candidate')} "
                    f"at {scheme.get('stake')} USD.",
            },
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def fbi(self, symbol: str, window: int = 250, duration: str = "5t", stake: float = 1.0) -> dict:
        """FBI final verdict - per-digit OVER/UNDER probabilities plus an ENTRY digit.

        Turns a coarse "OVER 5" into evidence-backed, per-digit confidence:
        for every barrier d in 0..9 it returns P(next digit > d) and
        P(next digit < d) with Wilson lower-bound confidence, breakeven-aware
        edge (pp) and EV, then ranks the strongest OVER / UNDER plays and the
        best single entry digit. Advisory only - never fires.
        """
        from math import sqrt
        from app.services.analytics_advanced import digit_engine

        window = max(20, int(window))
        analysis = digit_engine.get_digit_analysis(symbol, window=window) or {}
        freq = analysis.get("frequency") or {}
        n = int(analysis.get("n") or 0)
        if n <= 0:
            return {
                "symbol": symbol, "window": window, "duration": duration,
                "verdict": "FAIR", "reason": "no tape", "n": 0,
                "confidence_over": {"side": "OVER", "digit": None, "confidence": 0.0,
                                    "observed_pct": 0.0, "breakeven_pct": 0.0,
                                    "edge_pp": 0.0, "ev": 0.0, "playable": False},
                "confidence_under": {"side": "UNDER", "digit": None, "confidence": 0.0,
                                     "observed_pct": 0.0, "breakeven_pct": 0.0,
                                     "edge_pp": 0.0, "ev": 0.0, "playable": False},
                "entry": {"side": None, "digit": None, "confidence": 0.0,
                          "observed_pct": 0.0, "breakeven_pct": 0.0,
                          "edge_pp": 0.0, "ev": 0.0, "available": False},
                "over_digits": [], "under_digits": [], "ranked": [],
            }

        def wilson_lb(p: float, n_: int) -> float:
            # Wilson score lower bound (95%), two-sided z=1.96
            z = 1.96
            phat = p
            denom = 1 + z * z / n_
            center = (phat + z * z / (2 * n_)) / denom
            margin = z * sqrt((phat * (1 - phat) + z * z / (4 * n_)) / n_) / denom
            return max(0.0, min(1.0, center - margin))

        # Per-digit Bayes-shrunk estimate (trustworthy share out of 10)
        est = [float(freq.get(str(d), {}).get("estimate", 10.0)) / 100.0 for d in range(10)]
        # Raw observed share (for the cumulative sums below use counts when available)
        counts = [int(freq.get(str(d), {}).get("count", 0)) for d in range(10)]
        if sum(counts) == 0:
            counts = [int(round(e * n)) for e in est]

        over_rows = []
        under_rows = []
        for d in range(10):
            # P(digit > d) and P(digit < d) from observed digits
            ob_over = sum(counts[k] for k in range(d + 1, 10)) / n
            ob_under = sum(counts[k] for k in range(0, d)) / n
            # breakeven for OVER barrier d (digits d+1..9 => 9-d-1+1 = 9-d outcomes)
            n_over = 9 - d
            n_under = d
            payout_over = 10.0 / n_over if n_over else 10.0    # 1 => 10.0, 8=>1.25...
            payout_under = 10.0 / n_under if n_under else 10.0
            be_over = 100.0 / payout_over
            be_under = 100.0 / payout_under
            lb_over = wilson_lb(ob_over, n) * 100.0
            lb_under = wilson_lb(ob_under, n) * 100.0
            ev_over = (ob_over * payout_over) - 1.0
            ev_under = (ob_under * payout_under) - 1.0
            over_rows.append({
                "digit": d, "barrier": d,
                "observed_pct": round(ob_over * 100, 2),
                "wilson_lb": round(lb_over, 2),
                "breakeven_pct": round(be_over, 2),
                "edge_pp": round(ob_over * 100 - be_over, 2),
                "ev": round(ev_over, 4),
                "playable": lb_over > be_over and ev_over > 0,
            })
            under_rows.append({
                "digit": d, "barrier": d,
                "observed_pct": round(ob_under * 100, 2),
                "wilson_lb": round(lb_under, 2),
                "breakeven_pct": round(be_under, 2),
                "edge_pp": round(ob_under * 100 - be_under, 2),
                "ev": round(ev_under, 4),
                "playable": lb_under > be_under and ev_under > 0,
            })

        playable_over = [r for r in over_rows if r["playable"]]
        playable_under = [r for r in under_rows if r["playable"]]
        best_over = max(playable_over, key=lambda r: r["wilson_lb"]) if playable_over else None
        best_under = max(playable_under, key=lambda r: r["wilson_lb"]) if playable_under else None

        # ENTRY: single digit with the best EV across both families; tie-break by edge_pp
        entry_pool = playable_over + playable_under
        best_entry = max(entry_pool, key=lambda r: (r["ev"], r["edge_pp"])) if entry_pool else None

        def fin(rows: List[dict], side: str) -> dict:
            r = max(rows, key=lambda x: x["wilson_lb"]) if rows else None
            return {
                "side": side,
                "digit": r["digit"] if r else None,
                "confidence": r["wilson_lb"] if r else 0.0,
                "observed_pct": r["observed_pct"] if r else 0.0,
                "breakeven_pct": r["breakeven_pct"] if r else 0.0,
                "edge_pp": r["edge_pp"] if r else 0.0,
                "ev": r["ev"] if r else 0.0,
                "playable": bool(r),
                "top": r if r else None,
            }

        over_fin = fin(playable_over, "OVER")
        under_fin = fin(playable_under, "UNDER")
        entry_fin = {
            "side": ("OVER" if best_entry and best_entry in over_rows else "UNDER") if best_entry else None,
            "digit": best_entry["digit"] if best_entry else None,
            "confidence": best_entry["wilson_lb"] if best_entry else 0.0,
            "observed_pct": best_entry["observed_pct"] if best_entry else 0.0,
            "breakeven_pct": best_entry["breakeven_pct"] if best_entry else 0.0,
            "edge_pp": best_entry["edge_pp"] if best_entry else 0.0,
            "ev": best_entry["ev"] if best_entry else 0.0,
            "available": bool(best_entry),
        }
        reason = (
            f"Tape n={n} · best OVER {over_fin['digit']} @ {over_fin['confidence']:.1f}% · "
            f"best UNDER {under_fin['digit']} @ {under_fin['confidence']:.1f}% · "
            f"ENTRY digit {entry_fin['digit']} "
            + (f"({entry_fin['side']}, EV {entry_fin['ev']:+.3f}/$)" if entry_fin["available"] else "(no edge yet)")
        )
        verdict = "EDGE" if (over_fin["playable"] or under_fin["playable"]) else "FAIR"

        ranked = sorted(
            [dict(r, side="OVER") for r in over_rows if r["playable"]] +
            [dict(r, side="UNDER") for r in under_rows if r["playable"]],
            key=lambda r: r["wilson_lb"], reverse=True,
        )

        return {
            "symbol": symbol, "window": window, "duration": duration, "stake": stake,
            "verdict": verdict, "reason": reason, "n": n,
            "confidence_over": over_fin, "confidence_under": under_fin,
            "entry": entry_fin, "over_digits": over_rows, "under_digits": under_rows,
            "ranked": ranked,
        }

    @staticmethod
    def _parse_duration(duration: str) -> Tuple[int, str]:


        try:
            num = int("".join(ch for ch in duration if ch.isdigit())) or "0"
            unit = "".join(ch for ch in duration if ch.isalpha()).lower() or "t"
            if unit == "m":
                num, unit = num * 60,"s"
            elif unit not in ("t", "s"):
                unit = "t"
            return max(1, num), unit
        except Exception:
            return 5,"t"


    @staticmethod
    def _deriv_contract_type(family: str, digit: Optional[int]) -> str:


        mapping = {
            "ODD": "DIGITODD",
            "EVEN": "DIGITEVEN",
            "MATCHES": "DIGITMATCH",
            "DIFFERS": "DIGITDIFF",
            "OVER": "DIGITOVER",
            "UNDER": "DIGITUNDER",
        }
        return mapping.get(family, family)


    def backtest(self, symbol: str, family: str = "EVEN", window: int = 250,
                  duration: str = "5t", stake: float =  1.0) -> dict:




        ticks = self._load_tape(symbol)

        if len(ticks) < 200:
            return {
                "symbol": symbol,
                "note": f"only {len(ticks)} recorded ticks; need at least 200 for an honest replay",
                "trades": [],
                "win_rate": None,
            }

        family = family.upper()
        dur_num, dur_unit = self._parse_duration(duration)
        results: List[dict] = []
        wins =  0
        losses =  0
        longest_loss_streak =  0
        cur_loss_streak =  0
        equity =  0.0
        best_hour: Dict[str, float] = {}


        i = window
        while i < len(ticks) - dur_num:
            tape = ticks[i - window:i]
            digits = [t.get("digit") for t in tape if t.get("digit") is not None]
            if len(digits) < max(50, window // 2):
                i += window //  2
                continue
            observed = sum(1 for d in digits if self._digit_hits(d, family)()) / len(digits) *  100.0
            breakeven =  50.0 if family in ("ODD", "EVEN") else 70.0
            if observed < breakeven + MIN_EDGE_PCT:
                i += window //  2
                continue
            opens = i
            closes = i + dur_num
            if closes >= len(ticks):
                break
            outcome = ticks[opens +  1:closes + 1]
            hits = [t.get("digit") for t in outcome if t.get("digit") is not None]
            win = any(self._digit_hits(h, family) for h in hits) if hits else False
            wins += int(win)
            losses += int(not win)
            cur_loss_streak = cur_loss_streak + 1 if not win else  0
            longest_loss_streak = max(longest_loss_streak, cur_loss_streak)
            equity += stake if win else -stake

            ts = tape[-1].get("ts", "")
            if ts:
                hour = ts[11:13]
                best_hour[hour] = best_hour.get(hour,  0.0) + (stake if win else -stake)


            results.append({"entry": i, "observed": round(observed, 1), "win": win})
            i += window


        total = wins + losses
        net_ev = round(equity / max(1, total),2) if total else None
        win_rate = round(wins / total *  100.0, 1) if total else None
        best_hour_ranked = sorted(best_hour.items(), key=lambda kv: kv[1], reverse=True)[:3]
        return {
            "symbol": symbol,
            "family": family,
            "window": window,
            "duration": duration,
            "trades": len(results),
            "win_rate": win_rate,
            "wins": wins,
            "losses": losses,
            "net_profit": round(equity, 2),
            "net_ev": net_ev,
            "longest_loss_streak": longest_loss_streak,
            "best_hour": [{"hour": h + ":00", "pnl": round(p, 2)}for h, p in best_hour_ranked],
            "note": "Replay of EAGLE-X's recorded tape - descriptive,not predictive.",
        }


    @staticmethod
    def _digit_hits(d: int, family: str) -> bool:


        if family == "ODD":
            return d % 2 ==  1
        if family == "EVEN":
            return d %  2 ==  0
        return False


    def _load_tape(self, symbol: str) -> List[dict]:



        path = _TICK_DIR / f"{symbol}.jsonl"
        if not path.exists():
            return []
        out: List[dict] = []
        try:
            with path.open("r", encoding="utf-8")as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:


                        continue
        except OSError:
            logger.exception("cockpit tape read failed %s", symbol)
        return out


cockpit_engine = CockpitEngine()
