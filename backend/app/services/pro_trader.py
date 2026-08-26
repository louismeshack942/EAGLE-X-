"""Pro Trader layer — a statistical decision engine, not a certainty engine.

Implements the Pro Trader spec on top of the existing squad analytics:
multi-window digit frequencies, z-scores, chi-square uniformity tests with
Benjamini-Hochberg FDR correction, gap/streak/entropy/transition/autocorrelation
features, Dirichlet-smoothed contract probabilities, Wilson confidence bounds,
payout-driven EV with hard gates, and a composite signal score.

The engine measures whether a recent digit sequence looks unusual. On an
RNG-driven synthetic index that is descriptive, not predictive — so every
signal carries hard gates and the honest default decision is REJECT.
"""
import math
import time
from collections import Counter
from typing import Dict, List, Optional

from app.core.queue import tick_queue

# Spec §1: analytical windows over the rolling buffer.
WINDOWS = (25, 50, 100, 250, 500, 1000)
WINDOW_WEIGHTS = {25: 0.30, 50: 0.25, 100: 0.20, 250: 0.10, 500: 0.10, 1000: 0.05}

# Spec §14/§20: hard-gate thresholds.
MIN_SAMPLE = 100
MIN_EV = 0.03
FDR_Q = 0.05
WILSON_Z = 1.96
MAX_LATENCY_MS = 500  # spec §29: abort signals staler than this

# Payout multiples (total payout / stake) used only when no live quote is
# supplied. Spec §15: never trust these — the execution path must re-price
# via the Deriv proposal endpoint and re-run the gates with the real payout.
DEFAULT_PAYOUTS = {
    "DIFFERS": 1.10,
    "MATCHES": 9.0,
    "OVER": 1.95,
    "UNDER": 1.95,
    "ODD": 1.95,
    "EVEN": 1.95,
}

RNG_NOTE = (
    "Digit statistics are descriptive, not predictive: Deriv synthetic indices "
    "are RNG-driven, so an unusual recent sequence does not imply a persistent edge."
)


# ---------------- pure-math primitives ----------------
def _gammaincc(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) — pure-python (no scipy)."""
    if x <= 0:
        return 1.0
    if x < a + 1.0:
        ap, total, delta = a, 1.0 / a, 1.0 / a
        for _ in range(300):
            ap += 1.0
            delta *= x / ap
            total += delta
            if abs(delta) < abs(total) * 1e-14:
                break
        p = total * math.exp(-x + a * math.log(x) - math.lgamma(a))
        return min(1.0, max(0.0, 1.0 - p))
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / max(x + 1.0 - a, tiny)
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-14:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return min(1.0, max(0.0, q))


def chi2_sf(x: float, df: int = 9) -> float:
    """Survival function P(X >= x) for chi-square — spec §5."""
    return _gammaincc(df / 2.0, x / 2.0)


def normal_two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def benjamini_hochberg(pvals: List[float], q: float = FDR_Q) -> List[float]:
    """BH-adjusted p-values — spec §6 (multiple-testing correction)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [1.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        running = min(running, pvals[idx] * m / rank)
        adjusted[idx] = min(1.0, running)
    return adjusted


def wilson_lower_bound(wins: int, n: int, z: float = WILSON_Z) -> float:
    """Wilson score lower bound — spec §19."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (center - margin) / denom)


def _lin_slope(ys: List[float]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


# ---------------- the engine ----------------
class ProTrader:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    # ---- spec §1-§11: per-symbol feature board ----
    def board(self, symbol: str) -> dict:
        ticks = self.queue.recent(symbol, limit=max(WINDOWS))
        digits = [t.digit for t in ticks]
        n = len(digits)
        if n == 0:
            return {"symbol": symbol, "ticks": 0, "windows": {}, "decision": "NO_DATA"}

        window_stats: Dict[str, dict] = {}
        digit_z: Dict[int, Dict[int, float]] = {d: {} for d in range(10)}
        pval_keys: List[tuple] = []
        pvals: List[float] = []
        for w in WINDOWS:
            if n < w:
                continue
            seg = digits[-w:]
            counts = Counter(seg)
            chi2 = sum((counts.get(d, 0) - w / 10.0) ** 2 / (w / 10.0) for d in range(10))
            p_chi = chi2_sf(chi2, 9)
            z_den = math.sqrt(w * 0.09)
            freq = {}
            for d in range(10):
                c = counts.get(d, 0)
                z = (c - 0.10 * w) / z_den
                digit_z[d][w] = z
                pval_keys.append((d, w))
                pvals.append(normal_two_sided_p(z))
                freq[str(d)] = {
                    "count": c,
                    "pct": round(c / w * 100, 1),
                    "posterior": round((c + 1) / (w + 10), 4),  # spec §13 Dirichlet
                    "z": round(z, 2),
                }
            window_stats[str(w)] = {"counts": freq, "chi2": round(chi2, 2), "chi2_p": round(p_chi, 4)}

        # Spec §6: one BH correction across every digit x window test.
        adjusted = benjamini_hochberg(pvals) if pvals else []
        digit_adj: Dict[int, float] = {d: 1.0 for d in range(10)}
        table_tests = [(int(w), s["chi2_p"]) for w, s in window_stats.items()]
        for (d, w), adj in zip(pval_keys, adjusted):
            digit_adj[d] = min(digit_adj[d], adj)
        table_adj = benjamini_hochberg([p for _, p in table_tests]) if table_tests else []
        table_fdr_significant = any(a < FDR_Q for a in table_adj)

        # Spec §3: weighted probability across windows (signal score only).
        weighted: Dict[int, float] = {}
        for d in range(10):
            num = sum(
                WINDOW_WEIGHTS[w] * window_stats[str(w)]["counts"][str(d)]["posterior"]
                for w in WINDOWS if str(w) in window_stats
            )
            den = sum(WINDOW_WEIGHTS[w] for w in WINDOWS if str(w) in window_stats)
            weighted[d] = num / den if den else 0.10

        # Spec §7/§8: gaps and streaks.
        gaps: Dict[int, int] = {}
        for d in range(10):
            gap = 0
            for x in reversed(digits):
                if x == d:
                    break
                gap += 1
            else:
                gap = n
            gaps[d] = gap
        max_streak: Dict[int, int] = {d: 0 for d in range(10)}
        cur_d, cur_len = digits[0], 1
        for x in digits[1:]:
            if x == cur_d:
                cur_len += 1
            else:
                max_streak[cur_d] = max(max_streak[cur_d], cur_len)
                cur_d, cur_len = x, 1
        max_streak[cur_d] = max(max_streak[cur_d], cur_len)

        # Spec §22: entropy regime. Thin tapes (< smallest window) fall back
        # to the whole buffer instead of crashing on an empty window set.
        avail_ws = [w for w in WINDOWS if str(w) in window_stats]
        big_w = max(avail_ws) if avail_ws else min(n, min(WINDOWS))
        counts_big = Counter(digits[-big_w:])
        entropy = -sum(
            (c / big_w) * math.log2(c / big_w) for c in counts_big.values() if c
        )
        h_norm = entropy / math.log2(10)
        regime = (
            "NEAR_UNIFORM" if h_norm >= 0.98
            else "MILD_CONCENTRATION" if h_norm >= 0.95
            else "DIGIT_CONCENTRATION"
        )

        # Spec §10: lag-k autocorrelation per digit → dependency score.
        max_rho, serial_dependence = 0.0, False
        rho_bound = WILSON_Z / math.sqrt(n)
        for d in range(10):
            xs = [1.0 if x == d else 0.0 for x in digits]
            mu = sum(xs) / n
            var = sum((x - mu) ** 2 for x in xs)
            if var == 0:
                continue
            for k in range(1, 11):
                rho = sum((xs[t] - mu) * (xs[t - k] - mu) for t in range(k, n)) / var
                if abs(rho) > abs(max_rho):
                    max_rho = rho
                if abs(rho) > rho_bound:
                    serial_dependence = True
        dependency_score = min(100.0, abs(max_rho) / 0.15 * 100) if serial_dependence else 0.0

        # Spec §11: transition matrix, only where count(i) >= 100.
        trans_counts: Dict[int, Counter] = {d: Counter() for d in range(10)}
        for a, b in zip(digits, digits[1:]):
            trans_counts[a][b] += 1
        transitions = {}
        for i in range(10):
            row_total = sum(trans_counts[i].values())
            if row_total >= 100:
                transitions[str(i)] = {
                    str(j): round(trans_counts[i][j] / row_total, 4) for j in range(10)
                }

        # Spec §23: composite anomaly score (context, never a buy signal).
        worst_gap_prob = min(0.9 ** g for g in gaps.values())
        worst_streak_prob = min(10 ** (1 - s) for s in max_streak.values())
        min_chi_p = min((s["chi2_p"] for s in window_stats.values()), default=1.0)
        max_abs_z = max(
            (abs(z) for dz in digit_z.values() for z in dz.values()), default=0.0
        )
        anomaly_score = round(min(100.0,
            30 * min(1.0, -math.log10(max(min_chi_p, 1e-12)) / 4)
            + 25 * min(1.0, max_abs_z / 4.0)
            + 20 * min(1.0, -math.log10(max(worst_gap_prob, 1e-12)) / 4)
            + 15 * min(1.0, -math.log10(max(worst_streak_prob, 1e-12)) / 4)
            + 10 * (1.0 - h_norm) / 0.05
        ), 1)

        # Spec §9: digit state classification.
        states = {}
        for d in range(10):
            p = weighted[d]
            state = (
                "HOT" if p > 0.115 else "COLD" if p < 0.085 else "NEUTRAL"
            )
            if digit_adj[d] < FDR_Q:
                state = "ANOMALOUS" if state == "NEUTRAL" else state
            states[str(d)] = {
                "state": state,
                "weighted_pct": round(p * 100, 2),
                "adj_p": round(digit_adj[d], 4),
                "gap": gaps[d],
                "max_streak": max_streak[d],
            }

        # Data-quality gate (spec §20-G): duplicate epochs / impossible digits.
        epochs = [t.timestamp for t in ticks]
        data_ok = len(set(epochs)) == len(epochs) and all(0 <= x <= 9 for x in digits)

        return {
            "symbol": symbol,
            "ticks": n,
            "last_digit": digits[-1],
            "windows": window_stats,
            "digit_states": states,
            "digit_adj_p": {str(d): round(v, 4) for d, v in digit_adj.items()},
            "table_fdr_significant": table_fdr_significant,
            "regime": regime,
            "entropy_norm": round(h_norm, 4),
            "dependency_score": round(dependency_score, 1),
            "serial_dependence": serial_dependence,
            "transitions": transitions,
            "anomaly_score": anomaly_score,
            "data_ok": data_ok,
            "note": RNG_NOTE,
        }

    # ---- spec §12-§21, §49-§50: contract opportunities with hard gates ----
    def opportunities(
        self,
        symbol: str,
        payouts: Optional[Dict[str, float]] = None,
        latency_ms: Optional[float] = None,
    ) -> List[dict]:
        started = time.monotonic()
        b = self.board(symbol)
        if b.get("decision") == "NO_DATA" or b["ticks"] < min(WINDOWS):
            return []
        ticks = self.queue.recent(symbol, limit=max(WINDOWS))
        digits = [t.digit for t in ticks]
        n = len(digits)
        payout_map = dict(DEFAULT_PAYOUTS)
        payout_source = "assumed_default"
        if payouts:
            payout_map.update({k: float(v) for k, v in payouts.items()})
            payout_source = "quoted"

        # Posterior probabilities per window (spec §13) and binary win counts
        # per contract (for the Wilson bound, spec §19).
        def window_posterior(w: int) -> List[float]:
            c = Counter(digits[-w:])
            return [(c.get(d, 0) + 1) / (w + 10) for d in range(10)]

        posts = {w: window_posterior(w) for w in WINDOWS if n >= w}
        eval_w = max((w for w in posts if w >= MIN_SAMPLE), default=max(posts))
        post = posts[eval_w]
        counts_eval = Counter(digits[-eval_w:])

        def contract_prob(kind: str, digit: Optional[int], probs=post) -> float:
            if kind == "DIFFERS":
                return 1.0 - probs[digit]
            if kind == "MATCHES":
                return probs[digit]
            if kind == "OVER":
                return sum(probs[k] for k in range(digit + 1, 10))
            if kind == "UNDER":
                return sum(probs[k] for k in range(0, digit))
            if kind == "ODD":
                return sum(probs[k] for k in (1, 3, 5, 7, 9))
            return sum(probs[k] for k in (0, 2, 4, 6, 8))

        def win_count(kind: str, digit: Optional[int]) -> int:
            if kind == "DIFFERS":
                return eval_w - counts_eval.get(digit, 0)
            if kind == "MATCHES":
                return counts_eval.get(digit, 0)
            if kind == "OVER":
                return sum(counts_eval.get(k, 0) for k in range(digit + 1, 10))
            if kind == "UNDER":
                return sum(counts_eval.get(k, 0) for k in range(0, digit))
            if kind == "ODD":
                return sum(counts_eval.get(k, 0) for k in (1, 3, 5, 7, 9))
            return sum(counts_eval.get(k, 0) for k in (0, 2, 4, 6, 8))

        candidates: List[tuple] = [(k, d) for k in ("DIFFERS", "MATCHES") for d in range(10)]
        candidates += [("OVER", d) for d in range(0, 9)]
        candidates += [("UNDER", d) for d in range(1, 10)]
        candidates += [("ODD", None), ("EVEN", None)]

        out: List[dict] = []
        for kind, d in candidates:
            name = f"{kind} on {d}" if d is not None else kind
            p_est = contract_prob(kind, d)
            payout = payout_map[kind]
            breakeven = 1.0 / payout  # spec §16
            ev = p_est * payout - 1.0  # spec §15
            edge = p_est - breakeven
            lb = wilson_lower_bound(win_count(kind, d), eval_w)

            # Gate B: FDR evidence on the involved digit(s), or the table test
            # for parity contracts.
            if d is not None:
                involved = [d] if kind in ("DIFFERS", "MATCHES") else [
                    k for k in range(10)
                    if (k > d if kind == "OVER" else k < d)
                ]
                adj_p = min(float(b["digit_adj_p"][str(k)]) for k in involved)
            else:
                adj_p = min(float(b["digit_adj_p"][str(k)]) for k in (
                    (1, 3, 5, 7, 9) if kind == "ODD" else (0, 2, 4, 6, 8)
                ))
            stats_ok = adj_p < FDR_Q or b["table_fdr_significant"]

            # Gate E: edge must survive across windows, not one window only.
            avail = [w for w in (25, 50, 100, 250) if n >= w]
            hits = sum(
                1 for w in avail if contract_prob(kind, d, posts[w]) > breakeven
            )
            stability_hits = hits
            stable = len(avail) >= 2 and hits >= min(3, len(avail))

            # Spec §21: edge decay — the rolling estimate P_t over TIME, not
            # across window sizes. Split the eval window into chronological
            # chunks and take the slope of the per-chunk edge.
            chunk_count = 5
            seg = digits[-eval_w:]
            chunk_len = max(1, len(seg) // chunk_count)
            chunk_edges = []
            for ci in range(chunk_count):
                chunk = seg[ci * chunk_len:(ci + 1) * chunk_len]
                if not chunk:
                    continue
                cc = Counter(chunk)
                cw = len(chunk)
                cpost = [(cc.get(x, 0) + 1) / (cw + 10) for x in range(10)]
                chunk_edges.append(contract_prob(kind, d, cpost) - breakeven)
            edge_decaying = _lin_slope(chunk_edges) < -0.01

            z_key = abs(b["windows"][str(eval_w)]["counts"][str(d if d is not None else 0)]["z"]) \
                if str(eval_w) in b["windows"] else 0.0

            gates = {
                "sample": n >= MIN_SAMPLE,
                "stats_fdr": stats_ok,
                "confidence": lb > breakeven,
                "ev": ev >= MIN_EV,
                "stability": stable,
                "fresh_data": bool(b["data_ok"]),
                "latency": latency_ms is None or latency_ms <= MAX_LATENCY_MS,
                "edge_not_decaying": not edge_decaying,
            }
            decision = "ACCEPT" if all(gates.values()) else "REJECT"
            reasons = [k for k, v in gates.items() if not v]

            # Spec §50: composite signal score (never overrides the gates).
            gap = b["digit_states"][str(d)]["gap"] if d is not None else 0
            streak = b["digit_states"][str(d)]["max_streak"] if d is not None else 0
            gap_score = 100 if gap >= 40 else 75 if gap >= 30 else 50 if gap >= 20 else 0
            streak_score = {0: 0, 1: 0, 2: 25, 3: 50, 4: 75}.get(streak, 100)
            freq_score = min(100.0, abs(p_est - breakeven) * 1000)
            score = round(
                0.25 * freq_score
                + 0.15 * min(100.0, z_key / 2.58 * 100)
                + 0.10 * gap_score
                + 0.10 * streak_score
                + 0.15 * (stability_hits / len(avail) * 100 if avail else 0)
                + 0.10 * b["dependency_score"]
                + 0.10 * min(100.0, max(ev, 0.0) / 0.10 * 100)
                + 0.05 * 100.0,
                1,
            )

            out.append({
                "symbol": symbol,
                "contract": kind,
                "barrier": d,
                "name": name,
                "estimated_probability": round(p_est, 4),
                "payout": payout,
                "payout_source": payout_source,
                "breakeven_probability": round(breakeven, 4),
                "raw_edge": round(edge, 4),
                "ev": round(ev, 4),
                "sample_size": eval_w,
                "adjusted_p": round(adj_p, 4),
                "wilson_lower_bound": round(lb, 4),
                "z_score": round(z_key, 2),
                "gap": gap,
                "entropy": b["entropy_norm"],
                "regime": b["regime"],
                "anomaly_score": b["anomaly_score"],
                "edge_decay": edge_decaying,
                "signal_score": score,
                "gates": gates,
                "decision": decision,
                "decision_reasons": reasons,
            })

        out.sort(key=lambda o: (o["decision"] == "ACCEPT", o["ev"]), reverse=True)
        _ = started  # latency bookkeeping lives in the execution layer
        return out

    # ---- spec §49: the Pro Signal object ----
    def signal(self, symbol: str, **kw) -> dict:
        opps = self.opportunities(symbol, **kw)
        b = self.board(symbol)
        if not opps:
            return {"symbol": symbol, "decision": "NO_DATA", "note": RNG_NOTE}
        best = opps[0]
        return {
            **best,
            "board": {
                "ticks": b["ticks"],
                "last_digit": b["last_digit"],
                "regime": b["regime"],
                "anomaly_score": b["anomaly_score"],
                "serial_dependence": b["serial_dependence"],
                "table_fdr_significant": b["table_fdr_significant"],
            },
            "accepted": [o["name"] for o in opps if o["decision"] == "ACCEPT"],
            "note": RNG_NOTE,
        }

    # ---- spec §26/§27/§51: multi-market scan ----
    def scan(self, symbols: List[str], **kw) -> dict:
        markets = []
        for sym in symbols:
            sig = self.signal(sym, **kw)
            if sig.get("decision") == "NO_DATA":
                continue
            b = self.board(sym)
            w100 = b["windows"].get("100", {})
            markets.append({
                "symbol": sym,
                "last_digit": b["last_digit"],
                "frequency_100": {
                    d: c["pct"] for d, c in w100.get("counts", {}).items()
                },
                "chi2": w100.get("chi2"),
                "chi2_p": w100.get("chi2_p"),
                "regime": b["regime"],
                "anomaly_score": b["anomaly_score"],
                "best_contract": sig["name"],
                "estimated_probability": sig["estimated_probability"],
                "breakeven_probability": sig["breakeven_probability"],
                "ev": sig["ev"],
                "decision": sig["decision"],
                "decision_reasons": sig["decision_reasons"],
                "signal_score": sig["signal_score"],
            })
        markets.sort(key=lambda m: (m["decision"] == "ACCEPT", m["ev"]), reverse=True)
        return {
            "markets": markets,
            "accepted": [m["symbol"] + ":" + m["best_contract"]
                         for m in markets if m["decision"] == "ACCEPT"],
            "note": RNG_NOTE,
        }


pro_trader = ProTrader()
