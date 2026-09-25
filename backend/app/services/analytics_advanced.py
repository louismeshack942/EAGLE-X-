"""Advanced analytics — digit frequency, psychology, contracts, gaps, predictor."""
import math
import os
from collections import Counter
from typing import Dict, List, Optional

from app.core.queue import tick_queue

WINDOWS = {"1m": 600, "5m": 3000, "15m": 9000, "1h": 36000}
EXPECTED_DIGIT_FREQ = 10.0  # a fair random digit appears 10% of the time

# Upper bound on how deep _window_digits will scan the queue while hunting for
# live ticks. A queue is bounded in memory, so this only guards against an
# unbounded loop if a caller asks for a window larger than the queue holds.
_QUEUE_SCAN_CEILING = 1_000_000


def _live_only() -> bool:
    """Whether analytics must ignore synthetic ticks.

    Default ON: a synthetic tape is what manufactured the phantom edges, and
    this module feeds Market Master AND the Truth Engine, so it is the single
    most dangerous place to let a generated digit into a verdict. Set
    EAGLEX_ALLOW_SYNTHETIC=1 to let synthetic ticks back in — the analytics
    test-suite relies on that, because bare `Tick`s default to provider
    "demo".
    """
    return os.environ.get("EAGLEX_ALLOW_SYNTHETIC", "0") != "1"


def _digits(ticks) -> List[int]:
    if _live_only():
        ticks = [t for t in ticks if getattr(t, "provider", "demo") == "deriv_live"]
    return [t.digit for t in ticks]


def _window_digits(queue, symbol: str, window: int) -> List[int]:
    """The last `window` usable digits for `symbol`.

    Filtering after slicing would silently shrink the sample: ask for 100
    ticks on a tape that is half synthetic and the caller gets 50 digits while
    believing it asked for 100. A single fixed over-fetch is not enough either
    — if the recent window happens to be dominated by synthetic ticks, 4x
    still comes back short. So we deepen the fetch until the window is
    satisfied or the queue is exhausted, and the caller always learns the true
    sample size from the returned length.
    """
    if not _live_only():
        return _digits(queue.recent(symbol, limit=window))

    limit = max(window * 4, window)
    digits = _digits(queue.recent(symbol, limit=limit))
    while len(digits) < window and limit < _QUEUE_SCAN_CEILING:
        limit *= 4
        digits = _digits(queue.recent(symbol, limit=limit))
    return digits[-window:]


class AdvancedAnalytics:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def get_digit_analysis(self, symbol: str, window: int = 100) -> dict:
        digits = _window_digits(self.queue, symbol, window)
        if not digits:
            return {"symbol": symbol, "window": window, "frequency": {}, "most_frequent": None, "least_frequent": None}

        n = len(digits)
        counts = Counter(digits)
        # Bayesian shrinkage: pull each digit's observed share toward the fair
        # 10% prior with a strength proportional to how little data we have.
        # A "100%" on 20 ticks collapses toward fair; on 2000 ticks it barely moves.
        PRIOR = 0.10
        PRIOR_STRENGTH = 25.0  # pseudo-count
        # Binomial z-score denominator for the fair 10% hypothesis.
        z_den = math.sqrt(n * PRIOR * (1 - PRIOR)) if n else 1.0
        frequency = {}
        for d in range(10):
            c = counts.get(d, 0)
            raw = c / n
            shrunk = (c + PRIOR_STRENGTH * PRIOR) / (n + PRIOR_STRENGTH)
            z = (c - n * PRIOR) / z_den if z_den else 0.0
            frequency[str(d)] = {
                "count": c,
                "percent": round(raw * 100, 1),
                "estimate": round(shrunk * 100, 2),  # trustworthy estimate
                "z": round(z, 2),                     # statistical significance
                "significant": abs(z) >= 1.96,        # 95% confidence level
            }
        sorted_counts = sorted(((counts.get(d, 0), d) for d in range(10)), reverse=True)
        most_freq_d = sorted_counts[0][1]
        least_freq_d = sorted_counts[-1][1]
        # Shannon entropy of the distribution
        entropy = -sum(
            (counts.get(d, 0) / n) * math.log2(counts.get(d, 0) / n)
            for d in range(10) if counts.get(d, 0) > 0
        )
        max_entropy = math.log2(10)
        balance = round(entropy / max_entropy * 100, 2)
        # runs: consecutive repeats vs alternations
        repeats = sum(1 for a, b in zip(digits, digits[1:]) if a == b)
        repetition = round(repeats / (n - 1) * 100, 1) if n > 1 else 0.0
        return {
            "symbol": symbol,
            "window": window,
            "n": n,
            "frequency": frequency,
            "most_frequent": most_freq_d,
            "least_frequent": least_freq_d,
            "entropy": round(entropy, 4),
            "balance": balance,
            "repetition": repetition,
            "alternation": round(100 - repetition, 1),
        }

    def get_psychology(self, symbol: str, window: int = 100) -> dict:
        analysis = self.get_digit_analysis(symbol, window)
        freq = analysis["frequency"]
        if not freq:
            return {"symbol": symbol, "overfed": None, "confirmation": None, "starving": None}
        devs = [
            (freq[str(d)]["percent"] - EXPECTED_DIGIT_FREQ, d, freq[str(d)]["percent"])
            for d in range(10)
        ]
        devs.sort(key=lambda x: x[0], reverse=True)
        return {
            "symbol": symbol,
            "overfed": {"digit": devs[0][1], "percent": devs[0][2], "deviation": round(devs[0][0], 1)},
            "confirmation": {"digit": devs[1][1], "percent": devs[1][2], "deviation": round(devs[1][0], 1)} if len(devs) > 1 else None,
            "starving": {"digit": devs[-1][1], "percent": devs[-1][2], "deviation": round(devs[-1][0], 1)},
        }

    def _evidence_class(self, deviation: float, supportive: bool) -> str:
        if abs(deviation) < 1.5:
            return "NEUTRAL"
        if deviation >= 4 and supportive:
            return "STRONG_DATA_SUPPORT"
        if deviation >= 1.5 and supportive:
            return "WEAK_DATA_SUPPORT"
        if deviation <= -4 or not supportive:
            return "WEAK_DATA_CONTRARY"
        return "NEUTRAL"

    def get_contract_analysis(self, symbol: str, mode: Optional[str] = None, window: int = 100) -> dict:
        analysis = self.get_digit_analysis(symbol, window)
        freq = analysis.get("frequency", {})
        if not freq:
            return {"symbol": symbol, "candidate": None, "modes": {}}
        psycho = self.get_psychology(symbol, window)
        candidate = psycho["overfed"]["digit"] if psycho["overfed"] else None

        modes: dict = {}
        # digit-based modes measured for the candidate digit
        if candidate is not None:
            obs = freq[str(candidate)]["percent"]
            modes["MATCHES"] = {
                "evidence": self._evidence_class(obs - EXPECTED_DIGIT_FREQ, True),
                "observed": obs,
                "expected": EXPECTED_DIGIT_FREQ,
                "deviation": round(obs - EXPECTED_DIGIT_FREQ, 1),
            }
            modes["DIFFERS"] = {
                "evidence": self._evidence_class(EXPECTED_DIGIT_FREQ - obs, True),
                "observed": round(100 - obs, 1),
                "expected": 90.0,
                "deviation": round(EXPECTED_DIGIT_FREQ - obs, 1),
            }
        odd_count = sum(freq[str(d)]["count"] for d in range(1, 10, 2))
        even_count = sum(freq[str(d)]["count"] for d in range(0, 10, 2))
        total = odd_count + even_count
        odd_pct = round(odd_count / total * 100, 1) if total else 50.0
        even_pct = round(even_count / total * 100, 1) if total else 50.0
        modes["ODD"] = {
            "evidence": self._evidence_class(odd_pct - 50, True),
            "observed": odd_pct,
            "expected": 50.0,
            "deviation": round(odd_pct - 50, 1),
        }
        modes["EVEN"] = {
            "evidence": self._evidence_class(even_pct - 50, True),
            "observed": even_pct,
            "expected": 50.0,
            "deviation": round(even_pct - 50, 1),
        }
        return {"symbol": symbol, "candidate": candidate, "modes": modes}

    def get_gap_analysis(self, symbol: str, window: int = 100) -> dict:
        digits = _window_digits(self.queue, symbol, window)

        result: dict = {"symbol": symbol, "gaps": {}}
        if not digits:
            return result
        for d in range(10):
            positions = [i for i, v in enumerate(digits) if v == d]
            if not positions:
                max_gap = len(digits)
                cur_gap = len(digits)
            else:
                interior = [b - a - 1 for a, b in zip(positions[:-1], positions[1:])]
                cur_gap = len(digits) - positions[-1] - 1
                max_gap = max([cur_gap, positions[0]] + interior)
            result["gaps"][str(d)] = {"current": cur_gap, "max": max_gap}
        return result

    def get_predictor(self, symbol: str, window: int = 100) -> dict:
        psycho = self.get_psychology(symbol, window)
        overfed = psycho["overfed"]
        if not overfed:
            return {"symbol": symbol, "candidate": None, "confidence": 0.0}
        conf = min(100.0, 50 + overfed["deviation"] * 5)
        return {
            "symbol": symbol,
            "candidate": overfed["digit"],
            "confidence": round(conf, 1),
            "observed_percent": overfed["percent"],
            "evidence": f"Digit {overfed['digit']} appears {overfed['percent']}% vs expected 10.0%",
        }

    def get_multi_window(self, symbol: str) -> dict:
        windows = {"1m": 25, "5m": 100, "15m": 250, "1h": 500}
        out = {"symbol": symbol, "windows": {}}
        for label, w in windows.items():
            pred = self.get_predictor(symbol, window=w)
            out["windows"][label] = pred
        return out

    def get_ldp_patterns(self, symbol: str, pattern_len: int = 2, window: int = 100) -> dict:
        """Last-digit-pattern analysis: frequency of digit tuples."""
        digits = _window_digits(self.queue, symbol, window)

        counts: Counter = Counter()
        for i in range(len(digits) - pattern_len + 1):
            counts[tuple(digits[i:i + pattern_len])] += 1
        top = [
            {"pattern": "".join(map(str, pat)), "count": c}
            for pat, c in counts.most_common(10)
        ]
        return {"symbol": symbol, "pattern_len": pattern_len, "top_patterns": top}


digit_engine = AdvancedAnalytics()
