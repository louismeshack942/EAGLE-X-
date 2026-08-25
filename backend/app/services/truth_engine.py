"""Truth Engine — the honest answer to the only question that matters:
"Do I have a real edge on this contract, right now?"

Every contract carries three numbers and one verdict:
- breakeven_wr: the win rate the payout REQUIRES (1/payout). The house
  sets the payout; anything below breakeven is a slow bleed no luck
  survives.
- observed_wr: the Bayes-shrunk win rate from the live tick tape.
- margin: observed minus breakeven, in percentage points. Positive means
  the tape beats the payout; the market mispriced it. Negative means the
  house edge holds.
- verdict:
    EDGE   — significant deviation AND positive EV. The only time a
             stake is justified.
    FAIR   — no significant deviation. The market is priced; trading it
             is paying the spread for entertainment.
    TRAP   — significant deviation but negative EV. The deviation is
             real and it STILL loses. These are the most dangerous
             contracts: they look like signal and are pure bleed.

reconcile_journal replays the user's own history against breakeven math.
Example of the truth it tells: 18 wins / 2 losses at $1 DIFFERS (payout
1.1) needs a 90.9% win rate to break even. 18/20 is 90.0%. Expected P&L:
20 * 1.0 * (0.90 * 1.1 - 1) = -$0.20. The observed +$0.18 is luck
variance on a contract whose structure pays less than its true win rate
demands. Not an edge. Never was.
"""
import math
from typing import List, Optional

from app.services.analytics_advanced import digit_engine
from app.services.market_master import PAYOUTS, _digit_payout
from app.services.persistence import journal_engine

# Verdict thresholds
SIGNIFICANCE_Z = 1.96
MIN_REAL_EV = 0.02  # 2 cents per 1.0 — below this the "edge" is execution noise


def _breakeven(payout: float) -> float:
    """Win rate (%) the payout demands. payout is total-return multiple."""
    return round(100.0 / payout, 2) if payout > 0 else 100.0


def _verdict(margin_pp: float, ev: float, significant: bool) -> str:
    if not significant:
        return "FAIR"
    if ev >= MIN_REAL_EV and margin_pp > 0:
        return "EDGE"
    return "TRAP"


class TruthEngine:
    """Computes honest expectancy per contract from the live tape."""

    def proven_edges(self, symbol: str, windows=(100, 300, 1000), min_ticks: int = 50) -> set[tuple]:
        """(type, digit) pairs that are EDGE in EVERY window with enough data.

        A single 300-tick window manufactures flukes — some digit always looks
        overfed by chance. An edge the CF may fire on must survive all three
        windows simultaneously. min_ticks is a thin-tape floor only (it must
        stay below the smallest window, or every call is a blanket ban).
        Returns an empty set when the tape is thin or the market is fair —
        the correct, capital-preserving answer.
        """
        proven: set[tuple] = set()
        first = True
        for w in windows:
            e = self.expectancy(symbol, window=w)
            if e.get("n_ticks", 0) < min_ticks:
                return set()  # not enough data to trust any edge
            keys = {
                (c["type"], c.get("digit"))
                for c in e.get("contracts", [])
                if c["verdict"] == "EDGE"
            }
            proven = keys if first else (proven & keys)
            first = False
            if not proven:
                return set()
        return proven

    def expectancy(self, symbol: str, window: int = 300) -> dict:
        analysis = digit_engine.get_digit_analysis(symbol, window)
        freq = analysis.get("frequency") or {}
        out = {"symbol": symbol, "window": window, "n_ticks": analysis.get("n", 0), "contracts": []}
        if not freq:
            return out

        est = {d: float(freq[str(d)].get("estimate", 10.0)) for d in range(10)}
        z = {d: float(freq[str(d)].get("z", 0.0) or 0.0) for d in range(10)}
        contracts = out["contracts"]

        # Digit contracts: MATCHES (win if digit == d) and DIFFERS (win if
        # digit != d).
        for d in range(10):
            for ctype, wr_obs, z_eff in (
                ("MATCHES", est[d], z[d]),
                ("DIFFERS", 100.0 - est[d], -z[d]),
            ):
                payout = PAYOUTS[ctype]
                be = _breakeven(payout)
                margin = round(wr_obs - be, 2)
                ev = round(wr_obs / 100.0 * payout - 1.0, 4)
                # z_eff points positive in the winning direction for both
                # (MATCHES: overfed digit; DIFFERS: starving digit via -z).
                sig = z_eff >= SIGNIFICANCE_Z
                contracts.append({
                    "name": f"{ctype} {d}" if ctype != "ODD" and ctype != "EVEN" else ctype,
                    "type": ctype,
                    "digit": d,
                    "payout": payout,
                    "breakeven_wr": be,
                    "observed_wr": round(wr_obs, 2),
                    "margin_pp": margin,
                    "ev": ev,
                    "z": round(z_eff, 2),
                    "significant": sig,
                    "verdict": _verdict(margin, ev, sig),
                })

        # OVER d wins on digits d+1..9; UNDER d wins on digits 0..d-1.
        for d in range(1, 10):
            over_wr = sum(est[x] for x in range(d + 1, 10))
            under_wr = sum(est[x] for x in range(0, d))
            n = analysis.get("n", 0)
            # z for the pooled "digit > d" event against its fair share
            fair_over = (9 - d) * 10.0
            fair_under = d * 10.0
            den = math.sqrt(n * (fair_over / 100.0) * (1 - fair_over / 100.0)) if n else 1.0
            over_z = (sum((est[x] / 100.0) * n for x in range(d + 1, 10)) - n * fair_over / 100.0) / den if den else 0.0
            for ctype, wr_obs, z_eff, payout in (
                ("OVER", over_wr, over_z, _digit_payout(fair_over / 100.0)),
                ("UNDER", under_wr, -over_z, _digit_payout(fair_under / 100.0)),
            ):
                be = _breakeven(payout)
                margin = round(wr_obs - be, 2)
                ev = round(wr_obs / 100.0 * payout - 1.0, 4)
                sig = abs(z_eff) >= SIGNIFICANCE_Z
                contracts.append({
                    "name": f"{ctype} {d}",
                    "type": ctype,
                    "digit": d,
                    "payout": round(payout, 4),
                    "breakeven_wr": be,
                    "observed_wr": round(wr_obs, 2),
                    "margin_pp": margin,
                    "ev": ev,
                    "z": round(z_eff, 2),
                    "significant": sig,
                    "verdict": _verdict(margin, ev, sig),
                })

        # ODD / EVEN — fair coin flips by design, but the tape is the
        # referee: if parity itself is skewed (z >= 1.96 pooled across the
        # five odd digits vs their 50% fair share), the skew is real and
        # the payout's 52.6% breakeven can be beaten. No skew -> FAIR, and
        # the CF stays benched off them.
        odd_wr = sum(est[d] for d in range(1, 10, 2))
        n = analysis.get("n", 0)
        den = math.sqrt(n * 0.5 * 0.5) if n else 1.0
        odd_z = ((odd_wr / 100.0) * n - n * 0.5) / den if den else 0.0
        for ctype, wr_obs, z_eff in (("ODD", odd_wr, odd_z), ("EVEN", 100.0 - odd_wr, -odd_z)):
            payout = PAYOUTS[ctype]
            be = _breakeven(payout)
            margin = round(wr_obs - be, 2)
            ev = round(wr_obs / 100.0 * payout - 1.0, 4)
            sig = z_eff >= SIGNIFICANCE_Z
            contracts.append({
                "name": ctype,
                "type": ctype,
                "digit": None,
                "payout": payout,
                "breakeven_wr": be,
                "observed_wr": round(wr_obs, 2),
                "margin_pp": margin,
                "ev": ev,
                "z": round(z_eff, 2),
                "significant": sig,
                "verdict": _verdict(margin, ev, sig),
            })

        best = max(contracts, key=lambda c: c["ev"], default=None)
        out["best"] = best
        out["edges"] = [c for c in contracts if c["verdict"] == "EDGE"]
        out["traps"] = [c for c in contracts if c["verdict"] == "TRAP"]
        return out

    def edge_board(self, symbols: List[str], window: int = 300) -> dict:
        """The board: one honest line per symbol, ranked across all symbols."""
        rows = []
        for symbol in symbols:
            e = self.expectancy(symbol, window)
            best = e.get("best")
            if not best:
                continue
            rows.append({
                "symbol": symbol,
                "n_ticks": e.get("n_ticks", 0),
                "best_contract": best["name"],
                "ev": best["ev"],
                "margin_pp": best["margin_pp"],
                "verdict": best["verdict"],
                "edges": len(e.get("edges", [])),
                "traps": len(e.get("traps", [])),
            })
        rows.sort(key=lambda r: r["ev"], reverse=True)
        board_has_edge = any(r["verdict"] == "EDGE" for r in rows)
        return {
            "window": window,
            "symbols": rows,
            "board_has_edge": board_has_edge,
            "note": (
                "Real edge somewhere on the board — stakes are justified on EDGE rows only."
                if board_has_edge else
                "No real edge anywhere on the board right now. The honest position is NO TRADE. "
                "Trading a FAIR/TRAP board is paying the house for entertainment."
            ),
        }

    def projection(self, symbol: str, bankroll: float, trades_per_day: float, window: int = 300) -> dict:
        """Honest daily projection for the symbol's best contract.

        Stake is quarter-Kelly capped at 10% of bankroll (the GK's rule).
        If there is no EDGE the projection is zero — not a hedge, zero.
        """
        e = self.expectancy(symbol, window)
        best = e.get("best")
        if not best or best["verdict"] != "EDGE":
            return {
                "symbol": symbol,
                "bankroll": bankroll,
                "trades_per_day": trades_per_day,
                "tradeable": False,
                "expected_daily_pnl": 0.0,
                "note": "No EDGE on this symbol — the honest projection is $0. "
                        "Anything traded here has negative expectancy.",
            }
        p = best["observed_wr"] / 100.0
        b = best["payout"] - 1.0
        q = 1.0 - p
        kelly = max(0.0, (b * p - q) / b) if b > 0 else 0.0
        stake = round(min(0.10, 0.25 * kelly) * bankroll, 2)
        ev_per_trade = round(stake * best["ev"], 4)
        daily = round(ev_per_trade * trades_per_day, 2)
        return {
            "symbol": symbol,
            "bankroll": bankroll,
            "trades_per_day": trades_per_day,
            "tradeable": True,
            "best_contract": best["name"],
            "ev_per_unit": best["ev"],
            "kelly_stake": stake,
            "ev_per_trade": ev_per_trade,
            "expected_daily_pnl": daily,
            "note": f"EDGE {best['name']}: at {trades_per_day:g} trades/day, quarter-Kelly "
                    f"expects ~${daily}/day. Anything above that is variance, not skill.",
        }

    def reconcile_journal(self, user_id: str = "default") -> dict:
        """Replay the journal against breakeven math — the trader's truth."""
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id]
        groups: dict[str, dict] = {}
        for e in entries:
            key = f"{e.get('contract', '?')} {e.get('digit') if e.get('digit') is not None else ''}".strip()
            g = groups.setdefault(key, {
                "contract": key,
                "n": 0, "wins": 0,
                "staked": 0.0, "pnl": 0.0,
                "win_pnls": [], "stakes": [],
            })
            g["n"] += 1
            stake = float(e.get("stake", 0) or 0)
            pnl = float(e.get("pnl", 0) or 0)
            g["staked"] += stake
            g["pnl"] += pnl
            if e.get("result") == "win":
                g["wins"] += 1
                if stake > 0:
                    g["win_pnls"].append(pnl)
                    g["stakes"].append(stake)

        rows = []
        for g in groups.values():
            n = g["n"]
            wins = g["wins"]
            wr = wins / n if n else 0.0
            # Infer the payout actually paid from winning entries:
            # payout_total_return = pnl/stake + 1 (pnl is profit on win).
            ratios = [pnl / stake + 1.0 for pnl, stake in zip(g["win_pnls"], g["stakes"]) if stake > 0 and pnl > 0]
            payout = sorted(ratios)[len(ratios) // 2] if ratios else None
            be = _breakeven(payout) if payout else None
            margin = round(wr * 100 - be, 2) if be else None
            long_run_ev = round(wr * payout - 1.0, 4) if payout else None
            expected_pnl = round(g["staked"] * long_run_ev, 2) if long_run_ev is not None else None
            actual_pnl = round(g["pnl"], 2)
            if long_run_ev is None:
                verdict = "UNKNOWN"
            elif long_run_ev > 0.01:
                verdict = "SUSTAINABLE"
            elif long_run_ev >= -0.01:
                verdict = "BREAKEVEN"
            else:
                verdict = "SLOW BLEED"
            rows.append({
                "contract": g["contract"],
                "trades": n,
                "wins": wins,
                "win_rate": round(wr * 100, 1),
                "payout_paid": round(payout, 3) if payout else None,
                "breakeven_wr": be,
                "margin_pp": margin,
                "long_run_ev": long_run_ev,
                "staked": round(g["staked"], 2),
                "actual_pnl": actual_pnl,
                "expected_pnl_at_observed_rate": expected_pnl,
                "verdict": verdict,
            })
        rows.sort(key=lambda r: r["actual_pnl"])
        total_pnl = round(sum(r["actual_pnl"] for r in rows), 2)
        bleeds = [r for r in rows if r["verdict"] == "SLOW BLEED"]
        return {
            "user_id": user_id,
            "entries": len(entries),
            "total_pnl": total_pnl,
            "contracts": rows,
            "note": (
                f"{len(bleeds)} contract famil{'ies are' if len(bleeds) != 1 else 'y is'} structurally losing "
                "(win rate below the payout's breakeven). Cutting "
                f"{'them' if len(bleeds) != 1 else 'it'} is the single highest-EV move in the book."
                if bleeds else
                "No structurally losing contract family in the journal — variance, not structure, "
                "decides this month's P&L."
            ),
        }


truth_engine = TruthEngine()
