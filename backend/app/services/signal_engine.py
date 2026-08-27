"""Phase 4 — Validated Signal / Decision Engine.

Turns real-time statistical analysis + real (or clearly-labeled) Deriv proposals into a
disciplined, explainable trading decision pipeline. The signal engine NEVER bypasses
analysis, proposal pricing, or risk gates:

    analysis snapshot -> candidate -> proposal -> probability/EV -> multi-window
        -> significance -> risk gate -> SIGNAL (with explicit state)

Every Signal is a normalized object traceable back to ticks/analysis/proposal/decision.
State transitions are explicit and rule-bound — there is deliberately NO
ANALYSIS -> BUY shortcut. Execution eligibility is a state, never an assumption.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from app.config import settings
from app.services.contracts import ContractSpec
from app.services.proposal_engine import NormalizedProposal
from app.services.probability import BetaConfig, beta_posterior_mean, beta_ci_width


class SignalState(str, Enum):
    NO_SIGNAL = "NO_SIGNAL"
    CANDIDATE = "CANDIDATE"           # analysis+proposal look plausible; not yet validated
    VALIDATING = "VALIDATING"         # running multi-window + significance checks
    QUALIFIED = "QUALIFIED"           # passed statistical + pricing gates (read-only)
    EXECUTION_READY = "EXECUTION_READY"  # risk gate PASS; may be submitted for execution
    EXECUTING = "EXECUTING"           # execution request in flight
    OPEN = "OPEN"                     # contract bought/opened
    WON = "WON"
    LOST = "LOST"
    VOID = "VOID"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"             # failed a hard gate (pricing/risk/multi-window)
    ERROR = "ERROR"

    # Execution sub-states (rolled into error/rejected where ambiguous)
    EXECUTION_UNCERTAIN = "EXECUTION_UNCERTAIN"
    BLOCKED = "BLOCKED"               # execution blocked (kill switch / limits / policy)


class ExecutionState(str, Enum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    ELIGIBLE = "ELIGIBLE"             # risk gate PASS, signal EXECUTION_READY
    REQUESTED = "REQUESTED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"           # broker confirmed purchase
    UNCERTAIN = "UNCERTAIN"           # ambiguous confirmation -> reconcile
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class RiskState(str, Enum):
    NO_SIGNAL = "NO_SIGNAL"
    PASS = "PASS"
    VETO = "VETO"
    NOT_RUN = "NOT_RUN"


# Contract priority for comparing otherwise-valid opportunities.
# Higher = higher priority. Priority is NOT "always trade this"; it only otherwise
# breaks ties between comparable valid candidates.
CONTRACT_PRIORITY = {
    "MATCHES": 6,
    "OVER": 5,
    "UNDER": 4,
    "ODD": 3,
    "EVEN": 2,
    "DIFFERS": 1,
}

# Risk veto reason codes (Phase 4 §10).
VETO_INSUFFICIENT_DATA = "insufficient data"
VETO_STALE_DATA = "stale data"
VETO_CONFLICTING_WINDOWS = "conflicting windows"
VETO_NEGATIVE_EV = "negative EV"
VETO_MISSING_PROPOSAL = "missing proposal"
VETO_INVALID_PRICE = "invalid price"
VETO_INVALID_CONTRACT = "invalid contract"
VETO_INVALID_DURATION = "invalid duration"
VETO_CONNECTION_FAILURE = "connection failure"
VETO_AUTHORIZATION_FAILURE = "authorization failure"
VETO_STAKE_ABOVE_LIMIT = "stake above limit"
VETO_DAILY_LOSS_EXCEEDED = "daily loss exceeded"
VETO_LOSS_STREAK_EXCEEDED = "loss streak exceeded"
VETO_TOO_MANY_OPEN = "too many open trades"
VETO_COOLDOWN = "cooldown active"
VETO_DUPLICATE_SIGNAL = "duplicate signal"
VETO_EXECUTION_LOCK = "execution lock active"
VETO_SIGNAL_EXPIRED = "signal expired"
VETO_KILL_SWITCH = "kill switch active"
VETO_NOT_ENABLED = "execution not enabled"


def contract_priority(family: str) -> int:
    return CONTRACT_PRIORITY.get(family, 0)


def _now_ts() -> float:
    return time.time()


def deterministic_signal_id(spec: ContractSpec, window_tag: str) -> str:
    """Deterministic idempotency identity for the analysis (tie): same symbol+contract+window.

    Two signals built from the SAME analysis window for the SAME contract produce the
    SAME id, which is what duplicate protection keys on at the signal layer.
    """
    base = f"{spec.symbol}|{spec.family}|{spec.barrier}|{window_tag}"
    s = 0x9E3779B9
    for ch in base:
        s = ((s << 5) - s + ord(ch)) & 0xFFFFFFFF
    return f"sig-{s:010x}"


@dataclass
class Signal:
    """Normalized phase-4 signal object. Fully traceable."""

    signal_id: str
    symbol: str
    contract_family: str
    contract_type: str
    barrier: int | None
    prediction: str
    duration_ticks: int
    stake: float
    source: str = ""                          # harness | deriv_live | recorded
    analysis_snapshot_id: str = ""            # deterministic window tag
    proposal_id: str = ""
    proposal_source: str = ""
    ask_price: float | None = None
    potential_payout: float | None = None
    estimated_probability: float | None = None
    probability_method: str = ""              # e.g. "beta-bayesian posterior mean"
    expected_value: float | None = None
    expected_return: float | None = None
    breakeven_probability: float | None = None
    multi_window_state: str = "INSUFFICIENT_DATA"
    statistical_evidence: dict = field(default_factory=dict)
    risk_state: str = RiskState.NOT_RUN.value
    risk_reason: str = ""
    risk_vetos: list = field(default_factory=list)
    execution_state: str = ExecutionState.NOT_ELIGIBLE.value
    signal_state: str = SignalState.NO_SIGNAL.value
    expiry: float = 0.0                       # unix ts after which EXECUTION is BLOCKED
    created_ts: float = field(default_factory=_now_ts)
    reason: str = ""
    warnings: list = field(default_factory=list)
    data_quality: str = "INSUFFICIENT_DATA"
    analysis_snapshot: dict = field(default_factory=dict)  # traceable raw evidence
    proposal: dict = field(default_factory=dict)            # normalized proposal snapshot

    def to_dict(self) -> dict:
        return {
            "signal_id": self.signal_id,
            "timestamp": self.created_ts,
            "symbol": self.symbol,
            "contract_family": self.contract_family,
            "contract_type": self.contract_type,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "duration": self.duration_ticks,
            "stake": self.stake,
            "source": self.source,
            "analysis_snapshot_id": self.analysis_snapshot_id,
            "proposal_id": self.proposal_id,
            "proposal_source": self.proposal_source,
            "ask_price": self.ask_price,
            "potential_payout": self.potential_payout,
            "estimated_probability": self.estimated_probability,
            "probability_method": self.probability_method,
            "expected_value": self.expected_value,
            "expected_return": self.expected_return,
            "breakeven_probability": self.breakeven_probability,
            "multi_window_state": self.multi_window_state,
            "statistical_evidence": self.statistical_evidence,
            "risk_state": self.risk_state,
            "risk_reason": self.risk_reason,
            "risk_vetos": self.risk_vetos,
            "execution_state": self.execution_state,
            "signal_state": self.signal_state,
            "expiry": self.expiry,
            "created_ts": self.created_ts,
            "reason": self.reason,
            "warnings": self.warnings,
            "data_quality": self.data_quality,
        }

    # ---- explicit state helpers (no ANALYSIS -> BUY shortcut) ----------------
    def is_executable(self) -> bool:
        """Execution eligibility is an explicit state reached only after risk PASS."""
        return (
            self.signal_state == SignalState.EXECUTION_READY.value
            and self.execution_state == ExecutionState.ELIGIBLE.value
        )

    def valid_at(self, now: float | None = None) -> bool:
        """A signal that has expired must never be executed."""
        if self.expiry <= 0:
            return False
        if (now if now is not None else _now_ts()) > self.expiry:
            return False
        return True

    def expire(self) -> None:
        self.signal_state = SignalState.EXPIRED.value
        self.execution_state = ExecutionState.BLOCKED.value

    def expire_if_stale(self, now: float | None = None) -> None:
        if self.expiry <= 0:
            return
        if (now if now is not None else _now_ts()) > self.expiry:
            self.expire()


# --------------------------------------------------------------------------
# Signal factory: turns a contract + analysis + proposal into a Signal with
# explicit probability, EV and multi-window verdict. Does NOT run the risk gate.
# --------------------------------------------------------------------------
class SignalEngine:
    """Builds normalized signals from validated inputs (Phase 4 core)."""

    def __init__(self, *, beta_cfg: BetaConfig | None = None) -> None:
        self.beta = beta_cfg or BetaConfig(
            alpha0=settings.signal_beta_alpha0, beta0=settings.signal_beta_beta0
        )

    def _empirical_evidence(self, spec: ContractSpec, wa: dict) -> tuple[int, dict]:
        """Return (successes, evidence) for a contract's win condition over a window."""
        n = wa.get("n", 0)
        counts: list[int] = list(wa.get("counts") or [0] * 10)
        if len(counts) < 10:
            counts = (counts + [0] * 10)[:10]
        d = spec.barrier if spec.barrier is not None else 0
        fam = spec.family
        evidence: dict = {"n": n, "window": wa.get("size", 0)}
        if fam in ("MATCHES", "DIFFERS"):
            c = counts[d] if 0 <= d <= 9 else 0
            successes = c if fam == "MATCHES" else (n - c)
            evidence["matches_count"] = c
            evidence["differs_count"] = n - c
            evidence["digit"] = d
        elif fam in ("ODD", "EVEN"):
            odd = sum(counts[1::2])
            even = n - odd
            successes = odd if fam == "ODD" else even
            evidence["odd_count"] = odd
            evidence["even_count"] = even
        elif fam in ("OVER", "UNDER"):
            over = sum(counts[d + 1:]) if d < 9 else 0
            under = sum(counts[:d]) if d >= 1 else 0
            successes = over if fam == "OVER" else under
            evidence["over_count"] = over
            evidence["under_count"] = under
            evidence["barrier"] = d
        else:
            successes = 0
            evidence["unsupported_family"] = spec.family
        evidence["successes"] = successes
        return successes, evidence

    def build(
        self,
        spec: ContractSpec,
        *,
        window_analysis: dict,
        proposal: NormalizedProposal | None,
        data_quality: dict | None,
        multi_window_state: str,
        snapshot_tag: str,
        source: str = "",
    ) -> Signal:
        """Build a Signal from available inputs WITHOUT running the risk gate.

        If a hard pricing/data gate fails, the signal is still materialized in an honest
        state (REJECTED / NO_SIGNAL) so consumers can see WHY. Probability / EV are only
        computed when pricing fields exist (spec: never use EV without real price data →
        simulated prices are labeled and kept non-qualifying at the risk gate).
        """
        n = window_analysis.get("n", 0) if window_analysis else 0
        dq = (data_quality or {}).get("state", "INSUFFICIENT_DATA") if data_quality else "INSUFFICIENT_DATA"
        successes, evidence = self._empirical_evidence(spec, window_analysis or {})

        sig = Signal(
            signal_id=deterministic_signal_id(spec, snapshot_tag),
            symbol=spec.symbol,
            contract_family=spec.family,
            contract_type=spec.contract_type,
            barrier=spec.barrier,
            prediction=spec.prediction,
            duration_ticks=spec.duration_ticks,
            stake=float(spec.stake),
            source=source or (data_quality or {}).get("source", "") if data_quality else source,
            analysis_snapshot_id=snapshot_tag,
            data_quality=dq,
            multi_window_state=multi_window_state,
            statistical_evidence=evidence,
            analysis_snapshot={
                "n": n,
                "data_quality": dq,
                "counts": list((window_analysis or {}).get("counts") or []),
            },
        )

        # ---- data / sample gates ---------------------------------------------
        if dq != "DATA_READY":
            sig.signal_state = SignalState.REJECTED.value
            sig.reason = f"data_quality={dq}; refusing to build a signal."
            sig.warnings.append("data not ready")
            sig.expiry = _now_ts()  # expired immediately
            sig.execution_state = ExecutionState.NOT_ELIGIBLE.value
            return sig
        if n < settings.signal_min_sample:
            sig.signal_state = SignalState.REJECTED.value
            sig.reason = f"sample n={n} below minimum {settings.signal_min_sample}; insufficient evidence."
            sig.warnings.append("insufficient sample")
            sig.expiry = _now_ts()
            sig.execution_state = ExecutionState.NOT_ELIGIBLE.value
            return sig

        # ---- proposal gates ----------------------------------------------------
        if proposal is None or proposal.state != "OK":
            sig.signal_state = SignalState.REJECTED.value
            sig.reason = proposal.message if proposal else "missing proposal (pricing unavailable)."
            sig.proposal_source = proposal.source if proposal else "UNAVAILABLE"
            sig.warnings.append("proposal unavailable")
            sig.expiry = _now_ts()
            sig.execution_state = ExecutionState.NOT_ELIGIBLE.value
            return sig
        if proposal.ask_price is None or proposal.payout is None or proposal.payout <= 0:
            sig.signal_state = SignalState.REJECTED.value
            sig.reason = "proposal missing/invalid ask_price or payout."
            sig.proposal_source = proposal.source
            sig.warnings.append("invalid price")
            sig.expiry = _now_ts()
            sig.execution_state = ExecutionState.NOT_ELIGIBLE.value
            return sig

        sig.proposal_id = proposal.proposal_id
        sig.proposal_source = proposal.source
        sig.ask_price = proposal.ask_price
        sig.potential_payout = proposal.payout
        sig.breakeven_probability = proposal.breakeven_win_rate

        # ---- probability estimation (Beta/Bayesian, transparent) ---------------
        if successes < 0 or successes > n:
            successes = min(max(successes, 0), n)
        pw = beta_posterior_mean(successes, n, self.beta)
        ci = beta_ci_width(successes, n, self.beta)
        sig.estimated_probability = round(pw, 4)
        sig.probability_method = "beta-bayesian posterior mean"
        evidence["posterior_estimate"] = round(pw, 4)
        evidence["beta_ci_width"] = round(ci, 4)
        evidence["prior"] = {
            "alpha0": self.beta.alpha0,
            "beta0": self.beta.beta0,
            "mean": round(self.beta.prior_mean(), 4),
        }
        evidence["beta_ci_stable"] = ci <= 0.25

        # ---- EV (documented payoff interpretation) -----------------------------
        net_profit = proposal.profit_net if proposal.profit_net is not None else (proposal.payout - float(spec.stake))
        loss = float(spec.stake)
        ev = pw * net_profit - (1 - pw) * loss
        sig.expected_value = round(ev, 4)
        sig.expected_return = round(ev - float(spec.stake), 4)

        # ---- multi-window confirmation (Phase 4 §7) -----------------------------
        sig.expiry = _now_ts() + min(
            settings.signal_max_lifetime_secs, settings.signal_max_proposal_age_secs
        )
        sig.signal_state = SignalState.VALIDATING.value
        sig.reason = (
            f"P(win) (Beta) {pw:.3f} (n={n}) vs payout breakeven "
            f"{sig.breakeven_probability:.3f}; EV {ev:.4f}. Validating multi-window + risk."
        )
        return sig

    def confirm_eligible(self, sig: Signal, *, risk_state: str, risk_reason: str, vetos: list) -> None:
        """Move a VALIDATING signal to EXECUTION_READY only after risk PASS (Phase 4 §10/§12).

        There is deliberately NO path between VALIDATING and execution unless the risk
        gate returns PASS and the signal is not expired.
        """
        if sig.signal_state == SignalState.VALIDATING.value and risk_state == RiskState.PASS.value:
            sig.risk_state = risk_state
            sig.risk_reason = risk_reason
            sig.risk_vetos = []
            sig.signal_state = SignalState.EXECUTION_READY.value
            sig.execution_state = ExecutionState.ELIGIBLE.value
            sig.warnings.append("risk gate PASS")
        elif risk_state == RiskState.VETO.value:
            sig.risk_state = risk_state
            sig.risk_reason = risk_reason or ";".join(vetos)
            sig.risk_vetos = list(vetos)
            sig.signal_state = SignalState.REJECTED.value
            sig.execution_state = ExecutionState.BLOCKED.value
            sig.warnings.append("risk gate VETO")
        sig.expire_if_stale()


_engine = SignalEngine()


def build_signal(
    spec: ContractSpec,
    *,
    window_analysis: dict,
    proposal: NormalizedProposal | None,
    data_quality: dict | None,
    multi_window_state: str,
    snapshot_tag: str,
    source: str = "",
) -> Signal:
    return _engine.build(
        spec,
        window_analysis=window_analysis,
        proposal=proposal,
        data_quality=data_quality,
        multi_window_state=multi_window_state,
        snapshot_tag=snapshot_tag,
        source=source,
    )


if __name__ == "__main__":  # pragma: no cover
    pass


__all__ = [
    "CONTRACT_PRIORITY",
    "Signal",
    "SignalEngine",
    "SignalState",
    "ExecutionState",
    "RiskState",
    "VETO_AUTHORIZATION_FAILURE",
    "VETO_CONFLICTING_WINDOWS",
    "VETO_COOLDOWN",
    "VETO_DAILY_LOSS_EXCEEDED",
    "VETO_DUPLICATE_SIGNAL",
    "VETO_EXECUTION_LOCK",
    "VETO_INSUFFICIENT_DATA",
    "VETO_INVALID_CONTRACT",
    "VETO_INVALID_DURATION",
    "VETO_INVALID_PRICE",
    "VETO_KILL_SWITCH",
    "VETO_LOSS_STREAK_EXCEEDED",
    "VETO_MISSING_PROPOSAL",
    "VETO_NEGATIVE_EV",
    "VETO_NOT_ENABLED",
    "VETO_SIGNAL_EXPIRED",
    "VETO_STAKE_ABOVE_LIMIT",
    "VETO_STALE_DATA",
    "VETO_TOO_MANY_OPEN",
    "build_signal",
    "contract_priority",
    "deterministic_signal_id",
]