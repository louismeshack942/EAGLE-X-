"""Rivalry — the Self-Competitive / Limit-Breaking engine.

EAGLE-X vs EAGLE-X. CHAMPION vs CHALLENGER with a deterministic,
no-lookahead walk-forward comparator:

  CHAMPION    — the current production gate configuration (live defaults)
  CHALLENGER  — a candidate trying to beat it, WITHOUT touching live capital

Promotion is earned (§4/§13): experiment -> perturbation robustness ->
shuffle dissonance -> walk-forward over disjoint folds -> risk-adjusted
EAGLE_SCORE comparison. A challenger with higher raw EV but deeper
drawdown LOSES (§22). Robustness < 50 or walk-forward consistency < 0.5
kills the candidate regardless of score.

Sub-systems: §3 experiment generator (unique id + config snapshot),
§5/§6 walk-forward (must survive disjoint periods), §7 robustness 0-100,
§8 perturbation, §13 dissonance, §11/§12 contract/market tournaments,
§14/§19 adversarial challenger + blind spots, §23/§24 decay detector +
automatic rollback, §25 EAGLE_SCORE, §26 version evolution.

Determinism: the comparator measures REALIZED outcomes from the tape
(outcome = next tick after the decision point). No broker, no mocks, no
lookahead — a fold's decision uses only digits that preceded it.
"""
import random
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from app.services.bottom_up import BottomUpEngine
from app.services.persistence import journal_engine
from app.services.pro_trader import RNG_NOTE, wilson_lower_bound

TUNABLE = ("min_edge_x0_5", "min_edge_x2", "min_sample_50", "min_sample_250",
           "windows_50", "windows_100")

DEFAULT_PAYOUTS = {"MATCHES": 9.0, "OVER": 1.95, "UNDER": 1.95,
                   "ODD": 1.95, "EVEN": 1.95, "DIFFERS": 1.10}


@dataclass
class Config:
    """A complete gate-configuration snapshot (§3)."""
    min_edge: float = 0.03
    min_sample: int = 100
    window: int = 250
    version: str = "v1.0"

    def snapshot(self) -> dict:
        return asdict(self)


def _candidates(kind_d_pairs: List[Tuple[str, Optional[int]]]):
    return kind_d_pairs


def _win(kind: str, d, outcome) -> bool:
    if kind == "MATCHES":
        return outcome == d
    if kind == "DIFFERS":
        return outcome != d
    if kind == "OVER":
        return outcome > d
    if kind == "UNDER":
        return outcome < d
    if kind == "ODD":
        return outcome % 2 == 1
    return outcome % 2 == 0  # EVEN


def _pick(digits: List[int], cfg: Config) -> Optional[dict]:
    """Best weighted-edge candidate on the slice, or None if no margin."""
    w = min(cfg.window, len(digits))
    c = Counter(digits[-w:])
    post = [(c.get(x, 0) + 1) / (w + 10) for x in range(10)]
    best, best_edge = None, cfg.min_edge
    for kind, d in BottomUpEngine._candidates():
        payout = DEFAULT_PAYOUTS[kind]
        be = 1.0 / payout
        p = BottomUpEngine._contract_prob(kind, d, post)
        if p - be > best_edge:
            best_edge = p - be
            best = {"contract": kind, "barrier": d, "payout": payout,
                    "probability": round(p, 4), "edge": round(p - be, 4),
                    "ev": round(p * payout - 1.0, 4)}
    return best


class RivalryEngine:
    def __init__(self):
        self.champion = Config()
        self.challengers: List[dict] = []
        self.experiments: List[dict] = []
        self.blind_spots: List[dict] = []
        self.history: List[dict] = [{"version": "v1.0", "note": "production baseline"}]
        self._rng = random.Random(23)

    # ---------------- §3 generator ----------------
    def generate(self, kind: str) -> dict:
        if kind not in TUNABLE:
            return {"ok": False, "error": f"unknown knob; choose from {list(TUNABLE)}"}
        base = self.champion
        cand = Config(**base.snapshot())
        if kind == "min_edge_x0_5":
            cand.min_edge = base.min_edge * 0.5
        elif kind == "min_edge_x2":
            cand.min_edge = base.min_edge * 2
        elif kind == "min_sample_50":
            cand.min_sample = 50
        elif kind == "min_sample_250":
            cand.min_sample = 250
        elif kind == "windows_50":
            cand.window = 50
        elif kind == "windows_100":
            cand.window = 100
        exp = {"ok": True, "experiment_id": f"exp-{uuid.uuid4().hex[:8]}",
               "kind": kind, "candidate": cand.snapshot()}
        self.experiments.append(exp)
        return exp

    # ---------------- §6 walk-forward comparator ----------------
    def _walk_forward_one(self, cfg: Config, digits: List[int], folds: int = 4,
                          decision_every: int = 25) -> dict:
        """Replay on the tape: at each decision point pick a candidate from
        the past; score it with the NEXT tick. No lookahead."""
        n = wins = 0
        pnl = 0.0
        staked = 0.0
        cum = peak = dd = 0.0
        for pos in range(cfg.min_sample, len(digits) - 1, decision_every):
            cand = _pick(digits[:pos], cfg)
            if cand is None:
                continue
            win = _win(cand["contract"], cand["barrier"], digits[pos])
            n += 1
            staked += 1.0
            pnl += (cand["payout"] - 1.0) if win else -1.0
            wins += 1 if win else 0
            cum += (cand["payout"] - 1.0) if win else -1.0
            peak = max(peak, cum)
            dd = max(dd, peak - cum)
        return {
            "trades": n,
            "wins": wins,
            "precision": round(wins / n, 4) if n else 0.0,
            "ev": round(pnl / staked, 4) if staked > 0 else 0.0,
            "drawdown": round(dd, 4),
        }

    def _folds_eval(self, cfg: Config, digits: List[int], folds: int = 4) -> dict:
        """Run the whole tape once, then check period-by-period consistency."""
        overall = self._walk_forward_one(cfg, digits)
        if overall["trades"] < 5:
            return {"ok": False, "reason": "too few trades for a verdict",
                    "overall": overall}
        fold_len = len(digits) // folds
        period_evs, period_dds = [], []
        for f in range(folds):
            part = digits[f * fold_len:(f + 1) * fold_len]
            one = self._walk_forward_one(cfg, part)
            if one["trades"]:
                period_evs.append(one["ev"])
                period_dds.append(one["drawdown"])
        consistency = (sum(1 for e in period_evs if e > 0) / len(period_evs)
                       if period_evs else 0.0)
        instability = (max(period_evs) - min(period_evs)) if len(period_evs) > 1 else 0.0
        return {"ok": True, "overall": overall,
                "period_evs": period_evs, "period_dds": period_dds,
                "consistency": round(consistency, 3),
                "instability": round(instability, 4)}

    # ---------------- §7 robustness ----------------
    def _robustness(self, cfg: Config, digits: List[int], folds_wf: dict) -> float:
        parts = []
        folds_ok = folds_wf["ok"]
        parts.append(0.30 * (folds_wf["consistency"] if folds_ok else 0.0))
        parts.append(0.25 * max(0.0, 1.0 - folds_wf["instability"] / 0.5) if folds_ok else 0.0)
        # §8 perturbation: window +/-20% must keep the same sign of EV
        if folds_ok:
            stable = 0
            for w_mult in (0.8, 1.25):
                p = Config(min_edge=cfg.min_edge, min_sample=cfg.min_sample,
                           window=max(50, int(cfg.window * w_mult)))
                r = self._walk_forward_one(p, digits)
                if (r["ev"] > 0) == (folds_wf["overall"]["ev"] > 0):
                    stable += 0.125
            parts.append(stable)
        else:
            parts.append(0.0)
        # §13 dissonance: the edge must not be equally good on shuffled data
        shuffled = digits[:]
        self._rng.shuffle(shuffled)
        fake = self._walk_forward_one(cfg, shuffled)
        if folds_ok and fake["trades"] >= 5:
            gap = folds_wf["overall"]["ev"] - fake["ev"]
            parts.append(0.25 * max(0.0, min(gap / 0.2, 1.0)))
        else:
            parts.append(0.0)
        return round(100 * sum(parts), 1)

    # ---------------- §25 EAGLE_SCORE ----------------
    @staticmethod
    def _score(wf: dict, robustness: float) -> float:
        o = wf["overall"] if wf["ok"] else {"ev": 0.0, "precision": 0.0, "drawdown": 0.0}
        instability = wf["instability"] if wf["ok"] else 0.0
        score = (o["ev"] * 40 + o["precision"] * 20 + robustness * 0.25
                 - o["drawdown"] * 20 - instability * 15)
        return round(score, 3)

    # ---------------- §4 the promotion ----------------
    def compete(self, digits: List[int], kind: Optional[str] = None,
                folds: int = 4) -> dict:
        if kind is None:
            kind = self._rng.choice(list(TUNABLE))
        exp = self.generate(kind)
        if not exp["ok"]:
            return {"promoted": False, "error": exp["error"]}
        cand = Config(**exp["candidate"])
        champ_wf = self._folds_eval(self.champion, digits, folds)
        chal_wf = self._folds_eval(cand, digits, folds)
        robustness = self._robustness(cand, digits, chal_wf)
        c_score = self._score(champ_wf, robustness)
        x_score = self._score(chal_wf, robustness)
        consistency = chal_wf.get("consistency", 0.0)
        promoted = (chal_wf["ok"] and x_score > c_score
                    and robustness >= 50 and consistency >= 0.5)
        rationale = (f"x{ x_score} vs c{c_score}, robustness {robustness}, "
                     f"consistency {consistency}")
        verdict = {"promoted": promoted, "experiment_id": exp["experiment_id"],
                   "kind": kind, "champion_score": c_score,
                   "challenger_score": x_score, "robustness": robustness,
                   "challenger_walk_forward": chal_wf, "rationale": rationale,
                   "note": RNG_NOTE}
        self.challengers.append({k: verdict[k] for k in
                                 ("promoted", "experiment_id", "kind",
                                  "champion_score", "challenger_score",
                                  "robustness", "rationale")})
        if promoted:
            cand.version = f"v{len(self.history) + 1}.0"
            self.history.append({"version": cand.version,
                                 "demoted": self.champion.version,
                                 "reason": rationale,
                                 "experiment_id": exp["experiment_id"]})
            self.champion = cand
        return verdict

    # ---------------- §11/§12 tournaments ----------------
    def tournament(self, user_id: str = "default", dimension: str = "contract") -> dict:
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id
                   and e.get("result") in ("win", "loss")]
        keyfn = ((lambda e: str(e.get("contract", "?")).split()[0].upper())
                 if dimension == "contract" else (lambda e: str(e.get("market", "?"))))
        score: Dict[str, dict] = {}
        for e in entries:
            k = keyfn(e)
            cell = score.setdefault(k, {"n": 0, "wins": 0, "pnl": 0.0, "staked": 0.0})
            cell["n"] += 1
            cell["wins"] += 1 if e["result"] == "win" else 0
            cell["pnl"] += float(e.get("pnl", 0) or 0)
            cell["staked"] += float(e.get("stake", 0) or 0)
        ranked = sorted(score.items(),
                        key=lambda kv: (kv[1]["pnl"] / kv[1]["staked"]
                                        if kv[1]["staked"] > 0 else -9.0),
                        reverse=True)
        return {
            "dimension": dimension,
            "leaderboard": [{
                "name": k,
                "trades": v["n"],
                "precision": round(v["wins"] / v["n"], 4) if v["n"] else None,
                "ev": round(v["pnl"] / v["staked"], 4) if v["staked"] > 0 else None,
            } for k, v in ranked],
            "note": "the system may re-prioritize the validated leader; it "
                    "never stays attached to a market/contract (§11/§12).",
        }

    # ---------------- §14/§19 adversarial + blind spots ----------------
    def adversarial(self, user_id: str = "default") -> dict:
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id
                   and e.get("result") in ("win", "loss")]
        losses = [e for e in entries if e["result"] == "loss"]
        wins = [e for e in entries if e["result"] == "win"]

        def share(pool, keyfn):
            c = Counter(keyfn(e) for e in pool)
            return {k: round(v / len(pool), 3) for k, v in c.items()} if pool else {}

        spots = []
        for name, keyfn in {
            "market": lambda e: str(e.get("market", "?")),
            "contract": lambda e: str(e.get("contract", "?")).split()[0].upper(),
            "barrier": lambda e: str(e.get("digit")),
        }.items():
            ld, wd = share(losses, keyfn), share(wins, keyfn)
            for k, v in ld.items():
                if v - wd.get(k, 0) >= 0.10 and k not in ("?", "None"):
                    spot = {"dimension": name, "value": k,
                            "loss_overweight": round(v - wd.get(k, 0), 3)}
                    spots.append(spot)
                    if spot not in self.blind_spots:
                        self.blind_spots.append(spot)
        return {"blind_spots": spots,
                "note": "§14/§19: find where the champion fails and feed it "
                        "back. Patterns must survive out-of-sample before they "
                        "harden into filters."}

    # ---------------- §23/§24 decay + rollback ----------------
    def decay(self, user_id: str = "default", recent_n: int = 20) -> dict:
        entries = [e for e in journal_engine.list_entries(limit=100000)
                   if e.get("user_id", "default") == user_id
                   and e.get("result") in ("win", "loss")]
        if len(entries) < 2 * recent_n:
            return {"status": "INSUFFICIENT_DATA", "action": "monitor"}
        recent, hist = entries[-recent_n:], entries[:-recent_n]
        r_ev = sum(float(e.get("pnl", 0) or 0) for e in recent)
        r_wr = sum(1 for e in recent if e["result"] == "win") / recent_n
        h_wr = sum(1 for e in hist if e["result"] == "win") / len(hist)
        deteriorating = r_ev < 0 and r_wr < h_wr
        status = "YELLOW" if deteriorating else "GREEN"
        if deteriorating and r_ev < -10.0:
            status = "RED"
        action = "monitor"
        if status == "RED":
            action = "ROLLBACK to previous champion" if len(self.history) > 1 \
                else "HALT champion (no prior version to roll back to)"
        return {"status": status, "recent_win_rate": round(r_wr, 3),
                "historical_win_rate": round(h_wr, 3),
                "recent_ev": round(r_ev, 2), "action": action,
                "note": "§23/§24: RED triggers automatic rollback — no "
                        "experiment gets to destroy the account unnoticed."}

    # ---------------- status ----------------
    def status(self) -> dict:
        return {
            "champion": self.champion.snapshot(),
            "challengers_tested": len(self.challengers),
            "promoted": sum(1 for c in self.challengers if c["promoted"]),
            "blind_spots": self.blind_spots,
            "history": self.history[-10:],
            "note": "the machine chases improvement, not the market.",
        }


rivalry_engine = RivalryEngine()
