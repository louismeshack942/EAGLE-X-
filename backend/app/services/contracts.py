"""Deriv digit-contract definitions — metadata only (no pricing).

Contract semantics align with Deriv's public digit option definitions captured in
Phase 0 (docs/protrader/CONTRACTS.md) and re-verified against the current official API:

    contract_type          DIGITMATCH / DIGITDIFF / DIGITEVEN / DIGITODD /
                           DIGITOVER / DIGITUNDER
    duration               <= 10 ticks for digit contracts
    barrier                the predicted digit 0..9 for MATCHES/DIFFERS/OVER/UNDER

EAGLE-X stores metadata here; live pricing comes from Deriv. We never fabricate a
payout — see proposal_engine.py / pricing.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.analytics import UNIFORM_P

MAX_DIGIT_DURATION_TICKS = 10

# Barrier digit range for barriers-bearing contracts.
BARRIER_MIN = 0
BARRIER_MAX = 9

# Duration unit for digit contracts is 't' (ticks) on Deriv.
DURATION_UNIT = "t"


@dataclass(frozen=True)
class ContractFamily:
    key: str  # MATCHES / DIFFERS / ODD / EVEN / OVER / UNDER
    contract_type: str  # Deriv contract_type code
    requires_barrier: bool
    requires_digit_focus: bool  # whether EAGLE-X picks a predicted digit
    fair_win_rate: float  # baseline (uniform digits), 0..1
    description: str
    # For ODD/EVEN the prediction is a family, not a digit. For OVER/UNDER the
    # barrier is the digit threshold. For MATCHES/DIFFERS it is the predicted digit.
    barrier_kind: str = "digit"  # digit | none | parity

    @property
    def is_digit_contract(self) -> bool:
        return True


FAMILIES: dict[str, ContractFamily] = {
    "MATCHES": ContractFamily(
        key="MATCHES", contract_type="DIGITMATCH",
        requires_barrier=True, requires_digit_focus=True,
        fair_win_rate=UNIFORM_P, description="Last digit equals the predicted digit.",
    ),
    "DIFFERS": ContractFamily(
        key="DIFFERS", contract_type="DIGITDIFF",
        requires_barrier=True, requires_digit_focus=True,
        fair_win_rate=1.0 - UNIFORM_P,
        description="Last digit is not the predicted digit.",
    ),
    "ODD": ContractFamily(
        key="ODD", contract_type="DIGITODD",
        requires_barrier=False, requires_digit_focus=False,
        fair_win_rate=0.5, description="Last digit is odd (1,3,5,7,9).",
        barrier_kind="parity",
    ),
    "EVEN": ContractFamily(
        key="EVEN", contract_type="DIGITEVEN",
        requires_barrier=False, requires_digit_focus=False,
        fair_win_rate=0.5, description="Last digit is even (0,2,4,6,8).",
        barrier_kind="parity",
    ),
    "OVER": ContractFamily(
        key="OVER", contract_type="DIGITOVER",
        requires_barrier=True, requires_digit_focus=False,
        fair_win_rate=0.0,  # depends on barrier: (9-barrier)/10
        description="Last digit is greater than the barrier.",
    ),
    "UNDER": ContractFamily(
        key="UNDER", contract_type="DIGITUNDER",
        requires_barrier=True, requires_digit_focus=False,
        fair_win_rate=0.0,  # depends on barrier: barrier/10
        description="Last digit is less than the barrier.",
    ),
}

SUPPORTED_FAMILIES = ("MATCHES", "DIFFERS", "ODD", "EVEN", "OVER", "UNDER")
SUPPORTED_CONTRACT_TYPES = set(f.contract_type for f in FAMILIES.values())

DEFAULT_DURATION_TICKS = 5
DEFAULT_STAKE = 1.0
DEFAULT_CURRENCY = "USD"


@dataclass
class ContractSpec:
    """A fully-specified, evaluable digit contract candidate (read-only)."""

    symbol: str
    family: str  # MATCHES/DIFFERS/ODD/EVEN/OVER/UNDER
    contract_type: str
    barrier: int | None = None  # 0..9 for barriers-bearing families
    prediction: str = ""  # human label: digit "3", "ODD", "OVER barrier 4"
    duration_ticks: int = DEFAULT_DURATION_TICKS
    duration_unit: str = DURATION_UNIT  # 't' (ticks)
    stake: float = DEFAULT_STAKE
    currency: str = DEFAULT_CURRENCY
    basis: str = "stake"

    def __post_init__(self) -> None:
        fam = FAMILIES.get(self.family)
        if not fam:
            raise ValueError(f"unknown family {self.family!r}")
        if fam.contract_type != self.contract_type:
            raise ValueError(
                f"family {self.family} requires contract_type {fam.contract_type}, got {self.contract_type}"
            )
        if not 1 <= self.duration_ticks <= MAX_DIGIT_DURATION_TICKS:
            raise ValueError("digit contracts require duration 1..10 ticks")
        if self.duration_unit != DURATION_UNIT:
            raise ValueError("digit contracts use tick duration")
        if self.stake is None or float(self.stake) <= 0:
            raise ValueError("stake must be > 0")
        if fam.requires_barrier:
            if self.barrier is None or not (BARRIER_MIN <= int(self.barrier) <= BARRIER_MAX):
                raise ValueError("barrier must be an int in 0..9 for this family")
        else:
            self.barrier = None
        self.prediction = _prediction_label(self)

    def fair_win_rate(self) -> float:
        fam = FAMILIES[self.family]
        if fam.fair_win_rate > 0:
            return fam.fair_win_rate
        barrier = self.barrier or 0
        return (9 - barrier) / 10.0 if self.family == "OVER" else barrier / 10.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "family": self.family,
            "contract_type": self.contract_type,
            "barrier": self.barrier,
            "prediction": self.prediction,
            "duration_ticks": self.duration_ticks,
            "duration_unit": self.duration_unit,
            "stake": self.stake,
            "currency": self.currency,
            "basis": self.basis,
            "fair_win_rate": round(self.fair_win_rate(), 4),
        }


def _prediction_label(spec: ContractSpec) -> str:
    fam = spec.family
    if fam in ("MATCHES", "DIFFERS"):
        return f"digit {spec.barrier}"
    if fam == "ODD":
        return "ODD"
    if fam == "EVEN":
        return "EVEN"
    if fam == "OVER":
        return f"OVER barrier {spec.barrier}"
    if fam == "UNDER":
        return f"UNDER barrier {spec.barrier}"
    return spec.family


def build_spec(
    symbol: str,
    family: str,
    barrier: int | None = None,
    duration_ticks: int = DEFAULT_DURATION_TICKS,
    stake: float = DEFAULT_STAKE,
    currency: str = DEFAULT_CURRENCY,
) -> ContractSpec:
    fam = FAMILIES[family]
    return ContractSpec(
        symbol=symbol,
        family=family,
        contract_type=fam.contract_type,
        barrier=barrier,
        duration_ticks=duration_ticks,
        stake=stake,
        currency=currency,
    )


def all_specs_for_symbol(
    symbol: str,
    duration_ticks: int = DEFAULT_DURATION_TICKS,
    stake: float = DEFAULT_STAKE,
    currency: str = DEFAULT_CURRENCY,
    barriers: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
) -> list[ContractSpec]:
    """The candidate board for a symbol: all 6 families across relevant barriers.

    For MATCHES/DIFFERS/OVER/UNDER a barrier is required; ODD/EVEN need none. This is
    the "contract board" the scanner iterates.
    """
    specs: list[ContractSpec] = []
    for family in SUPPORTED_FAMILIES:
        fam = FAMILIES[family]
        if fam.requires_barrier:
            for b in barriers:
                specs.append(
                    build_spec(symbol, family, barrier=b, duration_ticks=duration_ticks,
                               stake=stake, currency=currency)
                )
        else:
            specs.append(
                build_spec(symbol, family, barrier=None, duration_ticks=duration_ticks,
                           stake=stake, currency=currency)
            )
    return specs


__all__ = [
    "BARRIER_MAX",
    "BARRIER_MIN",
    "DEFAULT_CURRENCY",
    "DEFAULT_DURATION_TICKS",
    "DEFAULT_STAKE",
    "DURATION_UNIT",
    "FAMILIES",
    "MAX_DIGIT_DURATION_TICKS",
    "SUPPORTED_CONTRACT_TYPES",
    "SUPPORTED_FAMILIES",
    "ContractFamily",
    "ContractSpec",
    "all_specs_for_symbol",
    "build_spec",
]