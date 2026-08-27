"""Deterministic Phase 2 analytics tests (pure math + window engine).

Expected values are hand-computed against the documented formulas.
"""

import math

import pytest

from app.core.data_quality import DataQualityState, assess_data_quality
from app.core.ticks import NormalizedTick, normalize_tick
from app.services.analytics import (
    chi_square_uniformity,
    digit_frequency,
    gap_statistics,
    matches_differs_analysis,
    multi_window_state,
    over_under_analysis,
    parity_analysis,
    streak_statistics,
    z_scores,
)
from app.services.analysis_engine import AnalysisManager
from app.services.window_engine import WindowManager


def _tick(symbol="R_10", epoch=0, quote=0.0, provider="harness", digit=None):
    return normalize_tick(
        symbol=symbol,
        epoch_ms=epoch + 1_700_000_000_000,
        quote=quote,
        provider=provider,
    ) if digit is None else NormalizedTick(
        symbol=symbol,
        epoch_ms=epoch + 1_700_000_000_000,
        quote=float(quote),
        last_digit=digit,
        provider=provider,
    )


# ------------------------------------------------------------------ digit frequency
def test_digit_frequency_counts_and_rank():
    digits = [0, 0, 0, 1, 1, 2]
    f = digit_frequency(digits)
    assert f["n"] == 6
    assert f["counts"][0] == 3 and f["counts"][1] == 2 and f["counts"][2] == 1
    assert f["most_frequent"] == 0
    # all missing digits tie at 0; rank picks the largest digit among the tie
    assert f["least_frequent"] == 9
    assert f["expected_per_digit"] == round(6 / 10.0, 3)


def test_digit_frequency_percentages_and_deviation():
    digits = [5, 5, 5, 5, 5, 1, 2, 3, 4, 6]  # n=10
    f = digit_frequency(digits)
    assert f["percentages"][5] == 50.0
    # deviation_pp for digit 5 = (5/10 - 1/10)*100 = 40pp
    assert f["deviation_pp"][5] == 40.0


# ------------------------------------------------------------------------- z-score
def test_z_score_uniform_flat_data():
    # 10 of each digit, n=100 -> all p_hat=0.1, z=0.0
    digits = [d for d in range(10) for _ in range(10)]
    z = z_scores(digits)
    assert all(abs(v) < 1e-6 for v in z)


def test_z_score_known_value():
    # n=100 with count[7]=50, so p_hat=0.5; se=sqrt(0.1*0.9/100)=0.03
    # z = (0.5-0.1)/0.03 = 13.333
    digits = [7] * 50 + [3] * 30 + [5] * 20
    assert len(digits) == 100
    z = z_scores(digits)
    assert abs(z[7] - round((0.5 - 0.1) / math.sqrt(0.1 * 0.9 / 100.0), 3)) < 1e-6


# --------------------------------------------------------------------------- gap
def test_gap_statistics_known():
    # digit 2 at pos0 and pos4: gap = pos4-pos0-1 = 3; current gap = 0
    dg = [2, 5, 6, 7, 2]
    g2 = gap_statistics(dg)
    assert g2[2]["max_gap"] == 3
    assert g2[2]["current_gap"] == 0
    # digit 1 seen at pos1 and pos5: gap = 5-1-1 = 3
    dg2 = [0, 1, 3, 4, 5, 1]
    g3 = gap_statistics(dg2)
    assert g3[1]["max_gap"] == 3


# ----------------------------------------------------------------------- streak
def test_same_digit_streak_known():
    digits = [3, 3, 3, 5, 5, 3]
    s = streak_statistics(digits)
    assert s["same_digit"]["max_same_digit_streak"] == 3
    assert s["same_digit"]["per_digit_longest"][3] == 3
    assert s["same_digit"]["per_digit_longest"][5] == 2


def test_parity_streak_known():
    digits = [1, 3, 5, 2, 4, 6, 6]  # ODD x3 then EVEN x4
    s = streak_statistics(digits)
    assert s["parity"]["max_odd_streak"] == 3
    assert s["parity"]["max_even_streak"] == 4
    assert s["parity"]["current_parity"] == "EVEN"
    assert s["parity"]["current_parity_streak"] == 4  # last 4 are 2,4,6,6


# ---------------------------------------------------------------- parity analysis
def test_parity_analysis_counts_and_baseline():
    digits = [1, 2, 3, 4, 5, 6]  # odd {1,3,5}=3, even{2,4,6}=3
    p = parity_analysis(digits)
    assert p["odd_count"] == 3 and p["even_count"] == 3
    assert p["odd_percent"] == 50.0 and p["baseline_percent"] == 50.0


# ------------------------------------------------------- over / under analysis
def test_over_under_known():
    # barrier=4 (default): OVER wins d>4 (5-9), UNDER wins d<4 (0-3)
    digits = [5, 6, 7, 8, 9, 0, 1, 2, 3, 4]
    ou = over_under_analysis(digits, barrier=4)
    assert ou["over_count"] == 5 and ou["under_count"] == 4 and ou["equal_count"] == 1
    assert ou["fair_over_percent"] == 50.0 and ou["fair_under_percent"] == 40.0


# -------------------------------------------------- matches / differs statistics
def test_matches_differs_known():
    digits = [7, 7, 7, 1, 2, 3, 4, 5, 6, 8]  # n=10, digit 7 appears 3x
    md = matches_differs_analysis(digits)
    row7 = next(r for r in md["rows"] if r["digit"] == 7)
    assert row7["matches_observed"] == 0.3
    assert row7["differs_observed"] == 0.7
    assert row7["differs_baseline"] == 0.9


# ---------------------------------------------------------------- chi-square
def test_chi_square_uniform_flat():
    digits = [d for d in range(10) for _ in range(10)]  # perfectly uniform n=100
    c = chi_square_uniformity(digits)
    assert c["applicable"] is True
    assert c["statistic"] == 0.0
    assert c["p_value"] == 1.0
    assert c["significant"] is False


def test_chi_square_known_p_value():
    # Hand reference: chi2=15.0, df=9 -> p ~ 0.0908 (from standard tables).
    # Construct data to yield statistic = 15.0 exactly is awkward; instead check
    # monotonicity: a larger statistic must give a smaller p.
    p1 = chi_square_uniformity_synthetic(10.0)
    p2 = chi_square_uniformity_synthetic(30.0)
    assert p2 < p1


def chi_square_uniformity_synthetic(statistic: float):
    # Build n=1000 uniform-ish then override by scaling won't be exact; instead test the
    # threshold via the internal CDF directly.
    from app.services.analytics import _chi2_cdf

    return 1.0 - _chi2_cdf(9.0, statistic)


def test_chi_square_cdf_sanity():
    from app.services.analytics import _chi2_cdf

    assert _chi2_cdf(9.0, 0.0) == 0.0
    # CDF is monotonic increasing in x
    assert _chi2_cdf(9.0, 15.0) < _chi2_cdf(9.0, 30.0)
    # P(chi2_9 <= 9) ~ 0.56, P(chi2_9 <= 15) ~ 0.91, P(chi2_9 <= 30) ~ 0.9996
    assert abs(_chi2_cdf(9.0, 9.0) - 0.56) < 0.05
    assert abs(_chi2_cdf(9.0, 15.0) - 0.91) < 0.03
    assert _chi2_cdf(9.0, 30.0) > 0.999


def test_chi_square_insufficient_sample():
    c = chi_square_uniformity([1, 2, 3])
    assert c["applicable"] is False


# ------------------------------------------------------- multi-window agreement
def test_multi_window_stable():
    short = {"n": 50, "most_frequent": 7}
    medium = {"n": 250, "most_frequent": 7}
    long = {"n": 1000, "most_frequent": 7}
    assert multi_window_state(short, medium, long) == "STABLE"


def test_multi_window_conflict():
    short = {"n": 50, "most_frequent": 3}
    medium = {"n": 250, "most_frequent": 7}
    long = {"n": 1000, "most_frequent": 7}
    assert multi_window_state(short, medium, long) == "CONFLICTING"


def test_multi_window_insufficient():
    literal = {"n": 0, "most_frequent": -1}
    assert multi_window_state(literal, {"n": 0, "most_frequent": -1}, {"n": 0, "most_frequent": -1}) == "INSUFFICIENT_DATA"


# ---------------------------------------------------------------- window engine
def test_window_rolling_bounds():
    wm = WindowManager("R_10", "harness")
    for i in range(120):
        wm.push(_tick(digit=i % 10, epoch=i))
    w = wm.window(100)
    assert w.n == 100  # bounded to 100
    assert w.digits[0] == 20 % 10  # 0
    assert w.digits[-1] == 119 % 10  # 9
    assert wm.window(1000).n == 120


def test_window_duplicate_rejection():
    wm = WindowManager("R_10", "harness")
    t = _tick(digit=3, epoch=5)
    wm.push(t)
    wm.push(NormalizedTick(symbol="R_10", epoch_ms=t.epoch_ms, quote=t.quote, last_digit=3, provider="harness"))
    assert wm.duplicate_ticks == 1
    assert wm.n_total == 1


def test_window_provider_mixing_rejected():
    wm = WindowManager("R_10", "harness")
    with pytest.raises(ValueError):
        wm.push(_tick(digit=1, provider="deriv_live"))


def test_window_invalid_digit_rejected():
    wm = WindowManager("R_10", "harness")
    # NaN quote drives last_digit to -1 (no digit); post-init keeps -1
    wm.push(NormalizedTick(symbol="R_10", epoch_ms=1, quote=float("nan"), last_digit=-1, provider="harness"))
    assert wm.invalid_ticks == 1
    assert wm.n_total == 0
    assert wm.window(100).n == 0


# ---------------------------------------------------------------- data quality
def test_data_quality_states():
    now = 1_700_000_000_100
    # DATA_READY
    q = assess_data_quality(n=100, window_size=100, newest_epoch_ms=now, now_ms=now,
                            duplicate_ticks=0, invalid_ticks=0, source="harness", connection_state="connected")
    assert q.state == DataQualityState.DATA_READY
    assert q.window_complete is True

    # INSUFFICIENT
    q = assess_data_quality(n=5, window_size=100, newest_epoch_ms=now, now_ms=now,
                            duplicate_ticks=0, invalid_ticks=0, source="harness", connection_state="connected")
    assert q.state == DataQualityState.INSUFFICIENT_DATA

    # not complete
    q = assess_data_quality(n=50, window_size=100, newest_epoch_ms=now, now_ms=now,
                            duplicate_ticks=0, invalid_ticks=0, source="harness", connection_state="connected")
    assert q.window_complete is False

    # STALE (old data)
    q = assess_data_quality(n=100, window_size=100, newest_epoch_ms=now - 60_000, now_ms=now,
                            duplicate_ticks=0, invalid_ticks=0, source="harness", connection_state="connected")
    assert q.state == DataQualityState.STALE

    # DISCONNECTED with no data
    q = assess_data_quality(n=0, window_size=100, newest_epoch_ms=0, now_ms=now,
                            duplicate_ticks=0, invalid_ticks=0, source="harness", connection_state="disconnected")
    assert q.state == DataQualityState.DISCONNECTED


# ---------------------------------------------------------------- analysis manager
def test_analysis_manager_real_time_and_snapshot():
    import time

    am = AnalysisManager()
    mgr = am.manager("R_50", "harness")
    am.mark_connection("R_50", "connected")
    now = int(time.time() * 1000)
    for i in range(110):
        t = NormalizedTick(
            symbol="R_50", epoch_ms=now - (110 - i), quote=1000.0 + i,
            last_digit=i % 10, provider="harness",
        )
        mgr.push(t)
    snap = am.snapshot("R_50")
    assert snap["source"] == "harness"
    assert snap["connection_state"] == "connected"
    assert 100 in snap["windows"]
    assert snap["windows"][100]["n"] == 100
    assert snap["windows"][100]["data_quality"]["state"] == "DATA_READY"
    # multi-window summary present
    assert snap["multi_window"]["state"] in ("STABLE", "MULTI_WINDOW_SUPPORT", "CONFLICTING", "INSUFFICIENT_DATA")


def test_analysis_manager_empty_snapshot():
    am = AnalysisManager()
    snap = am.snapshot("R_01")
    assert snap["symbol"] == "R_01"
    assert snap["source"] == ""
    assert snap["multi_window"]["state"] == "INSUFFICIENT_DATA"


def test_harness_live_separation_in_registry():
    am = AnalysisManager()
    am.manager("R_10", "harness").push(_tick(digit=1))
    am.manager("R_10", "deriv_live").push(_tick(digit=2, provider="deriv_live"))
    # distinct managers, both track R_10 independently
    assert am._managers[("R_10", "harness")].n_total == 1
    assert am._managers[("R_10", "deriv_live")].n_total == 1


def test_digit_extraction_precision_formats():
    from app.core.ticks import last_digit_from_quote

    assert last_digit_from_quote(5000.0) == 0
    assert last_digit_from_quote(5000.000001) == 1
    assert last_digit_from_quote(3.14) == 4
    assert last_digit_from_quote(2.718) == 8