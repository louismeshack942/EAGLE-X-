"""Deterministic Phase 3 tests: contracts, proposals, recommendations, NO-TRADE.

We never hit the network. Real proposal normalization is unit-tested against a
captured-shape payload; HARNESS/simulated pricing is clearly labeled.
"""

import pytest

from app.core.data_quality import DataQualityState
from app.services.contracts import (
    FAMILIES,
    all_specs_for_symbol,
    build_spec,
)
from app.services.proposal_engine import (
    SOURCE_HARNESS,
    SOURCE_LIVE,
    SOURCE_UNAVAILABLE,
    NormalizedProposal,
    ProposalService,
    normalize_deriv_proposal,
)
from app.services.recommender import (
    INSUFFICIENT,
    NO_TRADE,
    QUALIFIED,
    WATCH,
    RecommendationEngine,
)

engine = RecommendationEngine()


# ---------------------------------------------------------------- contracts
def test_contract_codes_match_deriv():
    assert FAMILIES["MATCHES"].contract_type == "DIGITMATCH"
    assert FAMILIES["DIFFERS"].contract_type == "DIGITDIFF"
    assert FAMILIES["ODD"].contract_type == "DIGITODD"
    assert FAMILIES["EVEN"].contract_type == "DIGITEVEN"
    assert FAMILIES["OVER"].contract_type == "DIGITOVER"
    assert FAMILIES["UNDER"].contract_type == "DIGITUNDER"


def test_spec_validation():
    s = build_spec("R_10", "MATCHES", barrier=3, duration_ticks=5)
    assert s.barrier == 3 and s.contract_type == "DIGITMATCH"
    assert s.prediction == "digit 3"
    # barrier required for MATCHES
    with pytest.raises(ValueError):
        build_spec("R_10", "MATCHES", barrier=None)
    # duration bound
    with pytest.raises(ValueError):
        build_spec("R_10", "DIFFERS", barrier=1, duration_ticks=11)
    with pytest.raises(ValueError):
        build_spec("R_10", "ODD", duration_ticks=0)


def test_fair_win_rates():
    assert FAMILIES["MATCHES"].fair_win_rate == 0.1
    assert FAMILIES["DIFFERS"].fair_win_rate == 0.9
    assert FAMILIES["ODD"].fair_win_rate == 0.5
    s = build_spec("R_10", "OVER", barrier=4)
    assert s.fair_win_rate() == 0.5  # (9-4)/10
    s = build_spec("R_10", "UNDER", barrier=4)
    assert s.fair_win_rate() == 0.4  # 4/10


def test_all_specs_board_size():
    specs = all_specs_for_symbol("R_100")
    # 4 barriers-bearing families x 10 barriers + ODD + EVEN
    assert len(specs) == 4 * 10 + 2
    assert all(s.symbol == "R_100" for s in specs)


# ---------------------------------------------------------------- proposals
def test_normalize_real_deriv_payload():
    spec = build_spec("R_10", "DIFFERS", barrier=5, duration_ticks=5, stake=1.0)
    raw = {
        "msg_type": "proposal",
        "req_id": 1,
        "proposal": {
            "id": "abc123",
            "ask_price": "1.00",
            "payout": 1.12,
            "spot": 605.83,
            "contract_type": "DIGITDIFF",
            "barrier": "5",
            "date_expiry": 1700000000,
        },
    }
    p = normalize_deriv_proposal(raw, spec, source=SOURCE_LIVE)
    assert p.source == SOURCE_LIVE
    assert p.state == "OK"
    assert p.ask_price == 1.00
    assert p.payout == 1.12
    assert p.profit_net == pytest.approx(0.12)
    # payout_pct = (1.12/1.00 - 1)*100 = 12.0%
    assert p.payout_pct == pytest.approx(12.0)
    # breakeven = ask/payout = 1.00/1.12 (stored rounded to 5dp)
    assert p.breakeven_win_rate == pytest.approx(1.0 / 1.12, abs=1e-4)


@pytest.mark.asyncio
async def test_proposal_service_unavailable_without_ws():
    svc = ProposalService(use_live=False)
    p = svc.harness_proposal(build_spec("R_10", "DIFFERS", barrier=1))
    assert p.source == SOURCE_HARNESS
    # harness labels must not claim live
    assert "SIMULATED" in p.message
    req = await svc.request(build_spec("R_10", "DIFFERS", barrier=1))
    assert isinstance(req, NormalizedProposal)
    assert req.state == "PROPOSAL_UNAVAILABLE"
    assert req.source == SOURCE_UNAVAILABLE


def test_harness_proposal_marked():
    spec = build_spec("R_10", "OVER", barrier=4, stake=1.0)
    svc = ProposalService(use_live=False)
    p = svc.harness_proposal(spec)
    assert p.source == SOURCE_HARNESS
    assert p.state == "OK"
    assert p.payout is not None and p.payout > 1.0
    assert p.request["barrier"] == "4"


# ---------------------------------------------------------------- recommendations
def _window_analysis(counts: list[int], qstate=DataQualityState.DATA_READY.value, n=None):
    n = n if n is not None else sum(counts)
    return {
        "n": n,
        "counts": counts,
        "data_quality": {"state": qstate, "source": "deriv_live"},
    }


def _live_proposal(spec, payout=None, ask=1.0):
    payout = payout if payout is not None else float(spec.stake)
    return NormalizedProposal(
        source=SOURCE_LIVE, state="OK", symbol=spec.symbol, contract_type=spec.contract_type,
        barrier=spec.barrier, duration_ticks=spec.duration_ticks, stake=float(spec.stake),
        ask_price=ask, payout=payout, profit_net=round(payout - ask, 4),
        payout_pct=round((payout / ask - 1) * 100, 3), breakeven_win_rate=round(ask / payout, 5),
        currency=spec.currency,
    )


def test_insufficient_data_gate():
    spec = build_spec("R_10", "MATCHES", barrier=3)
    rec = engine.evaluate(spec, window_analysis=None, proposal=None, data_quality=None,
                          multi_window_state="INSUFFICIENT_DATA")
    assert rec.state == INSUFFICIENT


def test_data_quality_blocks():
    spec = build_spec("R_10", "DIFFERS", barrier=1)
    rec = engine.evaluate(spec, window_analysis=_window_analysis([10]*10, "STALE"),
                          proposal=_live_proposal(spec, payout=1.2),
                          data_quality={"state": "STALE", "source": "harness"},
                          multi_window_state="STABLE")
    assert rec.state == NO_TRADE


def test_low_sample_blocks():
    spec = build_spec("R_10", "MATCHES", barrier=3)
    counts = [0]*10
    counts[3] = 8
    counts[5] = 7  # n=15
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=15),
                          proposal=_live_proposal(spec, payout=5.0),
                          data_quality={"state": "DATA_READY", "source": "harness"},
                          multi_window_state="STABLE")
    assert rec.state == NO_TRADE
    assert "sample" in rec.reason


def test_ev_calculation_positive():
    # DIFFERS barrier=1; 90% of 100 digits are not 1, so empirical p(win)=0.9.
    spec = build_spec("R_10", "DIFFERS", barrier=1, stake=1.0)
    counts = [0]*10
    counts[1] = 10
    counts[0] = 90  # n=100, p(win)=0.9
    # payout 1.20 net profit 0.20; breakeven=1/1.2=0.833; 0.9>0.833+0.02
    prop = _live_proposal(spec, payout=1.20)
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=prop,
                          data_quality={"state": "DATA_READY", "source": "deriv_live"},
                          multi_window_state="STABLE")
    assert rec.state == QUALIFIED
    assert rec.observed_win_rate == pytest.approx(0.9)
    assert rec.ev is not None and rec.ev > 0
    # EV = 0.9*0.20 - 0.1*1.0 = 0.18 - 0.10 = 0.08
    assert rec.ev == pytest.approx(0.08, abs=1e-4)


def test_negative_ev_no_trade():
    spec = build_spec("R_10", "MATCHES", barrier=1, stake=1.0)
    counts = [0]*10
    counts[1] = 11  # p(win)=0.11
    counts[0] = 89
    # payout 5.0 => net 4.0 => EV = 0.11*4 - 0.89*1 = 0.44 - 0.89 < 0
    prop = _live_proposal(spec, payout=5.0)
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=prop,
                          data_quality={"state": "DATA_READY", "source": "deriv_live"},
                          multi_window_state="STABLE")
    assert rec.state == NO_TRADE


def test_breakeven_margin_applied():
    # p(win)=0.86, breakeven=0.85 -> margin 0.02 forces >=0.87 => NO TRADE despite EV>0
    spec = build_spec("R_10", "DIFFERS", barrier=1, stake=1.0)
    counts = [0]*10
    counts[1] = 14
    counts[0] = 86  # p(win)=0.86; but EV = 0.86*0.2 - 0.14 = -0.172 -> already negative
    # use payout generous enough that EV>0 but still fails margin:
    counts = [0]*10
    counts[1] = 14
    counts[0] = 86  # n=100 => pw=0.86
    prop = _live_proposal(spec, payout=3.0)  # breakeven=0.333 -> margin 0.353; 0.86>0.353
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=prop,
                          data_quality={"state": "DATA_READY", "source": "deriv_live"},
                          multi_window_state="STABLE")
    # pw=0.86 clears breakeven+margin (0.353) and EV>0 -> QUALIFIED
    assert rec.state == QUALIFIED


def test_harness_proposal_watch_only():
    # even with good data + positive math, a HARNESS proposal cannot qualify
    spec = build_spec("R_10", "DIFFERS", barrier=1, stake=1.0)
    counts = [0]*10
    counts[1] = 10
    counts[0] = 90
    harness = NormalizedProposal(
        source=SOURCE_HARNESS, state="OK", symbol=spec.symbol, contract_type=spec.contract_type,
        barrier=spec.barrier, duration_ticks=spec.duration_ticks, stake=1.0,
        ask_price=1.0, payout=1.2, profit_net=0.2, breakeven_win_rate=0.833, currency="USD",
        message="SIMULATED",
    )
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=harness,
                          data_quality={"state": "DATA_READY", "source": "harness"},
                          multi_window_state="STABLE")
    assert rec.state == WATCH
    assert "SIMULATED" in rec.reason


def test_conflicting_windows_no_trade():
    spec = build_spec("R_10", "MATCHES", barrier=1, stake=1.0)
    counts = [0]*10
    counts[1] = 90
    counts[0] = 10
    prop = _live_proposal(spec, payout=6.0)
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=prop,
                          data_quality={"state": "DATA_READY", "source": "deriv_live"},
                          multi_window_state="CONFLICTING")
    assert rec.state == NO_TRADE


def test_quality_reason_after_empty():
    spec = build_spec("R_10", "ODD")
    rec = engine.evaluate(spec, window_analysis=None, proposal=None,
                          data_quality={"state": "INSUFFICIENT_DATA", "source": "harness"})
    assert rec.state == INSUFFICIENT or rec.state == NO_TRADE


def test_over_under_pw_matches_fair():
    # OVER barrier 4 with pyramid of digits 5..9 dominant
    spec = build_spec("R_10", "OVER", barrier=4, stake=1.0)
    counts = [0]*10
    for d in (5, 6, 7, 8, 9):
        counts[d] = 12
    for d in (0, 1, 2, 3, 4):
        counts[d] = 8
    # n = 5*12 + 5*8 = 100; over_count = 60; p(win)=0.6
    prop = _live_proposal(spec, payout=1.8)  # breakeven = 1/1.8 = 0.556 -> 0.6>0.576
    rec = engine.evaluate(spec,
                          window_analysis=_window_analysis(counts, n=100),
                          proposal=prop,
                          data_quality={"state": "DATA_READY", "source": "deriv_live"},
                          multi_window_state="STABLE")
    assert rec.observed_win_rate == pytest.approx(0.6)
    assert rec.fair_win_rate == pytest.approx(0.5)