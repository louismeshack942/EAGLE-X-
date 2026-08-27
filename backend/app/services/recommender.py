"""Read-only recommendation engine.

Combines Phase-2 empirical probability with Phase-3 real Deriv proposal pricing to
produce a READ-ONLY recommendation:
    QUALIFIED | WATCH | NO TRADE | INSUFFICIENT DATA

EV model (documented, using the REAL Deriv payout):
    net_profit = payout - stake   (when the contract wins)
    loss      = stake             (when it loses)
    EV = P(win) * net_profit - (1 - P(win)) * stake

    P(win) is the EMPIRICAL observed frequency over the window baseline — an estimate,
    never a guarantee. If the estimate is not defensible we do not compute a number.

Honesty rules:
    - Frequency is never presented as certainty.
    - A high win rate alone does NOT make a trade good; the payout must also beat the
      breakeven rate.
    - HARNESS proposals are simulations, clearly marked, never implied to be real.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.data_quality import DataQualityState
from app.services.analytics import over_under_analysis, parity_analysis
from app.services.contracts import ContractSpec
from app.services.proposal_engine import (
    SOURCE_HARNESS,
    SOURCE_UNAVAILABLE,
    NormalizedProposal,
)

QUALIFIED = "QUALIFIED"
WATCH = "WATCH"
NO_TRADE = "NO TRADE"
INSUFFICIENT = "INSUFFICIENT DATA"

# Minimum empirical sample for a defensible probability estimate.
MIN_PROB_SAMPLE = 40
# Positive EV required to consider a trade (strictly > 0).
MIN_EV = 0.0
# Observed win rate must clear the contract breakeven rate by >= this margin.
MIN_EDGE_MARGIN = 0.02


@dataclass
class Recommendation:
    symbol: str
    family: str
    contract_type: str
    barrier: int | None
    prediction: str
    stake: float
    duration_ticks: int
    state: str  # QUALIFIED | WATCH | NO TRADE | INSUFFICIENT DATA
    reason: str
    observed_win_rate: float | None = None
    sample_size: int = 0
    fair_win_rate: float | None = None
    breakeven_win_rate: float | None = None
    ask_price: float | None = None
    payout: float | None = None
    profit_net: float | None = None
    ev: float | None = None
    data_quality_state: str = ""
    data_source: str = ""
    proposal_source: str = ""
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "family": self.family,
            "contract_type": self.contract_type,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "stake": self.stake,
            "duration_ticks": self.duration_ticks,
            "state": self.state,
            "reason": self.reason,
            "observed_win_rate": self.observed_win_rate,
            "sample_size": self.sample_size,
            "fair_win_rate": self.fair_win_rate,
            "breakeven_win_rate": self.breakeven_win_rate,
            "ask_price": self.ask_price,
            "payout": self.payout,
            "profit_net": self.profit_net,
            "ev": self.ev,
            "data_quality_state": self.data_quality_state,
            "data_source": self.data_source,
            "proposal_source": self.proposal_source,
            "evidence": self.evidence or {},
        }


class RecommendationEngine:
    def __init__(self) -> None:
        pass

    def evaluate(
        self,
        spec: ContractSpec,
        *,
        window_analysis: dict | None,
        proposal: NormalizedProposal | None,
        data_quality: dict | None,
        multi_window_state: str = "INSUFFICIENT_DATA",
    ) -> Recommendation:
        """Produce a recommendation for one contract.

        Explicit ordering of hard NO-TRADE gates (§25). Every gate that fails returns
        NO TRADE / INSUFFICIENT DATA with an honest reason.
        """
        base = Recommendation(
            symbol=spec.symbol, family=spec.family, contract_type=spec.contract_type,
            barrier=spec.barrier, prediction=spec.prediction, stake=float(spec.stake),
            duration_ticks=spec.duration_ticks, state=INSUFFICIENT, reason="",
            data_source=(data_quality or {}).get("source", ""),
            evidence={},
        )

        # ---- data quality ------------------------------------------------
        dq_state = (data_quality or {}).get("state", DataQualityState.INSUFFICIENT_DATA.value)
        base.data_quality_state = dq_state
        if dq_state != DataQualityState.DATA_READY.value:
            base.state = INSUFFICIENT if dq_state == DataQualityState.INSUFFICIENT_DATA.value else NO_TRADE
            base.reason = _quality_reason(dq_state)
            return base

        if not window_analysis:
            base.state = INSUFFICIENT
            base.reason = "No window analysis available."
            return base

        # ---- empirical win probability ------------------------------------
        pw, sample, evidence = self._empirical_win_rate(spec, window_analysis)
        base.observed_win_rate = pw
        base.sample_size = sample
        base.evidence = evidence
        if pw is None or sample < MIN_PROB_SAMPLE:
            base.state = NO_TRADE
            base.reason = (
                f"Insufficient empirical sample (n={sample}) to estimate P(win) reliably."
            )
            return base

        # ---- proposal / pricing -------------------------------------------
        if proposal is None or proposal.state != "OK":
            base.state = NO_TRADE
            base.reason = proposal.message if proposal else "Missing proposal (pricing unavailable)."
            base.proposal_source = (proposal.source if proposal else SOURCE_UNAVAILABLE)
            return base
        base.ask_price = proposal.ask_price
        base.payout = proposal.payout
        base.profit_net = proposal.profit_net
        base.breakeven_win_rate = proposal.breakeven_win_rate
        base.proposal_source = proposal.source
        base.fair_win_rate = spec.fair_win_rate()

        # Proposal source must not be silently mixed: a HARNESS (simulated) price is
        # only ever advisory; never treated as a real quote for a QUALIFIED call.
        if proposal.source == SOURCE_HARNESS:
            base.state = WATCH
            base.reason = (
                "SIMULATED (HARNESS) proposal — not a real Deriv quote. "
                "Cannot qualify based on a simulated price."
            )
            base.ev = None
            return base

        if proposal.ask_price is None or proposal.payout is None:
            base.state = NO_TRADE
            base.reason = "Proposal missing ask_price/payout."
            return base
        if proposal.payout <= 0:
            base.state = NO_TRADE
            base.reason = "Invalid payout (<= 0)."
            return base
        breakeven = proposal.breakeven_win_rate
        if breakeven is None:
            breakeven = spec.fair_win_rate()

        # ---- EV -------------------------------------------------------------
        if pw <= 0 or pw >= 1:
            base.state = NO_TRADE
            base.reason = f"Unusual empirical P(win)={pw}; refusing."
            return base
        net_profit = proposal.profit_net if proposal.profit_net is not None else (proposal.payout - float(spec.stake))
        ev = pw * net_profit - (1 - pw) * float(spec.stake)
        base.ev = round(ev, 4)

        # ---- positive + defensive gates -------------------------------------
        if ev < MIN_EV:
            base.state = NO_TRADE
            base.reason = (
                f"Negative/non-positive EV ({ev:.4f}) — the Deriv payout does not justify "
                f"the empirical win rate."
            )
            return base
        if breakeven and pw < breakeven + MIN_EDGE_MARGIN:
            base.state = NO_TRADE
            base.reason = (
                f"Empirical win rate {pw:.3f} does not clear the payout breakeven "
                f"{breakeven:.3f} by the required margin."
            )
            return base
        if multi_window_state in ("CONFLICTING", "INSUFFICIENT_DATA"):
            base.state = (
                NO_TRADE if multi_window_state == "CONFLICTING" else WATCH
            )
            base.reason = (
                "Horizons conflict (CONFLICTING)." if multi_window_state == "CONFLICTING"
                else "Not enough multi-window agreement; watch only."
            )
            return base

        base.state = QUALIFIED
        base.reason = (
            f"Empirical P(win) {pw:.3f} (n={sample}) vs Deriv payout → breakeven {breakeven:.3f}; "
            f"EV {ev:.4f} > 0. Read-only; no trade placed."
        )
        return base

    def _empirical_win_rate(
        self, spec: ContractSpec, window_analysis: dict
    ) -> tuple[float | None, int, dict]:
        """Estimate P(win) from the analyzed window for this contract family.

        Returns (pw, sample, evidence) where pw is None if undetermined. `window_analysis`
        is the per-window dict from the analysis snapshot: it carries `n` and the digit
        `counts` (the raw digit list is recomputed from counts for parity/over-under).
        """
        n = window_analysis.get("n", 0)
        evidence: dict = {"n": n}

        counts: list[int] = list(window_analysis.get("counts") or [0] * 10)
        if not any(counts):
            return None, n, evidence

        if spec.family in ("MATCHES", "DIFFERS"):
            d = spec.barrier or 0
            c = counts[d] if 0 <= d <= 9 else 0
            if spec.family == "MATCHES":
                pw = c / n if n else None
            else:
                pw = (n - c) / n if n else None
            evidence["matches_count"] = c
        elif spec.family in ("ODD", "EVEN"):
            pa = parity_analysis(_digits_from_counts(counts))
            pw = (pa["odd_count"] / n) if spec.family == "ODD" else (pa["even_count"] / n)
            evidence["odd_count"] = pa["odd_count"]
            evidence["even_count"] = pa["even_count"]
        elif spec.family in ("OVER", "UNDER"):
            ou = over_under_analysis(_digits_from_counts(counts), barrier=(spec.barrier or 0))
            pw = (ou["over_count"] / n) if spec.family == "OVER" else (ou["under_count"] / n)
            evidence["over_count"] = ou["over_count"]
            evidence["under_count"] = ou["under_count"]
        else:
            return None, n, evidence
        if pw is None:
            return None, n, evidence
        return round(pw, 4), n, evidence


def _digits_from_counts(counts: list[int]) -> list[int]:
    out: list[int] = []
    for d, c in enumerate(counts):
        out.extend([d] * c)
    return out


def _quality_reason(state: str) -> str:
    return {
        DataQualityState.DATA_READY.value: "Data ready.",
        DataQualityState.INSUFFICIENT_DATA.value: "Insufficient data for a qualified analysis.",
        DataQualityState.STALE.value: "Data is stale — refusing.",
        DataQualityState.DISCONNECTED.value: "Feed disconnected — refusing.",
        DataQualityState.INVALID.value: "Invalid data — refusing.",
    }.get(state, "Data quality not ready.")


__all__ = [
    "INSUFFICIENT",
    "MIN_EDGE_MARGIN",
    "MIN_PROB_SAMPLE",
    "NO_TRADE",
    "QUALIFIED",
    "Recommendation",
    "RecommendationEngine",
    "WATCH",
]