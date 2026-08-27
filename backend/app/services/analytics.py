"""Statistical digit analysis — deterministic, transparent, pure-python.

All formulas are documented inline. No scipy dependency. These are descriptive
statistics over observed ticks; they are NEVER presented as a guaranteed prediction.

Uniform digit baseline under the null hypothesis: p0 = 0.1 per digit.
""" 

from __future__ import annotations

import math
import statistics
from typing import Sequence

UNIFORM_P = 0.1
DIGITS = list(range(10))
ODD_DIGITS = {1, 3, 5, 7, 9}
EVEN_DIGITS = {0, 2, 4, 6, 8}

Z_SIGMA_THRESHOLD = 3.0  # |z| >= 3 => 'significant deviation' (descriptive, ~3-sigma)
CHI2_MIN_SAMPLE = MIN_SAMPLE = 40  # min sample for chi-square uniformity test


# ---------------------------------------------------------------- digit frequency
def count_digits(digits: Sequence[int]) -> list[int]:
    """Return counts per digit 0..9."""
    counts = [0] * 10
    for d in digits:
        if 0 <= d <= 9:
            counts[int(d)] += 1
    return counts


def digit_frequency(digits: Sequence[int]) -> dict:
    """For every digit: count, percentage, rank, expected, deviation (pp)."""
    n = len(digits)
    counts = count_digits(digits)
    expected = n / 10.0 if n else 0.0
    ranked = sorted(range(10), key=lambda d: (-counts[d], d))
    ranks = {d: i + 1 for i, d in enumerate(ranked)}
    return {
        "n": n,
        "counts": counts,
        "percentages": [round(c / n * 100, 2) if n else 0.0 for c in counts],
        "rank": ranks,
        "expected_per_digit": round(expected, 3),
        "expected_percent": 10.0,
        "deviation_pp": [
            round((c - expected) / n * 100.0, 2) if n else 0.0 for c in counts
        ],
        "most_frequent": ranked[0] if n else -1,
        "least_frequent": ranked[-1] if n else -1,
        "most_frequent_count": counts[ranked[0]] if n else 0,
        "least_frequent_count": counts[ranked[-1]] if n else 0,
        "uniform_p": UNIFORM_P,
    }


# ----------------------------------------------------------------------- z-score
def z_scores(digits: Sequence[int]) -> list[float]:
    """One-sample z-score per digit against the uniform baseline.

    For digit d with observed proportion p_hat = count/N and baseline p0 = 0.1, the
    standard error under the null is  sqrt(p0*(1-p0)/N).  z = (p_hat - p0) / se.

    This is a STATISTICAL DEVIATION measure (how many SE the observed frequency sits
    from the fair 10% baseline). It is NOT a prediction of future outcomes.
    """
    n = len(digits)
    if n == 0:
        return [0.0] * 10
    counts = count_digits(digits)
    se = math.sqrt(UNIFORM_P * (1.0 - UNIFORM_P) / n)
    if se == 0:
        return [0.0] * 10
    return [round((counts[d] / n - UNIFORM_P) / se, 3) for d in range(10)]


# --------------------------------------------------------------------------- gap
def gap_statistics(digits: Sequence[int]) -> dict:
    """Per digit: current gap in ticks, max gap, avg gap, median gap, percentile.

    'Gap' = number of ticks between consecutive appearances. Descriptive only — a
    large gap does NOT imply a future appearance is due.
    """
    n = len(digits)
    last_positions: dict[int, int] = {}
    max_gap: dict[int, int] = {d: 0 for d in range(10)}
    gaps: dict[int, list[int]] = {d: [] for d in range(10)}
    for pos, d in enumerate(digits):
        if d in last_positions:
            gap = pos - last_positions[d] - 1
            gaps[d].append(gap)
            if gap > max_gap[d]:
                max_gap[d] = gap
        last_positions[d] = pos

    result: dict[int, dict] = {}
    for d in range(10):
        gl = gaps[d]
        avg = round(statistics.mean(gl), 2) if gl else None
        med = round(statistics.median(gl), 2) if gl else None
        current = None
        if d in last_positions:
            current = (n - 1) - last_positions[d]
        percentile = None
        if current is not None:
            denom = n - last_positions[d]
            percentile = round(current / denom * 100.0, 1) if denom else None
        result[d] = {
            "current_gap": current,
            "max_gap": max_gap[d],
            "avg_gap": avg,
            "median_gap": med,
            "gap_percentile": percentile,
            "occurrences": len(gl),
        }
    return result


# ----------------------------------------------------------------------- streak
def streak_statistics(digits: Sequence[int]) -> dict:
    """Detect same-digit streaks + parity streaks; record distribution + max.

    Parity streak orders: a run where digits share the same parity (all ODD or all
    EVEN). Same-digit streak: consecutive identical digit.
    """
    n = len(digits)
    same_hist: dict[int, int] = {}  # digit -> longest run
    same_cur: dict[int, int] = {}
    current = None
    cur_run = 0
    parity_hist = {"ODD": 0, "EVEN": 0}
    parity_cur = 0
    current_parity: str | None = None

    def note_same(d: int, run: int) -> None:
        same_hist[d] = max(same_hist.get(d, 0), run)

    for idx, d in enumerate(digits):
        if d == current:
            cur_run += 1
        else:
            cur_run = 1
            current = d
        same_cur[d] = max(same_cur.get(d, 0), cur_run)
        note_same(d, cur_run)
        p = "ODD" if d in ODD_DIGITS else "EVEN"
        if p == current_parity:
            parity_cur += 1
        else:
            parity_cur = 1
            current_parity = p
        parity_hist[p] = max(parity_hist[p], parity_cur)

    max_same = max(same_hist.values()) if same_hist else 0
    return {
        "same_digit": {
            "max_same_digit_streak": max_same,
            "per_digit_longest": same_hist,
        },
        "parity": {
            "current_parity_streak": parity_cur if n else 0,
            "max_odd_streak": parity_hist["ODD"],
            "max_even_streak": parity_hist["EVEN"],
            "current_parity": current_parity,
        },
        "n": n,
    }


# ---------------------------------------------------------------- parity analysis
def parity_analysis(digits: Sequence[int]) -> dict:
    """ODD/EVEN counts + percentages + recent parity streak vs 50/50 baseline."""
    n = len(digits)
    odd = sum(1 for d in digits if d in ODD_DIGITS)
    even = n - odd
    return {
        "n": n,
        "odd_count": odd,
        "even_count": even,
        "odd_percent": round(odd / n * 100, 2) if n else 0.0,
        "even_percent": round(even / n * 100, 2) if n else 0.0,
        "baseline_percent": 50.0,
        "odd_deviation_pp": round((odd / n * 100 - 50.0), 2) if n else 0.0,
        "even_deviation_pp": round((even / n * 100 - 50.0), 2) if n else 0.0,
    }


# ------------------------------------------------------- over / under analysis
def over_under_analysis(
    digits: Sequence[int], barrier: int | None = None
) -> dict:
    """OVER/UNDER counts + percentages for a barrier (0..8 for >, 1..9 for <).

    Semantics (Deriv digit Over/Under): OVER wins if last digit > barrier;
    UNDER wins if last digit < barrier. A single barrier supports both families.
    barrier None => default 4 (OVER: digits 5-9, UNDER: digits 0-4), documented default.
    """
    b = 4 if barrier is None else int(barrier)
    n = len(digits)
    over = sum(1 for d in digits if d > b)
    under = sum(1 for d in digits if d < b)
    equal = n - over - under  # barrier equals digit => neither wins
    return {
        "barrier": b,
        "n": n,
        "over_count": over,
        "under_count": under,
        "equal_count": equal,
        "over_percent": round(over / n * 100, 2) if n else 0.0,
        "under_percent": round(under / n * 100, 2) if n else 0.0,
        "equal_percent": round(equal / n * 100, 2) if n else 0.0,
        # fair baseline given the barrier (ignoring the equal-tick case)
        "fair_over_percent": round((9 - b) * 10.0, 2),
        "fair_under_percent": round(b * 10.0, 2),
    }


# -------------------------------------------------- matches / differs statistics
def matches_differs_analysis(digits: Sequence[int]) -> dict:
    """For each predicted digit 0..9: P(MATCH), P(DIFFERS), sample, baseline, dev.

    MATCHES on digit d wins when last digit == d (fair 1/10).
    DIFFERS on digit d wins when last digit != d (fair, conditional 9/10 given d
    excludes the tie — but because digits are uniform this observationally equals
    (n - count_d)/n; baseline 90%).
    """
    n = len(digits)
    counts = count_digits(digits)
    rows = []
    for d in range(10):
        c = counts[d]
        rows.append(
            {
                "digit": d,
                "sample": n,
                "matches_observed": round(c / n, 4) if n else None,
                "matches_baseline": 0.1,
                "matches_deviation": round((c / n - 0.1), 4) if n else None,
                "differs_observed": round((n - c) / n, 4) if n else None,
                "differs_baseline": 0.9,
                "differs_deviation": round(((n - c) / n - 0.9), 4) if n else None,
            }
        )
    return {"n": n, "rows": rows}


# ---------------------------------------------------------------- chi-square
def _gamma_lower_series(a: float, x: float) -> float:
    """Lower incomplete gamma gamma(a, x) ~ exp(-x + a ln x) * sum_{n} (x^(a+n)/gamma(a+n+1))."""
    term = 1.0 / a
    total = term
    for n in range(1, 600):
        term *= x / (a + n)
        total += term
        if abs(term) < abs(total) * 1e-9:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * total


def _gammainc_lower_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) = gamma(a, x) / Gamma(a)."""
    if x == 0:
        return 0.0
    if x < a + 1.0:
        return _gamma_lower_series(a, x)
    # Series diverges in the right tail; use the continued-fraction definition of
    # the COMPLEMENT Q(a,x) and subtract:  P = 1 - Q.
    b = x + 1.0 - a
    c = 1.0 / 1e-30
    d = 1.0 / b
    h = d
    for i in range(1, 300):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < 1e-30:
            d = 1e-30
        c = b + an / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-7:
            break
    q = math.exp(-x + a * math.log(x) - math.lgamma(a)) * h
    return max(0.0, min(1.0, 1.0 - q))


def _chi2_cdf(k: float, x: float) -> float:
    """CDF of the chi-square distribution with k dof: P(k/2, x/2)."""
    if x <= 0:
        return 0.0
    return _gammainc_lower_p(k / 2.0, x / 2.0)


def chi_square_uniformity(digits: Sequence[int]) -> dict:
    """Pearson chi-square goodness-of-fit against the uniform digit distribution.

    statistic = sum_d (O_d - E)^2 / E,  E = N/10,  df = 9.
    p = P(chi2_9 > statistic). Requires N >= CHI2_MIN_SAMPLE.

    A significant p does NOT mean a guaranteed predictive edge; it only flags that the
    observed digit spread is unlikely to be exactly uniform.
    """
    n = len(digits)
    if n < CHI2_MIN_SAMPLE:
        return {
            "n": n,
            "applicable": False,
            "reason": f"sample below {CHI2_MIN_SAMPLE}",
            "statistic": None,
            "degrees_of_freedom": 9,
            "p_value": None,
            "significant": False,
            "interpretation": "Insufficient sample for chi-square uniformity test.",
        }
    counts = count_digits(digits)
    expected = n / 10.0
    statistic = sum((c - expected) ** 2 / expected for c in counts)
    df = 9
    p_value = 1.0 - _chi2_cdf(df, statistic)
    significant = p_value < 0.05
    return {
        "n": n,
        "applicable": True,
        "statistic": round(statistic, 4),
        "degrees_of_freedom": df,
        "p_value": round(p_value, 5),
        "significant": significant,
        "interpretation": (
            "Observed digit distribution deviates from uniform at p<0.05 "
            "(descriptive; not a predictive guarantee)."
            if significant
            else "No significant deviation from uniform distribution (p>=0.05)."
        ),
    }


# ------------------------------------------------------- multi-window agreement
def multi_window_state(short: dict, medium: dict, long: dict) -> str:
    """STABLE / MULTI_WINDOW_SUPPORT / CONFLICTING / INSUFFICIENT_DATA.

    Uses the most-frequent digit of each window. STABLE when all three windows agree on
    the leading digit; MULTI_WINDOW_SUPPORT when short and medium agree but long has too
    little data; CONFLICTING when short disagrees with medium; INSUFFICIENT when windows
    are too small to read.
    """
    def leader(state: dict) -> int | None:
        n = state.get("n", 0)
        mf = state.get("most_frequent", -1)
        if n == 0 or mf < 0:
            return None
        return mf

    a = leader(short)
    b = leader(medium)
    c = leader(long)
    if a is None:
        return "INSUFFICIENT_DATA"
    if b is None:
        # short has data, medium may have partial (window bigger). Read long too.
        return "MULTI_WINDOW_SUPPORT" if c == a or c is None else "CONFLICTING"
    if a == b:
        if c is None:
            return "MULTI_WINDOW_SUPPORT"
        return "STABLE" if c == a else "CONFLICTING"
    return "CONFLICTING"


__all__ = [
    "CHI2_MIN_SAMPLE",
    "DIGITS",
    "EVEN_DIGITS",
    "MIN_SAMPLE",
    "ODD_DIGITS",
    "UNIFORM_P",
    "Z_SIGMA_THRESHOLD",
    "chi_square_uniformity",
    "count_digits",
    "digit_frequency",
    "gap_statistics",
    "matches_differs_analysis",
    "multi_window_state",
    "over_under_analysis",
    "parity_analysis",
    "streak_statistics",
    "z_scores",
]