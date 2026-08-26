"""Bottom-Up Profitability Engine — the directive layer.

Mission order: reduce unnecessary losses first, improve decision quality
second, maximize risk-adjusted profitability third. Survival before profit.

This engine sits on top of the Pro Trader statistics (FDR-corrected
deviations, Dirichlet posteriors, Wilson bounds) and adds the directive's
decision discipline:

- Contract priority hierarchy (§1): MATCHES, OVER, UNDER, ODD, EVEN, then
  DIFFERS last. The hierarchy orders FOCUS among validated edges only — it
  never forces a trade. On a fair board the correct answer stays NO TRADE.
- Bottom-up pipeline (§4): every candidate must clear data quality,
  statistical significance, confidence (Wilson LB above breakeven), safety
  margin, multi-window stability, edge-decay and risk gates — in any order,
  but ALL of them. One failure is elimination.
- Safety margin (§7): edge must clear a configurable minimum (default +3pp;
  preferred +5pp). Thresholds are validated against the journal, never
  treated as permanent truths.
- Signal persistence (§11): a passing candidate is DETECTED, then must
  survive `confirmation_ticks` re-evaluations before it may execute.
- Edge decay (§12): every tracked signal carries an EDGE_LIFETIME —
  initial/current edge, slope, volatility. Decay below the floor cancels it.
- Opportunity score 0-100 and grade A+/A/B/C/D (§14/§15): the score NEVER
  overrides a hard rejection. Only A+/A are auto-executable.
- Loss post-mortem (§20), winning-trade analysis (§21), profitability
  scorecard (§24) and per-family strategy kill switches (§25).
- Martingale policy (§18): capped plans only, computed with the real
  required-recovery formula. Unlimited martingale is prohibited.

RNG honesty: synthetic indices are RNG-driven. These statistics are
descriptive, not predictive; the gates exist so that descriptive flukes
almost never become trades.
"""
import math
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from app.core.queue import tick_queue
from app.services.persistence import journal_engine, settings_store
from app.services.pro_trader import (
    DEFAULT_PAYOUTS,
    FDR_Q,
    MAX_LATENCY_MS,
    RNG_NOTE,
    _lin_slope,
    pro_trader,
    wilson_lower_bound,
)

# §1: focus hierarchy among VALIDATED edges. DIFFERS is always last.
CONTRACT_HIERARCHY = ("MATCHES", "OVER", "UNDER", "ODD", "EVEN", "DIFFERS")

# §20: post-mortem loss classes.
LOSS_CLASSES = (
    "STATISTICAL_LOSS", "SIGNAL_DECAY", "FALSE_SIGNAL", "BAD_ENTRY",
    "PAYOUT_PROBLEM", "EXECUTION_DELAY", "DATA_PROBLEM", "REGIME_CHANGE",
    "RISK_MANAGEMENT", "MODEL_ERROR", "NORMAL_RANDOM_LOSS",
)

_CONFIG_KEY = "bottom_up_config"


@dataclass
class BottomUpConfig:
    # §7 safety margin (probability points, not percent)
    min_edge: float = 0.03
    preferred_edge: float = 0.05
    min_sample: int = 100
    # §11 signal persistence
    confirmation_ticks: int = 5
    max_signal_age_ticks: int = 30
    # §12 edge decay
    edge_cancel_below: float = 0.0
    edge_decay_slope: float = -0.005
    # §5 short/medium/long windows
    window_short: int = 50
    window_medium: int = 250
    window_long: int = 1000
    # §15 grade thresholds (score 0-100)
    grade_aplus: float = 85.0
    grade_a: float = 70.0
    grade_b: float = 50.0
    grade_c: float = 30.0
    auto_execute_grades: Tuple[str, ...] = ("A+", "A")
    max_latency_ms: float = MAX_LATENCY_MS
    # §17 default risk profile (advisory — risk_guard owns enforcement)
    max_stake_pct: float = 0.01
    min_stake_pct: float = 0.0025
    max_simultaneous_trades: int = 1
    max_exposure_per_symbol: int = 1
    session_stop_pct: float = 0.05
    daily_stop_pct: float = 0.08
    consecutive_loss_pause: int = 3
    severe_loss_stop: int = 5
    martingale_enabled: bool = False  # §18: OFF unless explicitly researched

    @property
    def windows(self) -> Tuple[int, int, int]:
        return (self.window_short, self.window_medium, self.window_long)


def _load_config() -> BottomUpConfig:
    cfg = BottomUpConfig()
    stored = settings_store.get(_CONFIG_KEY)
    if isinstance(stored, dict):
        for k, v in stored.items():
            if hasattr(cfg, k) and k != "auto_execute_grades":
                try:
                    setattr(cfg, k, v)
                except (TypeError, ValueError):
                    continue
        if isinstance(stored.get("auto_execute_grades"), (list, tuple)):
            cfg.auto_execute_grades = tuple(stored["auto_execute_grades"])
    try:
        env = os.environ.get("BU_MIN_EDGE")
        if env:
            cfg.min_edge = float(env)
        env = os.environ.get("BU_CONFIRMATION_TICKS")
        if env:
            cfg.confirmation_ticks = int(env)
    except ValueError:
        pass
    return cfg


def wilson_upper_bound(wins: int, n: int, z: float = 1.96) -> float:
    if n <= 0:
        return 1.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (center + margin) / denom)


def _volatility(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


@dataclass
class TrackedSignal:
    """§11/§12: a detected edge with a lifetime, not an instant order."""
    symbol: str
    contract: str
    barrier: Optional[int]
    detected_at_tick: int
    initial_edge: float
    edges: List[float] = field(default_factory=list)
    state: str = "PENDING"  # PENDING | CONFIRMED | CANCELLED | STALE
    state_reason: str = ""
    last_eval_tick: int = 0

    @property
    def key(self) -> Tuple[str, str, Optional[int]]:
        return (self.symbol, self.contract, self.barrier)

    def lifetime(self) -> dict:
        return {
            "initial_edge": round(self.initial_edge, 4),
            "current_edge": round(self.edges[-1], 4) if self.edges else None,
            "edge_slope": round(_lin_slope(self.edges), 5),
            "edge_volatility": round(_volatility(self.edges), 5),
            "observations": len(self.edges),
        }

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("edges", None)
        return {**d, **self.lifetime()}


class BottomUpEngine:
    def __init__(self, queue=None, board_source=None):
        self.queue = queue or tick_queue
        self._board_source = board_source or pro_trader
        self.config = _load_config()
        self._signals: Dict[Tuple[str, str, Optional[int]], TrackedSignal] = {}
        self._tick_counts: Dict[str, int] = {}
        self._stats = {
            "signals_detected": 0,
            "signals_confirmed": 0,
            "signals_cancelled": 0,
            "signals_stale": 0,
        }

    # ---------------- config ----------------
    def get_config(self) -> dict:
        return asdict(self.config)

    def update_config(self, **kw) -> dict:
        cfg = self.config
        for k, v in kw.items():
            if v is None or not hasattr(cfg, k):
                continue
            if k == "auto_execute_grades":
                if isinstance(v, (list, tuple)) and all(g in ("A+", "A", "B", "C", "D") for g in v):
                    cfg.auto_execute_grades = tuple(v)
                continue
            try:
                setattr(cfg, k, type(getattr(cfg, k))(v))
            except (TypeError, ValueError):
                continue
        cfg.min_edge = max(0.0, min(0.5, cfg.min_edge))
        cfg.preferred_edge = max(cfg.min_edge, min(0.5, cfg.preferred_edge))
        cfg.confirmation_ticks = max(0, min(100, cfg.confirmation_ticks))
        cfg.min_sample = max(10, min(2000, cfg.min_sample))
        stored = asdict(cfg)
        stored["auto_execute_grades"] = list(cfg.auto_execute_grades)
        settings_store.set(_CONFIG_KEY, stored)
        return self.get_config()

    # ---------------- §3 contract probability models ----------------
    @staticmethod
    def _candidates() -> List[Tuple[str, Optional[int]]]:
        out: List[Tuple[str, Optional[int]]] = []
        for kind in CONTRACT_HIERARCHY:
            if kind in ("MATCHES", "DIFFERS"):
                out += [(kind, d) for d in range(10)]
            elif kind == "OVER":
                out += [(kind, d) for d in range(0, 9)]
            elif kind == "UNDER":
                out += [(kind, d) for d in range(1, 10)]
            else:
                out.append((kind, None))
        return out

    @staticmethod
    def _contract_prob(kind: str, d: Optional[int], post: List[float]) -> float:
        if kind == "MATCHES":
            return post[d]
        if kind == "DIFFERS":
            return 1.0 - post[d]
        if kind == "OVER":
            return sum(post[k] for k in range(d + 1, 10))
        if kind == "UNDER":
            return sum(post[k] for k in range(0, d))
        if kind == "ODD":
            return sum(post[k] for k in (1, 3, 5, 7, 9))
        return sum(post[k] for k in (0, 2, 4, 6, 8))

    @staticmethod
    def _win_count(kind: str, d: Optional[int], counts: Counter, total: int) -> int:
        if kind == "MATCHES":
            return counts.get(d, 0)
        if kind == "DIFFERS":
            return total - counts.get(d, 0)
        if kind == "OVER":
            return sum(counts.get(k, 0) for k in range(d + 1, 10))
        if kind == "UNDER":
            return sum(counts.get(k, 0) for k in range(0, d))
        if kind == "ODD":
            return sum(counts.get(k, 0) for k in (1, 3, 5, 7, 9))
        return sum(counts.get(k, 0) for k in (0, 2, 4, 6, 8))

    @staticmethod
    def _involved_digits(kind: str, d: Optional[int]) -> List[int]:
        if kind in ("MATCHES", "DIFFERS"):
            return [d]
        if kind == "OVER":
            return list(range(d + 1, 10))
        if kind == "UNDER":
            return list(range(0, d))
        if kind == "ODD":
            return [1, 3, 5, 7, 9]
        return [0, 2, 4, 6, 8]

    # ---------------- §14 opportunity score ----------------
    def _score(self, *, edge, ev, lb, breakeven, n, hits, avail, persistence,
               board, latency_ms) -> float:
        cfg = self.config
        prob_quality = min(1.0, max(edge, 0.0) / max(cfg.preferred_edge, 1e-9))
        ev_score = min(1.0, max(ev, 0.0) / 0.10)
        conf_score = min(1.0, max(lb - breakeven, 0.0) / 0.05)
        sample_score = min(1.0, n / max(cfg.window_long, 1))
        stability = hits / avail if avail else 0.0
        market = 1.0 - board.get("anomaly_score", 50.0) / 100.0
        execution = 1.0 if latency_ms is None else (
            1.0 if latency_ms <= cfg.max_latency_ms / 2 else 0.5
        )
        score = 100.0 * (
            0.20 * prob_quality
            + 0.15 * ev_score
            + 0.15 * conf_score
            + 0.10 * sample_score
            + 0.15 * stability
            + 0.10 * persistence
            + 0.10 * market
            + 0.05 * execution
        )
        return round(max(0.0, min(100.0, score)), 1)

    def _grade(self, score: float, gates_passed: bool) -> str:
        if not gates_passed:
            return "D"  # §14: the score never overrides a hard rejection
        cfg = self.config
        if score >= cfg.grade_aplus:
            return "A+"
        if score >= cfg.grade_a:
            return "A"
        if score >= cfg.grade_b:
            return "B"
        if score >= cfg.grade_c:
            return "C"
        return "D"

    # ---------------- §4 the pipeline ----------------
    def evaluate(
        self,
        symbol: str,
        payouts: Optional[Dict[str, float]] = None,
        latency_ms: Optional[float] = None,
        risk_blocked: bool = False,
    ) -> dict:
        cfg = self.config
        n_all = self.queue.count(symbol)
        ticks = self.queue.recent(symbol, limit=max(cfg.window_long, cfg.min_sample))
        digits = [t.digit for t in ticks]
        n = len(digits)
        if n == 0:
            return {"symbol": symbol, "n_ticks": 0, "candidates": [], "decision": "NO_DATA"}
        board = self._board_source.board(symbol)
        if board.get("decision") == "NO_DATA":
            board = {"data_ok": False, "anomaly_score": 0.0, "regime": "UNKNOWN",
                     "digit_adj_p": {str(d): 1.0 for d in range(10)},
                     "table_fdr_significant": False}

        payout_map = dict(DEFAULT_PAYOUTS)
        payout_source = "assumed_default"
        if payouts:
            payout_map.update({k: float(v) for k, v in payouts.items() if v})
            payout_source = "live_proposal"

        avail_windows = [w for w in cfg.windows if n >= w]
        posts: Dict[int, List[float]] = {}
        for w in avail_windows:
            c = Counter(digits[-w:])
            posts[w] = [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]
        eval_w = max(avail_windows) if avail_windows else n
        counts_eval = Counter(digits[-eval_w:])
        seg = digits[-eval_w:]
        long_w = max(avail_windows) if avail_windows else None

        tick_idx = self._tick_counts.get(symbol, n_all)
        candidates = []
        for hier_idx, (kind, d) in enumerate(self._candidates()):
            payout = payout_map[kind]
            breakeven = 1.0 / payout  # §6
            # §5: weighted short/medium/long estimate
            if avail_windows:
                wts = [1.0 / (i + 1) for i in range(len(avail_windows))]
                p_est = sum(
                    wt * self._contract_prob(kind, d, posts[w])
                    for wt, w in zip(wts, avail_windows)
                ) / sum(wts)
                hits = sum(
                    1 for w in avail_windows
                    if self._contract_prob(kind, d, posts[w]) > breakeven
                )
            else:
                p_est = self._contract_prob(
                    kind, d, [(counts_eval.get(x, 0) + 1) / (eval_w + 10) for x in range(10)]
                )
                hits = 0
            ev = p_est * payout - 1.0  # §6
            edge = p_est - breakeven
            lb = wilson_lower_bound(self._win_count(kind, d, counts_eval, eval_w), eval_w)

            adj_p = min(
                float(board["digit_adj_p"].get(str(k), 1.0))
                for k in self._involved_digits(kind, d)
            )
            stats_ok = adj_p < FDR_Q or board.get("table_fdr_significant", False)

            # §12: chronological chunk slope over the eval window
            chunk_len = max(1, len(seg) // 5)
            chunk_edges = []
            for ci in range(5):
                chunk = seg[ci * chunk_len:(ci + 1) * chunk_len]
                if not chunk:
                    continue
                cc = Counter(chunk)
                cw = len(chunk)
                cpost = [(cc.get(x, 0) + 1) / (cw + 10) for x in range(10)]
                chunk_edges.append(self._contract_prob(kind, d, cpost) - breakeven)
            slope = _lin_slope(chunk_edges)

            # §10 false-signal filter: the LONG window must independently
            # clear the safety margin, or the "edge" is a short-lived spike.
            long_ok = (
                self._contract_prob(kind, d, posts[long_w]) - breakeven >= cfg.min_edge
                if long_w else edge >= cfg.min_edge
            )

            key = (symbol, kind, d)
            tracked = self._signals.get(key)
            persistence = 0.0
            if tracked and tracked.state in ("PENDING", "CONFIRMED"):
                persistence = min(1.0, len(tracked.edges) / max(cfg.confirmation_ticks, 1))

            gates = {
                "data_quality": bool(board.get("data_ok")),
                "sample": n >= cfg.min_sample,
                "statistical": stats_ok,
                "confidence": lb > breakeven,           # §8
                "safety_margin": edge >= cfg.min_edge,  # §7
                "stability": len(avail_windows) >= 2 and hits >= max(2, len(avail_windows) - 1),
                "long_term": long_ok,               # §10 anti-spike
                "edge_not_decaying": not (slope < cfg.edge_decay_slope and edge < cfg.min_edge),
                "latency": latency_ms is None or latency_ms <= cfg.max_latency_ms,
                "risk": not risk_blocked,               # §16: risk has authority
            }
            passed = all(gates.values())
            reasons = [k for k, v in gates.items() if not v]

            score = self._score(
                edge=edge, ev=ev, lb=lb, breakeven=breakeven, n=n,
                hits=hits, avail=len(avail_windows), persistence=persistence,
                board=board, latency_ms=latency_ms,
            )
            grade = self._grade(score, passed)

            candidates.append({
                "symbol": symbol,
                "contract": kind,
                "barrier": d,
                "name": f"{kind} {d}" if d is not None else kind,
                "hierarchy_rank": hier_idx,
                "estimated_probability": round(p_est, 4),
                "payout": payout,
                "payout_source": payout_source,
                "breakeven_probability": round(breakeven, 4),
                "edge": round(edge, 4),
                "ev": round(ev, 4),
                "wilson_lower_bound": round(lb, 4),
                "adjusted_p": round(adj_p, 4),
                "window_hits": hits,
                "windows_available": len(avail_windows),
                "edge_slope": round(slope, 5),
                "sample_size": n,
                "gates": gates,
                "decision": "PASS" if passed else "REJECT",
                "decision_reasons": reasons,
                "score": score,
                "grade": grade,
                "auto_executable": passed and grade in cfg.auto_execute_grades,
            })

        self._update_tracker(symbol, candidates, tick_idx)

        passing = [c for c in candidates if c["decision"] == "PASS"]
        best = max(passing, key=lambda c: (c["score"], -c["hierarchy_rank"]), default=None)
        return {
            "symbol": symbol,
            "n_ticks": n,
            "regime": board.get("regime"),
            "candidates": candidates,
            "passing": len(passing),
            "best": best,
            "decision": "OK",
            "note": RNG_NOTE,
        }

    # ---------------- §11/§12 signal persistence + decay ----------------
    def _update_tracker(self, symbol: str, candidates: List[dict], tick_idx: int) -> None:
        cfg = self.config
        by_key = {(c["contract"], c["barrier"]): c for c in candidates}
        # Live signals keep being tracked even when the edge thins — that is
        # when decay detection matters most.
        for key, sig in list(self._signals.items()):
            if key[0] != symbol or sig.state in ("CANCELLED", "STALE"):
                continue
            c = by_key.get((key[1], key[2]))
            if c is None or tick_idx - sig.last_eval_tick > cfg.max_signal_age_ticks:
                sig.state = "STALE"
                sig.state_reason = "signal aged out without confirmation"
                self._stats["signals_stale"] += 1
                continue
            sig.edges.append(c["edge"])
            sig.last_eval_tick = tick_idx
            life = sig.lifetime()
            if c["edge"] < cfg.edge_cancel_below:
                sig.state = "CANCELLED"
                sig.state_reason = f"edge fell to {c['edge']:.4f} (floor {cfg.edge_cancel_below})"
                self._stats["signals_cancelled"] += 1
            elif life["edge_slope"] < cfg.edge_decay_slope and c["edge"] < cfg.min_edge:
                sig.state = "CANCELLED"
                sig.state_reason = f"edge decaying (slope {life['edge_slope']}) below margin"
                self._stats["signals_cancelled"] += 1
            elif c["decision"] != "PASS" and c["decision_reasons"] != ["safety_margin"]:
                sig.state = "CANCELLED"
                sig.state_reason = f"evidence disappeared: {', '.join(c['decision_reasons'])}"
                self._stats["signals_cancelled"] += 1
            elif (sig.state == "PENDING" and c["decision"] == "PASS"
                  and tick_idx - sig.detected_at_tick >= cfg.confirmation_ticks):
                sig.state = "CONFIRMED"
                sig.state_reason = f"edge survived {cfg.confirmation_ticks} ticks"
                self._stats["signals_confirmed"] += 1
        # New detections come only from candidates passing every gate.
        for c in candidates:
            if c["decision"] != "PASS":
                continue
            key = (symbol, c["contract"], c["barrier"])
            existing = self._signals.get(key)
            if existing is None or existing.state in ("CANCELLED", "STALE"):
                self._signals[key] = TrackedSignal(
                    symbol=symbol, contract=c["contract"], barrier=c["barrier"],
                    detected_at_tick=tick_idx, initial_edge=c["edge"],
                    edges=[c["edge"]], last_eval_tick=tick_idx,
                )
                self._stats["signals_detected"] += 1

    def on_tick(self, tick) -> None:
        """§11: re-evaluate only symbols with live signals — near-zero cost
        when nothing is pending (the common, capital-preserving case)."""
        symbol = tick.symbol
        # Monotonic per-symbol counter; the queue caps at its maxlen, so it
        # cannot be the counter itself once the buffer is full.
        cur = self._tick_counts.get(symbol)
        if cur is None:
            cur = self.queue.count(symbol)
        self._tick_counts[symbol] = cur + 1
        if any(k[0] == symbol and s.state in ("PENDING", "CONFIRMED")
               for k, s in self._signals.items()):
            try:
                self.evaluate(symbol)
            except Exception:  # noqa: BLE001 — tracking must never break ingestion
                pass

    def tracker(self) -> dict:
        states = {"PENDING": [], "CONFIRMED": [], "CANCELLED": [], "STALE": []}
        for sig in self._signals.values():
            states[sig.state].append(sig.to_dict())
        return {
            "states": states,
            "stats": dict(self._stats),
            "config": {
                "confirmation_ticks": self.config.confirmation_ticks,
                "max_signal_age_ticks": self.config.max_signal_age_ticks,
                "edge_cancel_below": self.config.edge_cancel_below,
            },
            "note": "A signal must survive the confirmation window before it may "
                    "execute; decay below the floor cancels it. §11/§12.",
        }

    # ---------------- §13/§14/§15 ranking ----------------
    def signal(self, symbol: str, **kw) -> dict:
        e = self.evaluate(symbol, **kw)
        if e["decision"] == "NO_DATA":
            return {"symbol": symbol, "decision": "NO_DATA", "note": RNG_NOTE}
        best = e.get("best")
        if not best:
            return {
                "symbol": symbol,
                "decision": "NO_TRADE",
                "reason": "no candidate clears every hard gate — the honest position",
                "candidates_evaluated": len(e["candidates"]),
                "note": RNG_NOTE,
            }
        key = (symbol, best["contract"], best["barrier"])
        tracked = self._signals.get(key)
        confirmed = tracked is not None and tracked.state == "CONFIRMED"
        if best["auto_executable"] and confirmed:
            decision = "EXECUTE"
        elif best["decision"] == "PASS":
            decision = "WATCH"  # §11: detected, waiting out the confirmation window
        else:
            decision = "NO_TRADE"
        return {
            **best,
            "decision": decision,
            "signal_state": tracked.state if tracked else None,
            "signal_lifetime": tracked.lifetime() if tracked else None,
            "note": RNG_NOTE,
        }

    def rank(self, symbols: List[str], **kw) -> dict:
        rows = []
        for sym in symbols:
            sig = self.signal(sym, **kw)
            if sig.get("decision") == "NO_DATA":
                continue
            if "score" not in sig:
                rows.append({"symbol": sym, "decision": sig["decision"],
                             "reason": sig.get("reason"), "score": 0.0, "grade": "D"})
                continue
            rows.append({
                "symbol": sym,
                "contract": sig["contract"],
                "barrier": sig["barrier"],
                "name": sig["name"],
                "score": sig["score"],
                "grade": sig["grade"],
                "ev": sig["ev"],
                "edge": sig["edge"],
                "decision": sig["decision"],
                "signal_state": sig.get("signal_state"),
                "auto_executable": bool(sig.get("auto_executable")) and sig["decision"] == "EXECUTE",
            })
        rows.sort(key=lambda r: (-r["score"], r.get("name", "")))
        executable = [r for r in rows if r.get("auto_executable")]
        return {
            "rank": rows,
            "executable": executable,
            "decision": "NO_TRADE" if not executable else "EXECUTE",
            "note": "Only A+/A signals that survived the confirmation window may "
                    "execute. A 99/100 score with a failed hard gate is still NO TRADE. "
                    + RNG_NOTE,
        }

    # ---------------- §17/§18 risk profile + martingale ----------------
    def risk_profile(self) -> dict:
        cfg = self.config
        return {
            "max_stake_pct_of_balance": [cfg.min_stake_pct, cfg.max_stake_pct],
            "max_simultaneous_trades": cfg.max_simultaneous_trades,
            "max_exposure_per_symbol": cfg.max_exposure_per_symbol,
            "session_stop_pct": cfg.session_stop_pct,
            "daily_stop_pct": cfg.daily_stop_pct,
            "consecutive_loss_pause": cfg.consecutive_loss_pause,
            "severe_loss_stop_session": cfg.severe_loss_stop,
            "martingale": "OFF" if not cfg.martingale_enabled else "CAPPED_RESEARCH_ONLY",
            "note": "Starting safety parameters, not profitability claims (§17). "
                    "risk_guard owns enforcement; this engine only advises.",
        }

    @staticmethod
    def martingale_plan(
        base_stake: float,
        payout: float,
        max_level: int,
        bankroll: float,
        target_profit: float = 0.0,
        cumulative_loss: float = 0.0,
    ) -> dict:
        """§18: capped recovery math only. Unlimited martingale is prohibited."""
        if max_level < 1:
            return {"error": "max_level must be >= 1 — unlimited martingale is prohibited"}
        net = payout - 1.0
        if net <= 0:
            return {"error": "payout must exceed 1.0"}
        levels = []
        loss = cumulative_loss
        stake = max(base_stake, (loss + target_profit) / net)
        for lvl in range(1, max_level + 1):
            if stake > bankroll - loss:
                return {
                    "levels": levels,
                    "feasible": False,
                    "reason": f"level {lvl} stake ${stake:.2f} exceeds remaining bankroll "
                              f"${bankroll - loss:.2f} — stop, do not chase",
                }
            levels.append({
                "level": lvl,
                "stake": round(stake, 2),
                "cumulative_exposure": round(loss + stake, 2),
                "worst_case_loss": round(loss + stake, 2),
                "remaining_bankroll_after_loss": round(bankroll - loss - stake, 2),
                "net_profit_if_win": round(stake * net - loss, 2),
            })
            loss += stake
            stake = (loss + target_profit) / net
        return {
            "levels": levels,
            "feasible": True,
            "max_level": max_level,
            "worst_case_loss": round(loss, 2),
            "note": "Martingale resizes losses, it does not create edge. Capped plans "
                    "only, and never to hide a weak strategy (§18).",
        }

    # ---------------- §20/§21 post-mortem + win analysis ----------------
    @staticmethod
    def _journal_entries(user_id: str) -> List[dict]:
        # list_entries is newest-first; flip to chronological so drawdown,
        # streaks and rolling windows are computed over real time order.
        newest_first = [e for e in journal_engine.list_entries(limit=100000)
                        if e.get("user_id", "default") == user_id]
        return list(reversed(newest_first))

    @staticmethod
    def _family(entry: dict) -> str:
        return str(entry.get("contract", "?")).split()[0].upper()

    def _family_ev(self, entries: List[dict]) -> Dict[str, float]:
        fam: Dict[str, List[float]] = {}
        for e in entries:
            stake = float(e.get("stake", 0) or 0)
            if stake <= 0:
                continue
            fam.setdefault(self._family(e), []).append(float(e.get("pnl", 0) or 0) / stake)
        return {k: sum(v) / len(v) for k, v in fam.items() if v}

    def _classify_loss(self, e: dict, family_ev: Dict[str, float]) -> str:
        snap = e.get("analysis_snapshot") or {}
        if float(e.get("data_quality", 1.0) or 1.0) < 0.7:
            return "DATA_PROBLEM"
        if snap.get("edge_decay") or snap.get("signal_state") == "CANCELLED":
            return "SIGNAL_DECAY"
        if float(snap.get("latency_ms", 0) or 0) > self.config.max_latency_ms:
            return "EXECUTION_DELAY"
        if snap.get("payout_changed") or (
            snap.get("payout") and snap.get("expected_payout")
            and float(snap["payout"]) < 0.97 * float(snap["expected_payout"])
        ):
            return "PAYOUT_PROBLEM"
        if snap.get("regime_change"):
            return "REGIME_CHANGE"
        if float(e.get("evidence_score", 100.0) or 100.0) < 50.0:
            return "FALSE_SIGNAL"
        stake = float(e.get("stake", 0) or 0)
        balance = float(snap.get("balance", 0) or 0)
        if balance > 0 and stake > self.config.max_stake_pct * balance:
            return "RISK_MANAGEMENT"
        if snap.get("decision") == "WATCH" or snap.get("confirmed") is False:
            return "BAD_ENTRY"  # fired before the confirmation window closed
        if family_ev.get(self._family(e), 0.0) < -0.02:
            return "MODEL_ERROR"  # the family itself is structurally losing
        return "NORMAL_RANDOM_LOSS"  # expected variance on a +EV decision

    def postmortem(self, user_id: str = "default") -> dict:
        entries = self._journal_entries(user_id)
        losses = [e for e in entries if e.get("result") == "loss"]
        family_ev = self._family_ev(entries)
        classified = [{
            "id": e.get("id"),
            "market": e.get("market"),
            "contract": e.get("contract"),
            "digit": e.get("digit"),
            "stake": e.get("stake"),
            "pnl": e.get("pnl"),
            "loss_class": self._classify_loss(e, family_ev),
        } for e in losses]
        counts = {c: 0 for c in LOSS_CLASSES}
        for c in classified:
            counts[c["loss_class"]] += 1
        wins = [e for e in entries if e.get("result") == "win"]
        n = len(wins) + len(losses)
        # Variance or model failure? Wilson UB of the win rate vs breakeven.
        payouts = [float(e["pnl"]) / float(e["stake"]) + 1.0
                   for e in wins if float(e.get("stake", 0) or 0) > 0 and float(e.get("pnl", 0)) > 0]
        med_payout = sorted(payouts)[len(payouts) // 2] if payouts else None
        verdict = "INSUFFICIENT_DATA"
        if n >= 20 and med_payout:
            ub = wilson_upper_bound(len(wins), n)
            verdict = ("EXPECTED_VARIANCE" if ub >= 1.0 / med_payout
                       else "MODEL_FAILURE_EVIDENCE")
        return {
            "user_id": user_id,
            "losses": len(losses),
            "classified": classified[-100:],
            "class_counts": counts,
            "variance_verdict": verdict,
            "note": "One loss never rewrites the strategy. Model changes require "
                    "evidence against a frozen dataset (§20/§23).",
        }

    def win_analysis(self, user_id: str = "default") -> dict:
        entries = self._journal_entries(user_id)
        wins = [e for e in entries if e.get("result") == "win"]
        fam: Dict[str, dict] = {}
        for e in entries:
            if e.get("result") not in ("win", "loss"):
                continue
            f = fam.setdefault(self._family(e), {"wins": 0, "n": 0, "payouts": []})
            f["n"] += 1
            if e.get("result") == "win":
                f["wins"] += 1
                stake = float(e.get("stake", 0) or 0)
                pnl = float(e.get("pnl", 0) or 0)
                if stake > 0 and pnl > 0:
                    f["payouts"].append(pnl / stake + 1.0)
        families = {}
        for name, f in fam.items():
            med = sorted(f["payouts"])[len(f["payouts"]) // 2] if f["payouts"] else None
            be = 1.0 / med if med else None
            lb = wilson_lower_bound(f["wins"], f["n"]) if f["n"] else 0.0
            families[name] = {
                "trades": f["n"],
                "win_rate": round(f["wins"] / f["n"], 4) if f["n"] else None,
                "breakeven": round(be, 4) if be else None,
                "wilson_lower_bound": round(lb, 4),
                "verdict": ("SKILL_CONSISTENT" if be and lb > be
                            else "VARIANCE_NOT_PROVEN"),
            }
        recent = [{
            "id": e.get("id"),
            "market": e.get("market"),
            "contract": e.get("contract"),
            "digit": e.get("digit"),
            "pnl": e.get("pnl"),
            "features": sorted((e.get("analysis_snapshot") or {}).keys()),
            "robust_one_tick_earlier": (e.get("analysis_snapshot") or {}).get("confirmed"),
        } for e in wins[-50:]]
        return {
            "user_id": user_id,
            "wins": len(wins),
            "families": families,
            "recent_wins": recent,
            "note": "A win on a contract whose Wilson lower bound sits below breakeven "
                    "is luck, not skill — do not learn the wrong lesson (§21).",
        }

    # ---------------- §24/§25 scorecard + kill switches ----------------
    def scorecard(self, user_id: str = "default") -> dict:
        entries = [e for e in self._journal_entries(user_id)
                   if e.get("result") in ("win", "loss")]
        wins = [e for e in entries if e["result"] == "win"]
        losses = [e for e in entries if e["result"] == "loss"]
        n = len(entries)
        staked = sum(float(e.get("stake", 0) or 0) for e in entries)
        pnl = sum(float(e.get("pnl", 0) or 0) for e in entries)
        gross_win = sum(float(e.get("pnl", 0) or 0) for e in wins if float(e.get("pnl", 0) or 0) > 0)
        gross_loss = abs(sum(float(e.get("pnl", 0) or 0) for e in losses))

        cum, peak, max_dd = 0.0, 0.0, 0.0
        longest_streak, streak = 0, 0
        for e in entries:
            cum += float(e.get("pnl", 0) or 0)
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
            if e["result"] == "loss":
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 0

        payouts = [float(e["pnl"]) / float(e["stake"]) + 1.0
                   for e in wins if float(e.get("stake", 0) or 0) > 0 and float(e.get("pnl", 0)) > 0]
        realized_ev = (pnl / staked) if staked > 0 else 0.0
        expected_evs = [float((e.get("analysis_snapshot") or {}).get("ev"))
                        for e in entries
                        if (e.get("analysis_snapshot") or {}).get("ev") is not None]

        def _group(keyfn):
            groups: Dict[str, dict] = {}
            for e in entries:
                k = keyfn(e)
                g = groups.setdefault(k, {"trades": 0, "wins": 0, "pnl": 0.0, "staked": 0.0})
                g["trades"] += 1
                g["wins"] += 1 if e["result"] == "win" else 0
                g["pnl"] += float(e.get("pnl", 0) or 0)
                g["staked"] += float(e.get("stake", 0) or 0)
            for g in groups.values():
                g["win_rate"] = round(g["wins"] / g["trades"], 4) if g["trades"] else None
                g["pnl"] = round(g["pnl"], 2)
                g["staked"] = round(g["staked"], 2)
            return groups

        by_contract = _group(lambda e: self._family(e))
        by_market = _group(lambda e: str(e.get("market", "?")))
        def _barrier_key(e):
            d = e.get("digit")
            if d is None:
                parts = str(e.get("contract", "")).split()
                if len(parts) > 1 and parts[-1].lstrip("-").isdigit():
                    d = parts[-1]
            return f"{self._family(e)} {d}"

        by_barrier = _group(_barrier_key)

        # §25: kill switch per family — rolling-100 EV < 0 AND the Wilson
        # upper bound of the win rate cannot reach breakeven.
        kills = {}
        for family in {self._family(e) for e in entries}:
            fam_entries = [e for e in entries if self._family(e) == family][-100:]
            fam_wins = [e for e in fam_entries if e["result"] == "win"]
            fam_staked = sum(float(e.get("stake", 0) or 0) for e in fam_entries)
            fam_pnl = sum(float(e.get("pnl", 0) or 0) for e in fam_entries)
            rolling_ev = fam_pnl / fam_staked if fam_staked > 0 else 0.0
            fam_payouts = [float(e["pnl"]) / float(e["stake"]) + 1.0
                           for e in fam_wins
                           if float(e.get("stake", 0) or 0) > 0 and float(e.get("pnl", 0)) > 0]
            med = sorted(fam_payouts)[len(fam_payouts) // 2] if fam_payouts else None
            ub = wilson_upper_bound(len(fam_wins), len(fam_entries)) if fam_entries else 1.0
            if med is None:
                # No wins at all in 20+ trades is self-evidently below breakeven.
                deteriorating = len(fam_entries) >= 20 and len(fam_wins) == 0
            else:
                deteriorating = ub < 1.0 / med
            kills[family] = {
                "rolling_trades": len(fam_entries),
                "rolling_ev": round(rolling_ev, 4),
                "wilson_upper_wr": round(ub, 4),
                "breakeven_wr": round(1.0 / med, 4) if med else None,
                "kill": bool(len(fam_entries) >= 20 and rolling_ev < 0 and deteriorating),
            }

        graded = {"A+": {"n": 0, "wins": 0}, "A": {"n": 0, "wins": 0}}
        for e in entries:
            grade = (e.get("analysis_snapshot") or {}).get("grade")
            if grade in graded:
                graded[grade]["n"] += 1
                graded[grade]["wins"] += 1 if e["result"] == "win" else 0
        grade_wr = {g: (round(v["wins"] / v["n"], 4) if v["n"] else None)
                    for g, v in graded.items()}

        return {
            "user_id": user_id,
            "trades": n,
            "win_rate": round(len(wins) / n, 4) if n else None,
            "loss_rate": round(len(losses) / n, 4) if n else None,
            "pnl": round(pnl, 2),
            "staked": round(staked, 2),
            "roi": round(pnl / staked, 4) if staked > 0 else None,
            "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
            "avg_payout": round(sorted(payouts)[len(payouts) // 2], 3) if payouts else None,
            "expected_ev": round(sum(expected_evs) / len(expected_evs), 4) if expected_evs else None,
            "realized_ev": round(realized_ev, 4),
            "max_drawdown": round(max_dd, 2),
            "longest_losing_streak": longest_streak,
            "signals": dict(self._stats),
            "grade_win_rates": grade_wr,
            "by_contract": by_contract,
            "by_market": by_market,
            "by_barrier": by_barrier,
            "kill_switches": kills,
            "note": "§24/§25. A family on kill=True has demonstrably degraded — "
                    "the system must stop using it until out-of-sample evidence "
                    "says otherwise.",
        }

    # ---------------- §7/§23 threshold validation ----------------
    def validate_thresholds(self, user_id: str = "default",
                            grid: Tuple[float, ...] = (0.0, 0.01, 0.02, 0.03, 0.05, 0.08)) -> dict:
        entries = [e for e in self._journal_entries(user_id)
                   if e.get("result") in ("win", "loss")]
        rows = []
        for t in grid:
            sel = [e for e in entries
                   if float((e.get("analysis_snapshot") or {}).get("edge", 1.0) or 0.0) >= t]
            wins = sum(1 for e in sel if e["result"] == "win")
            staked = sum(float(e.get("stake", 0) or 0) for e in sel)
            pnl = sum(float(e.get("pnl", 0) or 0) for e in sel)
            rows.append({
                "min_edge": t,
                "trades": len(sel),
                "win_rate": round(wins / len(sel), 4) if sel else None,
                "roi": round(pnl / staked, 4) if staked > 0 else None,
                "wilson_wr_lb": round(wilson_lower_bound(wins, len(sel)), 4) if sel else None,
            })
        return {
            "user_id": user_id,
            "grid": rows,
            "note": "In-sample only. §23: no threshold earns deployment without "
                    "out-of-sample, walk-forward, Monte Carlo and a random-baseline "
                    "comparison. A +900% backtest cell is curve fitting, not a strategy.",
        }


bottom_up_engine = BottomUpEngine()
