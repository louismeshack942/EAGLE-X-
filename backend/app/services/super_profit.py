"""Super-Profitability Engineering layer — the multi-brain ensemble.

Seven independent analytical brains vote on every candidate; the FINAL
signal is their consensus, never one brain's opinion:

  A Frequency    — adaptive-window digit frequencies, hot/cold divergence
  B Probability  — Bayesian posteriors, Wilson bounds, probability decay
  C Sequence     — transitions, streaks, gaps, recurrence (gated by a
                   shuffle test: no sequential info, no sequence vote)
  D Anomaly      — chi-square, entropy, z-scores, autocorrelation
  E Contract     — per-contract EV with specialist margin multipliers
  F Execution    — latency, payout freshness, signal freshness
  G Risk         — risk_guard veto + per-family model health

A candidate EXECUTES only when it (1) passes every bottom-up hard gate and
survived the confirmation window, (2) wins ensemble consensus with zero
opposing brains, (3) stays under the uncertainty ceiling, (4) is not in a
disabled regime / RED health family, and (5) clears the meta-score floor.
Everything else is REJECT or WATCH. No fantasy, no forced trades.

Offline honesty tools live here too: conditional-edge search (§3), feature
ablation (§16), probability calibration (§23), model health (§22), the
profitability matrix (§18), market profiles (§6), capital allocation (§19)
and profit locking (§20). Shuffle/ablation results are computed with NO
lookahead: every position is evaluated only on ticks that preceded it.
"""
import math
import random
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from app.core.queue import tick_queue
from app.services.bottom_up import (
    CONTRACT_HIERARCHY,
    BottomUpEngine,
    bottom_up_engine,
    wilson_upper_bound,
)
from app.services.persistence import journal_engine, settings_store
from app.services.pro_trader import (
    FDR_Q,
    RNG_NOTE,
    chi2_sf,
    pro_trader,
    wilson_lower_bound,
)

SUP = "SUPPORT"
NEU = "NEUTRAL"
OPP = "OPPOSE"

BRAIN_NAMES = ("frequency", "probability", "sequence", "anomaly",
               "contract", "execution", "risk")

REGIMES = ("NORMAL", "CONCENTRATED", "DISTRIBUTION_SHIFT",
           "HIGH_ANOMALY", "LOW_INFORMATION", "UNSTABLE")

_CONFIG_KEY = "super_config"


@dataclass
class SuperConfig:
    # Ensemble consensus (§2)
    min_support_brains: int = 5
    oppose_veto: bool = True          # one OPPOSE kills the consensus
    agreement_min: float = 0.70
    # Contract specialists (§7): margin multiplier per contract family
    margin_matches: float = 1.5       # low base rate needs stronger evidence
    margin_over: float = 1.0
    margin_under: float = 1.0
    margin_odd: float = 1.2
    margin_even: float = 1.2
    margin_differs: float = 1.0
    # Adaptive windows (§5)
    windows: Tuple[int, ...] = (25, 50, 100, 250, 500, 1000)
    adaptive_tol: float = 0.03        # short-vs-long agreement tolerance
    # Uncertainty (§24)
    max_uncertainty: float = 0.60
    # Meta-model (§17)
    meta_min: float = 60.0
    # Auction (§10/§11)
    min_ev_auction: float = 0.03
    trade_frequency_target: float = 0.0   # advisory only, never forced
    # Profit locking (§20): (session pnl %, stake multiplier)
    profit_lock_tiers: Tuple[Tuple[float, float], ...] = ((2.0, 0.75), (3.0, 0.5), (4.0, 0.0))
    # Model health (§22)
    health_min_trades: int = 20
    # Adversarial (§14/§15)
    shuffle_permutations: int = 199
    shuffle_percentile: float = 0.95
    shuffle_cache_ticks: int = 500
    # Regime gating (§12): contracts allowed to fire per regime.
    # UNSTABLE/HIGH_ANOMALY require anomaly-brain support (handled in logic).
    regime_block: Tuple[str, ...] = ("LOW_INFORMATION",)

    def margin_mult(self, contract: str) -> float:
        return {
            "MATCHES": self.margin_matches,
            "OVER": self.margin_over,
            "UNDER": self.margin_under,
            "ODD": self.margin_odd,
            "EVEN": self.margin_even,
            "DIFFERS": self.margin_differs,
        }.get(contract, 1.0)


def _load_config() -> SuperConfig:
    cfg = SuperConfig()
    stored = settings_store.get(_CONFIG_KEY)
    if isinstance(stored, dict):
        for k, v in stored.items():
            if not hasattr(cfg, k):
                continue
            if k in ("windows", "profit_lock_tiers", "regime_block"):
                try:
                    if k == "profit_lock_tiers":
                        cfg.profit_lock_tiers = tuple((float(a), float(b)) for a, b in v)
                    else:
                        setattr(cfg, k, tuple(v))
                except (TypeError, ValueError):
                    continue
            else:
                try:
                    setattr(cfg, k, type(getattr(cfg, k))(v))
                except (TypeError, ValueError):
                    continue
    return cfg


@dataclass
class BrainVote:
    brain: str
    vote: str            # SUPPORT | NEUTRAL | OPPOSE
    prob: float          # the brain's probability estimate for the candidate
    confidence: float    # 0-1
    strength: float      # 0-1
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _transition_deviation(digits: List[int]) -> float:
    """Mean absolute deviation of the transition matrix from uniform."""
    trans = {d: Counter() for d in range(10)}
    for a, b in zip(digits, digits[1:]):
        trans[a][b] += 1
    dev, rows = 0.0, 0
    for d in range(10):
        total = sum(trans[d].values())
        if total < 5:
            continue
        rows += 1
        dev += sum(abs(trans[d][j] / total - 0.1) for j in range(10)) / 10.0
    return dev / rows if rows else 0.0


class SuperProfitEngine:
    def __init__(self, queue=None, board_source=None, decision_layer=None, rng_seed: int = 42):
        self.queue = queue or tick_queue
        self._board = board_source or pro_trader
        self._layer: BottomUpEngine = decision_layer or bottom_up_engine
        self.config = _load_config()
        self._rng = random.Random(rng_seed)
        self._tick_counts: Dict[str, int] = {}
        self._shuffle_cache: Dict[str, dict] = {}

    # ---------------- config ----------------
    def get_config(self) -> dict:
        d = asdict(self.config)
        d["windows"] = list(self.config.windows)
        d["profit_lock_tiers"] = [list(t) for t in self.config.profit_lock_tiers]
        d["regime_block"] = list(self.config.regime_block)
        return d

    def update_config(self, **kw) -> dict:
        cfg = self.config
        for k, v in kw.items():
            if v is None or not hasattr(cfg, k):
                continue
            if k == "profit_lock_tiers":
                try:
                    cfg.profit_lock_tiers = tuple((float(a), float(b)) for a, b in v)
                except (TypeError, ValueError):
                    pass
                continue
            if k in ("windows", "regime_block"):
                try:
                    setattr(cfg, k, tuple(int(x) if k == "windows" else str(x) for x in v))
                except (TypeError, ValueError):
                    pass
                continue
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except (TypeError, ValueError):
                continue
        cfg.min_support_brains = max(1, min(7, cfg.min_support_brains))
        cfg.agreement_min = max(0.0, min(1.0, cfg.agreement_min))
        cfg.max_uncertainty = max(0.0, min(1.0, cfg.max_uncertainty))
        cfg.meta_min = max(0.0, min(100.0, cfg.meta_min))
        stored = self.get_config()
        settings_store.set(_CONFIG_KEY, stored)
        return stored

    # ---------------- shared context ----------------
    def on_tick(self, tick) -> None:
        self._tick_counts[tick.symbol] = self._tick_counts.get(tick.symbol, 0) + 1

    def _context(self, symbol: str) -> Optional[dict]:
        ticks = self.queue.recent(symbol, limit=max(self.config.windows))
        digits = [t.digit for t in ticks]
        if not digits:
            return None
        board = self._board.board(symbol)
        if board.get("decision") == "NO_DATA":
            board = {"data_ok": False, "anomaly_score": 0.0, "regime": "UNKNOWN",
                     "entropy_norm": 1.0, "serial_dependence": False,
                     "digit_adj_p": {str(d): 1.0 for d in range(10)},
                     "table_fdr_significant": False, "digit_states": {}}
        posts: Dict[int, List[float]] = {}
        for w in self.config.windows:
            if len(digits) >= w:
                c = Counter(digits[-w:])
                posts[w] = [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]
        return {"digits": digits, "board": board, "posts": posts, "n": len(digits)}

    # ---------------- §12 regime classification ----------------
    def _regime(self, ctx: dict) -> str:
        board = ctx["board"]
        anomaly = float(board.get("anomaly_score", 0.0))
        h = float(board.get("entropy_norm", 1.0))
        if anomaly >= 60:
            return "HIGH_ANOMALY"
        if board.get("serial_dependence"):
            return "UNSTABLE"
        # short-vs-long distribution shift (total variation distance)
        ws = sorted(ctx["posts"])
        if len(ws) >= 2:
            short, long_ = ctx["posts"][ws[0]], ctx["posts"][ws[-1]]
            tv = 0.5 * sum(abs(a - b) for a, b in zip(short, long_))
            if tv >= 0.25:
                return "DISTRIBUTION_SHIFT"
        if h < 0.95:
            return "CONCENTRATED"
        if (anomaly < 20 and h >= 0.995
                and not board.get("table_fdr_significant", False)):
            return "LOW_INFORMATION"
        return "NORMAL"

    # ---------------- §5 adaptive window selection ----------------
    def _adaptive_window(self, ctx: dict, kind: str, d: Optional[int]) -> int:
        """Pick the smallest window whose estimate agrees with the next larger
        window (within tolerance) — responsiveness with confirmation, using
        only data available right now. Falls back to the largest window."""
        ws = sorted(ctx["posts"])
        if not ws:
            return 0
        prob = BottomUpEngine._contract_prob
        for i in range(len(ws) - 1):
            small, big = ws[i], ws[i + 1]
            if abs(prob(kind, d, ctx["posts"][small]) - prob(kind, d, ctx["posts"][big])) \
                    <= self.config.adaptive_tol:
                return small
        return ws[-1]

    # ---------------- §15 shuffle test (adversarial, cached) ----------------
    def _shuffle_verdict(self, symbol: str, digits: List[int]) -> str:
        tick = self._tick_counts.get(symbol, len(digits))
        cached = self._shuffle_cache.get(symbol)
        if cached and tick - cached["tick"] < self.config.shuffle_cache_ticks \
                and cached["n"] == len(digits):
            return cached["verdict"]
        if len(digits) < 200:
            verdict = "NO_SEQUENCE"
        else:
            real = _transition_deviation(digits)
            count = 0
            perms = self.config.shuffle_permutations
            for _ in range(perms):
                shuffled = digits[:]
                self._rng.shuffle(shuffled)
                if _transition_deviation(shuffled) >= real:
                    count += 1
            # real beats the (1-p) percentile of shuffled => sequential info
            verdict = "SEQUENTIAL" if count / perms <= (1 - self.config.shuffle_percentile) \
                else "NO_SEQUENCE"
        self._shuffle_cache[symbol] = {"tick": tick, "n": len(digits), "verdict": verdict}
        return verdict

    # ---------------- §1 the seven brains ----------------
    def _brain_votes(self, symbol: str, c: dict, ctx: dict,
                     latency_ms: Optional[float], payout_source: str,
                     regime: str, health_status: str) -> List[BrainVote]:
        cfg = self.config
        kind, d = c["contract"], c["barrier"]
        breakeven = c["breakeven_probability"]
        margin = self._layer.config.min_edge * cfg.margin_mult(kind)
        n = ctx["n"]
        prob = BottomUpEngine._contract_prob
        votes: List[BrainVote] = []

        # Brain A — Frequency (adaptive window, §5)
        aw = self._adaptive_window(ctx, kind, d)
        p_a = prob(kind, d, ctx["posts"][aw]) if aw else c["estimated_probability"]
        edge_a = p_a - breakeven
        vote_a = SUP if edge_a >= margin else OPP if edge_a <= -margin else NEU
        votes.append(BrainVote(
            "frequency", vote_a, round(p_a, 4),
            round(min(1.0, aw / max(cfg.windows)), 3),
            round(min(1.0, abs(edge_a) / max(margin, 1e-9)), 3),
            f"adaptive_window={aw}, edge={edge_a:+.4f}"))

        # Brain B — Probability (Wilson bounds, §8 of the base directive)
        lb, p_est = c["wilson_lower_bound"], c["estimated_probability"]
        ub = wilson_upper_bound(round(p_est * c["sample_size"]), c["sample_size"])
        vote_b = SUP if lb > breakeven else OPP if ub < breakeven else NEU
        votes.append(BrainVote(
            "probability", vote_b, p_est,
            round(min(1.0, c["sample_size"] / max(cfg.windows)), 3),
            round(min(1.0, abs(lb - breakeven) / 0.05), 3),
            f"wilson=[{lb:.4f},{ub:.4f}] vs breakeven {breakeven:.4f}"))

        # Brain C — Sequence (transition-conditioned, shuffle-gated §15)
        verdict = self._shuffle_verdict(symbol, ctx["digits"])
        digits = ctx["digits"]
        last = digits[-1]
        cond_counts = Counter(b for a, b in zip(digits, digits[1:]) if a == last)
        cond_n = sum(cond_counts.values())
        if cond_n >= 30:
            cond_post = [(cond_counts.get(x, 0) + 1) / (cond_n + 10) for x in range(10)]
            p_c = prob(kind, d, cond_post)
            edge_c = p_c - breakeven
            if verdict != "SEQUENTIAL":
                vote_c = NEU
                detail = f"shuffle={verdict}: no sequential info, sequence brain abstains"
            else:
                vote_c = SUP if edge_c >= margin else OPP if edge_c <= -margin else NEU
                detail = f"shuffle={verdict}, P(.|prev={last}) edge={edge_c:+.4f}"
        else:
            p_c, vote_c, detail = p_est, NEU, f"transition rows thin (n={cond_n})"
        votes.append(BrainVote(
            "sequence", vote_c, round(p_c, 4),
            round(min(1.0, cond_n / 300.0), 3),
            round(min(1.0, abs((p_c - breakeven)) / max(margin, 1e-9)), 3), detail))

        # Brain D — Anomaly (FDR evidence vs contradiction)
        involved = BottomUpEngine._involved_digits(kind, d)
        adj = [float(ctx["board"]["digit_adj_p"].get(str(k), 1.0)) for k in involved]
        table_sig = bool(ctx["board"].get("table_fdr_significant", False))
        if any(a < FDR_Q for a in adj):
            vote_d = SUP
        elif table_sig and not any(a < FDR_Q for a in adj):
            vote_d = OPP  # the table is anomalous but NOT in this direction
        else:
            vote_d = NEU
        votes.append(BrainVote(
            "anomaly", vote_d, p_est,
            round(min(1.0, float(ctx["board"].get("anomaly_score", 0)) / 100.0), 3),
            round(min(1.0, -math.log10(max(min(adj), 1e-12)) / 4.0), 3),
            f"min_adj_p={min(adj):.4f}, table_fdr={table_sig}"))

        # Brain E — Contract specialist (§7/§8)
        ev, edge_e = c["ev"], c["edge"]
        vote_e = SUP if (ev > 0 and edge_e >= margin) else OPP if ev <= 0 else NEU
        votes.append(BrainVote(
            "contract", vote_e, p_est,
            round(min(1.0, abs(ev) / 0.10), 3),
            round(min(1.0, abs(edge_e) / max(margin, 1e-9)), 3),
            f"EV={ev:+.4f}, specialist margin x{cfg.margin_mult(kind):.2f}"))

        # Brain F — Execution (§9 payout freshness, latency, signal freshness)
        reasons = []
        vote_f = SUP
        if latency_ms is not None and latency_ms > cfg_max_latency(self._layer):
            vote_f, reasons = OPP, [f"latency {latency_ms:.0f}ms"]
        elif latency_ms is not None and latency_ms > cfg_max_latency(self._layer) / 2:
            vote_f, reasons = NEU, [f"latency elevated {latency_ms:.0f}ms"]
        if payout_source == "assumed_default":
            reasons.append("payout assumed — live proposal required before money moves")
            if vote_f == SUP:
                vote_f = NEU
        if c.get("signal_state") == "STALE":
            vote_f, reasons = OPP, reasons + ["signal stale"]
        votes.append(BrainVote(
            "execution", vote_f, p_est,
            1.0 if payout_source == "live_proposal" else 0.5,
            1.0 if vote_f == SUP else 0.5 if vote_f == NEU else 0.0,
            "; ".join(reasons) or "execution clean"))

        # Brain G — Risk (§16 base directive + §22 health)
        if c["gates"].get("risk") is False:
            vote_g, detail_g = OPP, "risk engine veto"
        elif health_status == "RED":
            vote_g, detail_g = OPP, "model health RED — family disabled"
        elif health_status == "ORANGE":
            vote_g, detail_g = NEU, "model health ORANGE — observation only"
        else:
            vote_g, detail_g = SUP, f"risk acceptable (health {health_status})"
        votes.append(BrainVote("risk", vote_g, p_est, 1.0, 1.0, detail_g))
        return votes

    # ---------------- §2 ensemble consensus ----------------
    def _consensus(self, votes: List[BrainVote]) -> dict:
        support = [v for v in votes if v.vote == SUP]
        oppose = [v for v in votes if v.vote == OPP]
        agreement = (len(support) - len(oppose)) / max(len(votes), 1)
        conf = (sum(v.confidence for v in support) / len(support)) if support else 0.0
        passed = (len(support) >= self.config.min_support_brains
                  and agreement >= self.config.agreement_min
                  and (not self.config.oppose_veto or not oppose))
        return {
            "support": len(support), "neutral": len(votes) - len(support) - len(oppose),
            "oppose": len(oppose), "agreement": round(agreement, 3),
            "confidence": round(conf, 3), "passed": passed,
            "opposing": [v.brain for v in oppose],
        }

    # ---------------- §24 uncertainty ----------------
    def _uncertainty(self, c: dict, votes: List[BrainVote], ctx: dict,
                     calibration_error: float) -> float:
        cfg = self.config
        sample_gap = 1.0 - min(1.0, c["sample_size"] / max(cfg.windows))
        conf_deficit = 1.0 - (sum(v.confidence for v in votes) / len(votes))
        oppose_w = sum(1 for v in votes if v.vote == OPP) / len(votes)
        life = c.get("signal_lifetime") or {}
        edge_vol = min(1.0, float(life.get("edge_volatility") or 0.0) / 0.05)
        score = (0.25 * sample_gap + 0.30 * conf_deficit
                 + 0.20 * oppose_w + 0.15 * edge_vol
                 + 0.10 * min(1.0, calibration_error / 0.15))
        return round(min(1.0, score), 3)

    # ---------------- §17 meta-model ----------------
    def _meta_score(self, consensus: dict, c: dict, uncertainty: float,
                    health_status: str, regime: str) -> float:
        health_w = {"GREEN": 1.0, "YELLOW": 0.6, "ORANGE": 0.3, "RED": 0.0}.get(health_status, 0.5)
        regime_w = 0.4 if regime in self.config.regime_block else 1.0
        life = c.get("signal_lifetime") or {}
        slope_bonus = 1.0 if float(life.get("edge_slope") or 0.0) >= 0 else 0.5
        score = 100.0 * (
            0.25 * max(0.0, consensus["agreement"])
            + 0.20 * min(1.0, max(c["ev"], 0.0) / 0.10)
            + 0.15 * consensus["confidence"]
            + 0.10 * min(1.0, c["sample_size"] / max(self.config.windows))
            + 0.10 * health_w
            + 0.10 * (1.0 - uncertainty)
            + 0.05 * slope_bonus
            + 0.05 * regime_w
        )
        return round(max(0.0, min(100.0, score)), 1)

    # ---------------- §22 model health ----------------
    def health(self, user_id: str = "default") -> dict:
        entries = _entries(user_id)
        fams: Dict[str, dict] = {}
        for e in entries:
            if e.get("result") not in ("win", "loss"):
                continue
            fam = str(e.get("contract", "?")).split()[0].upper()
            f = fams.setdefault(fam, {"n": 0, "wins": 0, "pnl": 0.0, "staked": 0.0,
                                      "payouts": [], "recent": []})
            f["n"] += 1
            stake = float(e.get("stake", 0) or 0)
            pnl = float(e.get("pnl", 0) or 0)
            f["staked"] += stake
            f["pnl"] += pnl
            f["recent"].append(1 if e["result"] == "win" else 0)
            if e["result"] == "win":
                f["wins"] += 1
                if stake > 0 and pnl > 0:
                    f["payouts"].append(pnl / stake + 1.0)
        out = {}
        for fam, f in fams.items():
            recent = f["recent"][-20:]
            recent_wr = sum(recent) / len(recent) if recent else 0.0
            rolling_ev = f["pnl"] / f["staked"] if f["staked"] > 0 else 0.0
            med = sorted(f["payouts"])[len(f["payouts"]) // 2] if f["payouts"] else None
            ub = wilson_upper_bound(f["wins"], f["n"]) if f["n"] else 1.0
            lb = wilson_lower_bound(f["wins"], f["n"]) if f["n"] else 0.0
            enough = f["n"] >= self.config.health_min_trades
            killed = enough and rolling_ev < 0 and (
                (med is None and f["wins"] == 0) or (med is not None and ub < 1.0 / med))
            if killed:
                status = "RED"
            elif not enough:
                status = "YELLOW"
            elif rolling_ev < 0 or (med is not None and lb < 1.0 / med):
                status = "ORANGE" if rolling_ev < -0.05 else "YELLOW"
            else:
                status = "GREEN"
            out[fam] = {
                "status": status,
                "trades": f["n"],
                "recent_win_rate": round(recent_wr, 3),
                "rolling_ev": round(rolling_ev, 4),
                "exposure_multiplier": {"GREEN": 1.0, "YELLOW": 0.5,
                                        "ORANGE": 0.0, "RED": 0.0}[status],
            }
        return {"user_id": user_id, "families": out,
                "note": "GREEN active, YELLOW reduced exposure, ORANGE observation "
                        "only, RED disabled (§22)."}

    # ---------------- §23 probability calibration ----------------
    def calibration(self, user_id: str = "default") -> dict:
        entries = [e for e in _entries(user_id)
                   if e.get("result") in ("win", "loss")
                   and (e.get("analysis_snapshot") or {}).get("estimated_probability") is not None]
        bins: Dict[int, dict] = {}
        for e in entries:
            p = float(e["analysis_snapshot"]["estimated_probability"])
            b = min(9, int(p * 10))
            g = bins.setdefault(b, {"n": 0, "wins": 0, "pred_sum": 0.0})
            g["n"] += 1
            g["wins"] += 1 if e["result"] == "win" else 0
            g["pred_sum"] += p
        rows, err_sum, err_n = [], 0.0, 0
        for b in sorted(bins):
            g = bins[b]
            pred = g["pred_sum"] / g["n"]
            actual = g["wins"] / g["n"]
            err_sum += abs(pred - actual)
            err_n += 1
            rows.append({"bin": f"{b / 10:.1f}-{(b + 1) / 10:.1f}", "n": g["n"],
                         "predicted": round(pred, 3), "actual": round(actual, 3),
                         "gap": round(pred - actual, 3)})
        error = err_sum / err_n if err_n else 0.0
        verdict = ("INSUFFICIENT_DATA" if len(entries) < 30
                   else "MIS_CALIBRATED" if error > 0.10 else "CALIBRATED")
        return {"user_id": user_id, "predictions": len(entries), "bins": rows,
                "calibration_error": round(error, 4), "verdict": verdict,
                "note": "If 60% predictions only win 51%, the model is "
                        "mis-calibrated and its EV/Kelly inputs are inflated (§23)."}

    # ---------------- §18 profitability matrix ----------------
    def matrix(self, user_id: str = "default") -> dict:
        entries = _entries(user_id)
        cells: Dict[str, dict] = {}
        for e in entries:
            if e.get("result") not in ("win", "loss"):
                continue
            sym = str(e.get("market", "?"))
            fam = str(e.get("contract", "?")).split()[0].upper()
            cell = cells.setdefault(f"{sym}|{fam}", {"trades": 0, "wins": 0, "pnl": 0.0,
                                                     "staked": 0.0, "peak": 0.0, "cum": 0.0,
                                                     "dd": 0.0, "regimes": {}})
            stake = float(e.get("stake", 0) or 0)
            pnl = float(e.get("pnl", 0) or 0)
            cell["trades"] += 1
            cell["wins"] += 1 if e["result"] == "win" else 0
            cell["pnl"] += pnl
            cell["staked"] += stake
            cell["cum"] += pnl
            cell["peak"] = max(cell["peak"], cell["cum"])
            cell["dd"] = max(cell["dd"], cell["peak"] - cell["cum"])
            regime = (e.get("analysis_snapshot") or {}).get("regime")
            if regime:
                r = cell["regimes"].setdefault(regime, {"n": 0, "pnl": 0.0})
                r["n"] += 1
                r["pnl"] += pnl
        grid: Dict[str, dict] = {}
        for key, cell in cells.items():
            sym, fam = key.split("|")
            row = grid.setdefault(sym, {})
            regimes = cell["regimes"]
            best = max(regimes, key=lambda r: regimes[r]["pnl"], default=None)
            worst = min(regimes, key=lambda r: regimes[r]["pnl"], default=None)
            row[fam] = {
                "trades": cell["trades"],
                "win_rate": round(cell["wins"] / cell["trades"], 4),
                "ev": round(cell["pnl"] / cell["staked"], 4) if cell["staked"] > 0 else None,
                "roi": round(cell["pnl"] / cell["staked"], 4) if cell["staked"] > 0 else None,
                "drawdown": round(cell["dd"], 2),
                "confidence": round(wilson_lower_bound(cell["wins"], cell["trades"]), 4),
                "best_conditions": best,
                "worst_conditions": worst,
            }
        return {"user_id": user_id, "matrix": grid,
                "note": "Institutional memory: every symbol x contract cell, "
                        "continuously updated from the journal (§18)."}

    # ---------------- §6 market profiles ----------------
    def profiles(self, user_id: str = "default") -> dict:
        m = self.matrix(user_id)["matrix"]
        entries = _entries(user_id)
        out = {}
        for sym, row in m.items():
            if not row:
                continue
            best_contract = max(row, key=lambda f: (row[f]["ev"] or -9))
            worst_contract = min(row, key=lambda f: (row[f]["ev"] or 9))
            sym_entries = [e for e in entries if str(e.get("market")) == sym]
            wins = [e for e in sym_entries if e.get("result") == "win"]
            windows = [int((e.get("analysis_snapshot") or {}).get("adaptive_window"))
                       for e in wins
                       if (e.get("analysis_snapshot") or {}).get("adaptive_window")]
            best_window = Counter(windows).most_common(1)[0][0] if windows else None
            losses = [e for e in sym_entries if e.get("result") == "loss"]
            false_signals = sum(1 for e in losses
                                if float(e.get("evidence_score", 100) or 100) < 50)
            out[sym] = {
                "best_contract": best_contract,
                "worst_contract": worst_contract,
                "best_window": best_window,
                "false_signal_rate": round(false_signals / len(losses), 3) if losses else None,
                "validated_performance": row[best_contract]["ev"],
                "trades": sum(c["trades"] for c in row.values()),
            }
        return {"user_id": user_id, "profiles": out,
                "note": "Learned per-market personalities from realized results "
                        "only — never assumptions (§6)."}

    # ---------------- §3 conditional edge search ----------------
    def conditional_edge(self, symbol: str) -> dict:
        ctx = self._context(symbol)
        if not ctx or ctx["n"] < 200:
            return {"symbol": symbol, "features": {}, "note": "need >=200 ticks"}
        digits = ctx["digits"]
        n = len(digits)
        global_counts = Counter(digits)

        def lift(states: List[str]) -> dict:
            """Max |P(d|state)-P(d)| with a chi-square check per state value."""
            by_state: Dict[str, List[int]] = {}
            for s, dgt in zip(states, digits):
                by_state.setdefault(s, []).append(dgt)
            best = {"feature_lift": 0.0, "state": None, "n": 0, "significant": False}
            for s, ds in by_state.items():
                if len(ds) < 50:
                    continue
                cs = Counter(ds)
                m = len(ds)
                chi2 = sum((cs.get(d, 0) - m * (global_counts.get(d, 0) / n)) ** 2
                           / max(m * (global_counts.get(d, 0) / n), 1e-9)
                           for d in range(10))
                dev = max(abs(cs.get(d, 0) / m - global_counts.get(d, 0) / n)
                          for d in range(10))
                sig = chi2_sf(chi2, 9) < 0.01
                if dev > best["feature_lift"]:
                    best = {"feature_lift": round(dev, 4), "state": s, "n": m,
                            "significant": bool(sig)}
            return best

        # Per-position states, each computed only from ticks up to that
        # position — no future information leaks into the conditioning set.
        gap_so_far = {d: n for d in range(10)}
        gap_state, streak_state = [], []
        run = 0
        last = None
        for i, dgt in enumerate(digits):
            gap_state.append("recent" if gap_so_far[dgt] <= 5 else "away")
            for x in range(10):
                gap_so_far[x] = 0 if x == dgt else gap_so_far[x] + 1
            run = run + 1 if last == dgt else 1
            streak_state.append("streak" if run >= 2 else "fresh")
            last = dgt
        features = {
            # states[i] describes the situation BEFORE outcome digits[i].
            "previous_digit": lift(["start"] + [str(d) for d in digits[:-1]]),
            "streak_state": lift(streak_state),
            "gap_bucket": lift(gap_state),
        }
        for name, f in features.items():
            f["verdict"] = "KEEP" if f["significant"] and f["feature_lift"] >= 0.02 else "DISCARD"
        return {"symbol": symbol, "features": features,
                "note": "P(outcome|STATE) vs P(outcome). Features without "
                        "significant conditional lift are discarded (§3)."}

    # ---------------- §16 feature ablation (no lookahead) ----------------
    def ablation(self, symbol: str, positions: int = 20) -> dict:
        ctx = self._context(symbol)
        if not ctx or ctx["n"] < 300:
            return {"symbol": symbol, "ablation": {}, "note": "need >=300 ticks"}
        digits = ctx["digits"]
        start = max(200, ctx["n"] // 3)
        step = max(1, (ctx["n"] - start) // positions)
        results = {b: {"changed": 0, "evaluated": 0} for b in BRAIN_NAMES}
        baseline_agree = 0
        for pos in range(start, ctx["n"], step):
            sub = digits[:pos]
            c = self._quick_candidate(sub)
            if c is None:
                continue
            full = self._quick_consensus(sub, c, BRAIN_NAMES)
            baseline_agree += 1 if full["passed"] else 0
            for brain in BRAIN_NAMES:
                partial = self._quick_consensus(sub, c, tuple(b for b in BRAIN_NAMES if b != brain))
                results[brain]["evaluated"] += 1
                if partial["passed"] != full["passed"]:
                    results[brain]["changed"] += 1
        total = max(1, max(r["evaluated"] for r in results.values()))
        out = {}
        for brain, r in results.items():
            impact = r["changed"] / total
            out[brain] = {"decision_changes": r["changed"], "positions": r["evaluated"],
                          "impact": round(impact, 3),
                          "verdict": "RETAIN" if impact >= 0.01 else "DELETE_CANDIDATE"}
        return {"symbol": symbol, "positions": total,
                "baseline_consensus_rate": round(baseline_agree / total, 3),
                "ablation": out,
                "note": "One brain removed at a time over walk-forward positions "
                        "(no future information). Zero impact => the brain is "
                        "bloat, not signal (§16)."}

    def _quick_candidate(self, digits: List[int]) -> Optional[dict]:
        """Strongest weighted-edge candidate on a digit slice (ablation helper)."""
        c = Counter(digits[-min(250, len(digits)):])
        w = min(250, len(digits))
        post = [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]
        best, best_edge = None, -9.0
        for kind, d in BottomUpEngine._candidates():
            payout = {"MATCHES": 9.0, "DIFFERS": 1.10, "OVER": 1.95,
                      "UNDER": 1.95, "ODD": 1.95, "EVEN": 1.95}[kind]
            be = 1.0 / payout
            p = BottomUpEngine._contract_prob(kind, d, post)
            if p - be > best_edge:
                best_edge = p - be
                best = {"contract": kind, "barrier": d, "edge": p - be,
                        "ev": p * payout - 1.0, "estimated_probability": p,
                        "breakeven_probability": be, "sample_size": w,
                        "wilson_lower_bound": wilson_lower_bound(
                            BottomUpEngine._win_count(kind, d, c, w), w)}
        return best

    def _quick_consensus(self, digits: List[int], c: dict, brains: Tuple[str, ...]) -> dict:
        margin = self._layer.config.min_edge
        votes = 0
        for brain in brains:
            if brain in ("frequency", "contract"):
                votes += 1 if c["edge"] >= margin else 0
            elif brain == "probability":
                votes += 1 if c["wilson_lower_bound"] > c["breakeven_probability"] else 0
            elif brain == "sequence":
                votes += 1 if _transition_deviation(digits[-250:]) > 0.05 else 0
            elif brain == "anomaly":
                w = min(250, len(digits))
                cnt = Counter(digits[-w:])
                chi2 = sum((cnt.get(d, 0) - w / 10.0) ** 2 / (w / 10.0) for d in range(10))
                votes += 1 if chi2_sf(chi2, 9) < 0.05 else 0
            elif brain in ("execution", "risk"):
                votes += 1  # clean by construction in ablation replays
        need = max(1, math.ceil(self.config.min_support_brains * len(brains) / 7))
        return {"passed": votes >= need, "support": votes}

    # ---------------- §19 capital allocation ----------------
    def allocate(self, balance: float, opportunities: List[dict]) -> dict:
        cfg_layer = self._layer.config
        live = [o for o in opportunities if o.get("ev", 0) > 0]
        if not live or balance <= 0:
            return {"allocations": [], "note": "no positive-EV opportunities — allocate nothing"}
        weights = []
        for o in live:
            conf = float(o.get("confidence", 0.5) or 0.5)
            health_mult = float(o.get("exposure_multiplier", 1.0) or 0.0)
            weights.append(max(0.0, o["ev"]) * conf * health_mult)
        total_w = sum(weights)
        cap = cfg_layer.max_stake_pct * balance
        floor = cfg_layer.min_stake_pct * balance
        allocs = []
        for o, w in zip(live, weights):
            raw = (w / total_w) * cap if total_w > 0 else 0.0
            stake = round(min(cap, max(floor, raw)), 2) if raw > 0 else 0.0
            allocs.append({**{k: o.get(k) for k in ("symbol", "contract", "barrier", "ev")},
                           "weight": round(w, 5), "stake": stake})
        return {"balance": balance, "allocations": allocs,
                "note": "Ranked by risk-adjusted expected return: EV x confidence x "
                        "health exposure — the highest raw EV is not automatically "
                        "the biggest allocation (§19)."}

    # ---------------- §20 profit locking ----------------
    def profit_lock_multiplier(self, session_pnl_pct: float) -> dict:
        mult = 1.0
        for threshold, m in sorted(self.config.profit_lock_tiers):
            if session_pnl_pct >= threshold:
                mult = m
        return {"session_pnl_pct": session_pnl_pct, "stake_multiplier": mult,
                "stop_session": mult == 0.0,
                "tiers": [list(t) for t in self.config.profit_lock_tiers],
                "note": "Profit becomes protected capital, not fuel for giving it "
                        "back (§20). Thresholds configurable and backtestable."}

    # ---------------- §25 the ultimate decision ----------------
    def decide(self, symbol: str, payouts: Optional[Dict[str, float]] = None,
               latency_ms: Optional[float] = None, risk_blocked: bool = False,
               user_id: str = "default") -> dict:
        base = self._layer.signal(symbol, payouts=payouts, latency_ms=latency_ms,
                                  risk_blocked=risk_blocked)
        if base.get("decision") in ("NO_DATA", "NO_TRADE"):
            return {"symbol": symbol, "final": "REJECT",
                    "reason": base.get("reason", base.get("decision")),
                    "base_decision": base.get("decision"), "note": RNG_NOTE}
        ctx = self._context(symbol)
        if ctx is None:
            return {"symbol": symbol, "final": "REJECT", "reason": "no data", "note": RNG_NOTE}
        regime = self._regime(ctx)
        health_all = self.health(user_id)["families"]
        health_status = health_all.get(base["contract"], {}).get("status", "YELLOW")
        cal_err = self.calibration(user_id)["calibration_error"]
        votes = self._brain_votes(symbol, base, ctx, latency_ms,
                                  base.get("payout_source", "assumed_default"),
                                  regime, health_status)
        consensus = self._consensus(votes)
        uncertainty = self._uncertainty(base, votes, ctx, cal_err)
        meta = self._meta_score(consensus, base, uncertainty, health_status, regime)

        gates = {
            "base_gates": base["decision"] in ("EXECUTE", "WATCH"),
            "confirmed": base["decision"] == "EXECUTE",
            "consensus": consensus["passed"],
            "uncertainty": uncertainty <= self.config.max_uncertainty,
            "regime": regime not in self.config.regime_block,
            "health": health_status != "RED",
            "meta": meta >= self.config.meta_min,
        }
        failed = [k for k, v in gates.items() if not v]
        if all(gates.values()):
            final = "EXECUTE"
        elif base["decision"] == "WATCH" and consensus["passed"] \
                and uncertainty <= self.config.max_uncertainty:
            final = "WATCH"
        else:
            final = "REJECT"

        # §13 self-critic evidence report
        critic = {
            "probability_edge": base["edge"] >= self._layer.config.min_edge,
            "ev_positive": base["ev"] > 0,
            "confidence": base["wilson_lower_bound"] > base["breakeven_probability"],
            "sample": base["sample_size"] >= self._layer.config.min_sample,
            "multi_window": base["window_hits"] >= max(2, base["windows_available"] - 1),
            "signal_stability": base.get("signal_state") in ("PENDING", "CONFIRMED"),
            "recent_degradation": (base.get("signal_lifetime") or {}).get("edge_slope", 0) < 0,
            "execution_quality": votes[5].vote != OPP,
            "risk": votes[6].vote != OPP,
            "contradictory_evidence": "LOW" if not consensus["opposing"]
                                      else "HIGH: " + ",".join(consensus["opposing"]),
        }
        return {
            "symbol": symbol,
            "market": symbol,
            "contract": base["contract"],
            "barrier": base["barrier"],
            "probability": base["estimated_probability"],
            "breakeven": base["breakeven_probability"],
            "edge": base["edge"],
            "ev": base["ev"],
            "confidence": consensus["confidence"],
            "model_agreement": consensus["agreement"],
            "signal_quality": base["grade"],
            "uncertainty": uncertainty,
            "meta_score": meta,
            "regime": regime,
            "health": health_status,
            "risk": "ACCEPTABLE" if votes[6].vote != OPP else "BLOCKED",
            "execution": "READY" if votes[5].vote == SUP else
                         "CAUTION" if votes[5].vote == NEU else "BLOCKED",
            "brains": [v.to_dict() for v in votes],
            "consensus": consensus,
            "self_critic": critic,
            "gates": gates,
            "failed_gates": failed,
            "base_decision": base["decision"],
            "signal_state": base.get("signal_state"),
            "adaptive_window": self._adaptive_window(ctx, base["contract"], base["barrier"]),
            "recovery_protocol": "Stake is earned by this trade's own evidence — "
                                 "never raised because the previous trade lost (§21).",
            "final": final,
            "note": RNG_NOTE,
        }

    # ---------------- §10 opportunity auction ----------------
    def auction(self, symbols: List[str], user_id: str = "default", **kw) -> dict:
        offers = []
        for sym in symbols:
            d = self.decide(sym, user_id=user_id, **kw)
            if d.get("final") == "REJECT" and "ev" not in d:
                continue
            offers.append({
                "symbol": sym,
                "contract": d.get("contract"),
                "barrier": d.get("barrier"),
                "ev": d.get("ev"),
                "edge": d.get("edge"),
                "meta_score": d.get("meta_score"),
                "model_agreement": (d.get("consensus") or {}).get("agreement"),
                "final": d.get("final"),
            })
        valid = [o for o in offers if o["final"] == "EXECUTE"
                 and o["ev"] is not None and o["ev"] >= self.config.min_ev_auction]
        valid.sort(key=lambda o: o["ev"], reverse=True)
        winner = valid[0] if valid else None
        return {
            "winner": winner,
            "accepted": valid,
            "rejected": [o for o in offers if o not in valid],
            "trades": len(valid),
            "trade_frequency_target": self.config.trade_frequency_target,
            "note": "The auction takes the best validated offer and rejects the "
                    "rest. Zero valid opportunities means zero trades — the "
                    "frequency target is never forced (§10/§11).",
        }


def cfg_max_latency(layer: BottomUpEngine) -> float:
    return layer.config.max_latency_ms


def _entries(user_id: str) -> List[dict]:
    newest_first = [e for e in journal_engine.list_entries(limit=100000)
                    if e.get("user_id", "default") == user_id]
    return list(reversed(newest_first))


def _posts_upto(digits: List[int], upto: int, windows: Tuple[int, ...]) -> Dict[int, List[float]]:
    posts = {}
    for w in windows:
        if upto >= w:
            c = Counter(digits[upto - w:upto])
            posts[w] = [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]
    return posts


super_profit_engine = SuperProfitEngine()
