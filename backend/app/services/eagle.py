"""Hunting-Eagle Precision layer — see everything, strike almost never.

Precision, not frequency (§1): the primary optimization target is
Precision = good trades / executed trades, monitored alongside EV, drawdown,
risk-adjusted return, calibration and the false-signal rate. A missed trade
is acceptable; a low-quality trade is not.

Built on top of the bottom-up gates, the seven-brain ensemble and the
lightning hot path:

- Three-layer vision (§3): EYE (500-2000 baseline), FOCUS (100-500),
  STRIKE (10-100). Agreement across horizons is a hard quality gate (§4).
- Probability consensus (§5/§6): raw, Bayesian (Dirichlet), rolling and
  conditional estimates must agree — model disagreement rejects.
- Signal stack (§12): twelve levels, every one must pass.
- Anti-overconfidence (§14): confidence, uncertainty and contradiction
  reported separately; excessive uncertainty rejects.
- EntryPrecisionScore 0-100 (§19) with A+/A/B/C bands; <65 is NO TRADE.
- Crosshair (§7/§8/§9/§10/§11): exact symbol/contract/barrier/digit
  ranking, never "OVER looks good".
- False-positive hunting (§20): the loss database is mined for common
  failure patterns and turned into explicit filters.
- One-tick decision recheck (§18) and precision scoreboard (§25).

Zero-forcing (§13): inactivity, overdueness, heat, or account mood are
never reasons to trade. Validated opportunity + acceptable risk only.
"""
import math
from collections import Counter
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.core.queue import tick_queue
from app.services.bottom_up import BottomUpEngine, bottom_up_engine, wilson_upper_bound
from app.services.persistence import journal_engine
from app.services.pro_trader import RNG_NOTE, pro_trader, wilson_lower_bound
from app.services.super_profit import SuperProfitEngine, super_profit_engine

# §3 three-layer vision horizons.
EYE_WINDOWS = (500, 1000)       # long-range baseline
FOCUS_WINDOWS = (100, 250, 500)  # medium range
STRIKE_WINDOWS = (10, 25, 50)    # immediate actionability


@dataclass
class EagleConfig:
    consensus_min: float = 0.80        # §6 probability agreement floor
    max_uncertainty: float = 0.35      # §14 anti-overconfidence ceiling
    max_contradiction: float = 0.25
    entry_a_plus: float = 90.0         # §19 precision score bands
    entry_a: float = 85.0
    entry_b: float = 75.0
    entry_c: float = 65.0              # below: NO TRADE
    horizon_agreement_tol: float = 0.05  # §4 min mean edge every horizon must clear
    min_sample: int = 100
    signal_strength_floor: float = 0.0  # §16 decay floor


EAGLE_CFG = EagleConfig()


def _posts(digits: List[int], windows: Tuple[int, ...]) -> Dict[int, List[float]]:
    out = {}
    for w in windows:
        if len(digits) >= w:
            c = Counter(digits[-w:])
            out[w] = [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]
    return out


def _prob_agreement(estimates: List[float]) -> float:
    """§6: 1 - normalized variance of the model probabilities."""
    if len(estimates) < 2:
        return 0.0
    mu = sum(estimates) / len(estimates)
    if mu <= 0 or mu >= 1:
        return 0.0
    var = sum((x - mu) ** 2 for x in estimates) / (len(estimates) - 1)
    # normalized against the widest possible spread on [0, 1]
    return round(max(0.0, 1.0 - (var / 0.25) ** 0.5), 4)


class EagleEngine:
    def __init__(self, queue=None, layer: BottomUpEngine = None,
                 ensemble: SuperProfitEngine = None):
        self.queue = queue or tick_queue
        self._layer = layer or bottom_up_engine
        self._ensemble = ensemble or super_profit_engine
        self.config = EagleConfig()

    # ---------------- §2 eagle eye market state ----------------
    def market_state(self, symbol: str) -> dict:
        ticks = self.queue.recent(symbol, limit=max(EYE_WINDOWS))
        digits = [t.digit for t in ticks]
        n = len(digits)
        if n == 0:
            return {"symbol": symbol, "ticks": 0, "state": "NO_DATA"}
        board = pro_trader.board(symbol)
        if board.get("decision") == "NO_DATA":
            board = {"data_ok": True, "regime": "UNKNOWN", "anomaly_score": 0.0,
                     "entropy_norm": 1.0}
        eye = _posts(digits, EYE_WINDOWS)
        focus = _posts(digits, FOCUS_WINDOWS)
        strike = _posts(digits, STRIKE_WINDOWS)
        big_w = max(eye) if eye else (max(focus) if focus else n)
        counts = Counter(digits[-big_w:])
        gaps = {}
        for d in range(10):
            gap = 0
            for x in reversed(digits):
                if x == d:
                    break
                gap += 1
            else:
                gap = n
            gaps[d] = gap
        streak = 1
        for i in range(n - 2, -1, -1):
            if digits[i] == digits[-1]:
                streak += 1
            else:
                break
        return {
            "symbol": symbol,
            "ticks": n,
            "current_digit": digits[-1],
            "current_streak": streak,
            "gaps": gaps,
            "horizons": {
                "eye": {str(w): [round(p, 4) for p in eye[w]] for w in eye},
                "focus": {str(w): [round(p, 4) for p in focus[w]] for w in focus},
                "strike": {str(w): [round(p, 4) for p in strike[w]] for w in strike},
            },
            "regime": board.get("regime"),
            "anomaly_score": board.get("anomaly_score"),
            "entropy_norm": board.get("entropy_norm"),
            "data_ok": bool(board.get("data_ok")) and all(0 <= x <= 9 for x in digits),
        }

    # ---------------- §5 probability consensus ----------------
    def _prob_estimates(self, digits: List[int], kind: str, d: Optional[int]) -> dict:
        prob = BottomUpEngine._contract_prob
        eye = _posts(digits, EYE_WINDOWS)
        focus = _posts(digits, FOCUS_WINDOWS)
        strike = _posts(digits, STRIKE_WINDOWS)
        raw_w = max(focus) if focus else len(digits)
        counts = Counter(digits[-raw_w:])
        raw = BottomUpEngine._win_count(kind, d, counts, raw_w) / raw_w
        bayes_w = max(focus) if focus else len(digits)
        bayes = prob(kind, d, focus[bayes_w]) if focus else raw
        rolling_ws = sorted(set(eye) | set(focus))
        rolling = (sum(prob(kind, d, _posts(digits, (w,))[w]) for w in rolling_ws)
                   / len(rolling_ws)) if rolling_ws else raw
        # conditional on the previous digit
        last = digits[-1]
        cond_pairs = [(a, b) for a, b in zip(digits, digits[1:]) if a == last]
        if len(cond_pairs) >= 30:
            cc = Counter(b for _, b in cond_pairs)
            cpost = [(cc.get(x, 0) + 1) / (len(cond_pairs) + 10) for x in range(10)]
            cond = prob(kind, d, cpost)
        else:
            cond = bayes
        estimates = {"raw": raw, "bayesian": bayes, "rolling": rolling, "conditional": cond}
        mean_p = sum(estimates.values()) / len(estimates)
        return {
            "estimates": {k: round(v, 4) for k, v in estimates.items()},
            "mean": round(mean_p, 4),
            "consensus": _prob_agreement(list(estimates.values())),
        }

    # ---------------- §3/§4 multi-horizon confirmation ----------------
    def _horizon_agreement(self, digits: List[int], kind: str, d: Optional[int],
                           breakeven: float) -> dict:
        prob = BottomUpEngine._contract_prob
        levels = {}
        for name, windows in (("eye", EYE_WINDOWS), ("focus", FOCUS_WINDOWS),
                              ("strike", STRIKE_WINDOWS)):
            posts = _posts(digits, windows)
            if not posts:
                levels[name] = None
                continue
            vals = [prob(kind, d, posts[w]) - breakeven for w in sorted(posts)]
            levels[name] = {"mean_edge": round(sum(vals) / len(vals), 4),
                            "all_positive": all(v > 0 for v in vals),
                            "windows": len(vals)}
        filled = [v for v in levels.values() if v]
        # §4: agreement means every horizon clears the SAFETY MARGIN, not
        # merely breakeven — a short spike that barely lifts the baseline is
        # LOW confidence, exactly the "sudden spike vs normal long-term" case.
        margin = max(self._layer.config.min_edge, self.config.horizon_agreement_tol)
        consistent = bool(filled) and all(v["mean_edge"] >= margin for v in filled)
        eye_edge = levels.get("eye", {}).get("mean_edge", 0.0) if levels.get("eye") else 0.0
        strike_edge = levels.get("strike", {}).get("mean_edge", 0.0) if levels.get("strike") else 0.0
        spike_only = strike_edge >= margin and eye_edge < margin
        return {"levels": levels, "consistent": consistent, "spike_only": spike_only}

    # ---------------- §19 entry precision score ----------------
    def _precision_score(self, *, base: dict, consensus: float, uncertainty: float,
                         contradiction: float, horizon: dict, latency_ms: Optional[float],
                         payout_source: str) -> float:
        cfg = self.config
        life = base.get("signal_lifetime") or {}
        slope = float(life.get("edge_slope") or 0.0)
        freshness = 1.0 if base.get("signal_state") == "CONFIRMED" else 0.5
        latency_q = 1.0 if latency_ms is None else (
            1.0 if latency_ms <= 250 else 0.5 if latency_ms <= 500 else 0.0)
        payout_q = 1.0 if payout_source == "live_proposal" else 0.6
        score = 100.0 * (
            0.18 * min(1.0, max(base["edge"], 0.0) / 0.08)
            + 0.17 * consensus
            + 0.15 * (1.0 - uncertainty)
            + 0.12 * (1.0 - contradiction)
            + 0.12 * (1.0 if horizon["consistent"] else 0.0 if horizon["spike_only"] else 0.4)
            + 0.10 * min(1.0, max(base["ev"], 0.0) / 0.10)
            + 0.08 * freshness
            + 0.05 * payout_q
            + 0.03 * latency_q
            + (0.0 if slope < 0 else 0.0)  # decay handled in gates; slope not double-counted
        )
        return round(max(0.0, min(100.0, score)), 1)

    def _band(self, score: float) -> str:
        cfg = self.config
        if score >= cfg.entry_a_plus:
            return "A+"
        if score >= cfg.entry_a:
            return "A"
        if score >= cfg.entry_b:
            return "B"
        if score >= cfg.entry_c:
            return "C"
        return "NO_TRADE"

    # ---------------- §12 the signal stack ----------------
    def _signal_stack(self, *, base: dict, ctx_state: dict, consensus: dict,
                      horizon: dict, uncertainty: float, contradiction: float,
                      latency_ms: Optional[float], risk_blocked: bool) -> dict:
        cfg = self.config
        payout_ok = base["payout"] > 1.0
        levels = {
            "1_valid_market": bool(ctx_state.get("data_ok")),
            "2_enough_data": base["sample_size"] >= cfg.min_sample,
            "3_conditional_edge": base["adjusted_p"] < 0.05 or base["window_hits"] > 0,
            "4_probability_advantage": consensus["mean"] > base["breakeven_probability"],
            "5_positive_ev": base["ev"] > 0,
            "6_confidence_supports": base["wilson_lower_bound"] > base["breakeven_probability"],
            "7_windows_agree": horizon["consistent"] and not horizon["spike_only"],
            "8_models_agree": consensus["consensus"] >= cfg.consensus_min,
            "9_no_decay": base.get("signal_state") in ("PENDING", "CONFIRMED"),
            "10_payout_attractive": payout_ok and base["ev"] > 0,
            "11_risk_approves": not risk_blocked and base["gates"].get("risk", True),
            "12_execution_ok": latency_ms is None or latency_ms <= 500,
        }
        failed = [k for k, v in levels.items() if not v]
        return {"levels": levels, "failed": failed, "passed": not failed}

    # ---------------- §25 the eagle strike ----------------
    def strike(self, symbol: str, payouts: Optional[Dict[str, float]] = None,
               latency_ms: Optional[float] = None, risk_blocked: bool = False,
               user_id: str = "default") -> dict:
        cfg = self.config
        state = self.market_state(symbol)
        if state.get("state") == "NO_DATA":
            return {"symbol": symbol, "final": "NO_TRADE", "reason": "NO_DATA", "note": RNG_NOTE}
        base = self._layer.signal(symbol, payouts=payouts, latency_ms=latency_ms,
                                  risk_blocked=risk_blocked)
        if base.get("decision") not in ("WATCH", "EXECUTE"):
            return {"symbol": symbol, "final": "NO_TRADE",
                    "reason": base.get("reason", base.get("decision")),
                    "base_decision": base.get("decision"), "note": RNG_NOTE}
        digits = [t.digit for t in self.queue.recent(symbol, limit=max(EYE_WINDOWS))]
        consensus = self._prob_estimates(digits, base["contract"], base["barrier"])
        horizon = self._horizon_agreement(digits, base["contract"], base["barrier"],
                                          base["breakeven_probability"])
        # contradiction: ensemble brains that oppose
        decision = self._ensemble.decide(symbol, payouts=payouts, latency_ms=latency_ms,
                                         risk_blocked=risk_blocked, user_id=user_id)
        brains = decision.get("brains", [])
        opposing = sum(1 for b in brains if b.get("vote") == "OPPOSE")
        contradiction = round(opposing / max(len(brains), 1), 3)
        ensemble_unc = float(decision.get("uncertainty", 1.0))
        uncertainty = round(max(ensemble_unc, 1.0 - consensus["consensus"]), 3)

        stack = self._signal_stack(
            base=base, ctx_state=state, consensus=consensus, horizon=horizon,
            uncertainty=uncertainty, contradiction=contradiction,
            latency_ms=latency_ms, risk_blocked=risk_blocked)
        score = self._precision_score(
            base=base, consensus=consensus["consensus"], uncertainty=uncertainty,
            contradiction=contradiction, horizon=horizon, latency_ms=latency_ms,
            payout_source=base.get("payout_source", "assumed_default"))
        band = self._band(score)

        gates = {
            "stack": stack["passed"],
            "base_execute": base["decision"] == "EXECUTE",
            "uncertainty": uncertainty <= cfg.max_uncertainty,
            "contradiction": contradiction <= cfg.max_contradiction,
            "band": band != "NO_TRADE",
        }
        failed = [k for k, v in gates.items() if not v]
        final = "STRIKE" if all(gates.values()) else "NO_TRADE"
        # §17/§18: the one-tick recheck note lives in the card; the live
        # execution path must re-verify payout/EV/freshness immediately
        # before any buy.
        return {
            "symbol": symbol,
            "contract": base["contract"],          # §7 exact crosshair
            "barrier": base["barrier"],
            "digit": base["barrier"],
            "probability": consensus["mean"],
            "probability_estimates": consensus["estimates"],   # §5
            "probability_consensus": consensus["consensus"],   # §6
            "breakeven": base["breakeven_probability"],
            "edge": base["edge"],
            "ev": base["ev"],
            "confidence": base["wilson_lower_bound"],
            "uncertainty": uncertainty,                        # §14
            "contradiction": contradiction,
            "horizons": horizon,                               # §3/§4
            "signal_stack": stack,                             # §12
            "entry_precision_score": score,                    # §19
            "band": band,
            "signal_state": base.get("signal_state"),
            "signal_lifetime": base.get("signal_lifetime"),    # §16
            "regime": state.get("regime"),
            "gates": gates,
            "failed_gates": failed,
            "base_decision": base.get("decision"),
            "entry": "next valid opportunity after a one-tick recheck of "
                     "probability, payout, EV, signal age, risk, position "
                     "state and connection (§18)",
            "final": final,
            "note": RNG_NOTE,
        }

    # ---------------- §8/§9/§10/§11 contract specialists ----------------
    def rank_barriers(self, symbol: str) -> dict:
        """§9/§10/§11: exact per-barrier ranking for every contract family."""
        e = self._layer.evaluate(symbol)
        if e.get("decision") == "NO_DATA" or not e.get("candidates"):
            return {"symbol": symbol, "note": "NO_DATA"}
        digits = [t.digit for t in self.queue.recent(symbol, limit=max(EYE_WINDOWS))]
        out: Dict[str, list] = {"MATCHES": [], "OVER": [], "UNDER": [],
                                "ODD": [], "EVEN": [], "DIFFERS": []}
        for c in e["candidates"]:
            cons = self._prob_estimates(digits, c["contract"], c["barrier"])
            out[c["contract"]].append({
                "barrier": c["barrier"],
                "name": c["name"],
                "probability": cons["mean"],
                "probability_consensus": cons["consensus"],
                "breakeven": c["breakeven_probability"],
                "edge": c["edge"],
                "ev": c["ev"],
                "confidence": c["wilson_lower_bound"],
                "decision": c["decision"],
                "grade": c["grade"],
            })
        for fam in out.values():
            fam.sort(key=lambda x: (x["decision"] == "PASS", x["ev"]), reverse=True)
        best = {fam: (rows[0]["name"] if rows and rows[0]["decision"] == "PASS" else None)
                for fam, rows in out.items()}
        return {"symbol": symbol, "families": out, "best_per_family": best,
                "note": "Exact barrier, never 'OVER looks good' — 'OVER 6 has "
                        "the strongest validated risk-adjusted opportunity' (§8)."}

    # ---------------- §20 false-positive hunting ----------------
    def false_positive_hunt(self, user_id: str = "default") -> dict:
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id
                   and e.get("result") in ("win", "loss")]
        losses = [e for e in entries if e["result"] == "loss"]
        wins = [e for e in entries if e["result"] == "win"]

        def share(pool, keyfn):
            c = Counter(keyfn(e) for e in pool)
            return {k: round(v / len(pool), 3) for k, v in c.items()} if pool else {}

        patterns = {}
        for name, keyfn in {
            "market": lambda e: str(e.get("market", "?")),
            "family": lambda e: str(e.get("contract", "?")).split()[0].upper(),
            "barrier": lambda e: str(e.get("digit")),
            "regime": lambda e: str((e.get("analysis_snapshot") or {}).get("regime", "?")),
            "signal_state": lambda e: str((e.get("analysis_snapshot") or {}).get("signal_state", "?")),
        }.items():
            loss_dist = share(losses, keyfn)
            win_dist = share(wins, keyfn)
            over = {k: round(loss_dist.get(k, 0) - win_dist.get(k, 0), 3)
                    for k in loss_dist}
            suspects = [k for k, v in over.items() if v >= 0.10 and k not in ("?", "None")]
            if suspects:
                patterns[name] = {"overrepresented_in_losses": suspects,
                                  "loss_share": loss_dist, "win_share": win_dist}
        filters = []
        for dim, p in patterns.items():
            for value in p["overrepresented_in_losses"]:
                filters.append(f"downgrade signals where {dim}={value} "
                               f"(loss-share {p['loss_share'].get(value)} vs "
                               f"win-share {p['win_share'].get(value, 0)})")
        return {
            "user_id": user_id,
            "losses_analyzed": len(losses),
            "patterns": patterns,
            "proposed_filters": filters,
            "note": "§20: what conditions make the model wrong? These filters are "
                    "proposals — each must be validated out-of-sample before it "
                    "joins the gate stack (§15).",
        }

    # ---------------- §25 precision scoreboard ----------------
    def scoreboard(self, user_id: str = "default") -> dict:
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id
                   and e.get("result") in ("win", "loss")]
        bands: Dict[str, dict] = {}
        for e in entries:
            snap = e.get("analysis_snapshot") or {}
            band = snap.get("band") or snap.get("grade") or "UNGRADED"
            b = bands.setdefault(band, {"n": 0, "wins": 0, "pnl": 0.0, "staked": 0.0,
                                        "preds": []})
            b["n"] += 1
            b["wins"] += 1 if e["result"] == "win" else 0
            b["pnl"] += float(e.get("pnl", 0) or 0)
            b["staked"] += float(e.get("stake", 0) or 0)
            if snap.get("estimated_probability") is not None:
                b["preds"].append((float(snap["estimated_probability"]),
                                   1 if e["result"] == "win" else 0))
        out = {}
        for band, b in bands.items():
            wr = b["wins"] / b["n"] if b["n"] else 0.0
            cal_err = (sum(abs(p - r) for p, r in b["preds"]) / len(b["preds"])
                       if b["preds"] else None)
            out[band] = {
                "trades": b["n"],
                "precision": round(wr, 4),                     # §1
                "realized_ev": round(b["pnl"] / b["staked"], 4) if b["staked"] > 0 else None,
                "wilson_lower": round(wilson_lower_bound(b["wins"], b["n"]), 4) if b["n"] else None,
                "calibration_error": round(cal_err, 4) if cal_err is not None else None,
            }
        ordered = [out.get(b, {}).get("precision") for b in ("A+", "A", "B") if b in out]
        monotone = all(ordered[i] >= ordered[i + 1] for i in range(len(ordered) - 1))
        return {
            "user_id": user_id,
            "bands": out,
            "grading_monotone": monotone if len(ordered) >= 2 else None,
            "grading_verdict": ("OK" if monotone else
                                "BROKEN: A+ not better than lower bands — fix the grading"
                                if len(ordered) >= 2 else "INSUFFICIENT_DATA"),
            "note": "§25: if A+ signals are not meaningfully better than B, the "
                    "grading system is broken. This scoreboard is the check.",
        }


eagle_engine = EagleEngine()
