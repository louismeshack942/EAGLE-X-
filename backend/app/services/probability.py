"""Transparent probability estimation for digit contracts.

We ESTIMATE P(win) for a candidate — we never claim certainty and never use raw
frequency as an unquestionable probability. The default estimator is a deterministic
Beta/Bayesian posterior with a conservative uniform prior:

    prior   Beta(alpha0, beta0),   alpha0 = beta0 = 1  (uniform / Jeffreys-ish on p)
    data    s successes in n observations for the contract's win condition
    posterior Beta(alpha0 + s, beta0 + (n - s))
    estimate  E[p] = (alpha0 + s) / (alpha0 + beta0 + n)   (posterior mean)

When n == 0 there is no evidence, so we return the fair prior mean (0.5 for ODD/EVEN
and equal-win contracts; matched per-family fair rate is supplied by the caller via
`fair_prior`). Effect choices below are explicitly configurable through
`BetaProbabilityConfig` (see config.py) rather than magic constants.

The method is deterministic given the same (s, n, prior). No claim is made that the
estimated probability predicts the NEXT tick with certainty — it is a shrinkage
estimate of the underlying win propensity given evidence plus prior.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BetaConfig:
    alpha0: float = 1.0
    beta0: float = 1.0

    def prior_mean(self) -> float:
        """The prior's expected value (used when there is no data)."""
        a, b = self.alpha0, self.beta0
        if a + b <= 0:
            return 0.5
        return a / (a + b)


def beta_posterior_mean(successes: int, n: int, cfg: BetaConfig) -> float:
    """Posterior mean of the Beta-Binomial model: (alpha0 + s) / (alpha0+beta0+n).

    Deterministic. Requires successes in [0, n].
    """
    if n < 0 or not (0 <= successes <= n):
        raise ValueError(f"invalid (successes={successes}, n={n})")
    a = cfg.alpha0 + successes
    b = cfg.beta0 + (n - successes)
    if a + b <= 0:
        return 0.5
    return a / (a + b)


def beta_ci_width(successes: int, n: int, cfg: BetaConfig) -> float:
    """A conservative measure of estimation uncertainty (posterior std approx).

    Uses the Beta variance formula var = ab / ((a+b)^2 (a+b+1)) with the posterior
    parameters; returns the standard deviation, clipped to [0, 1]. A small n or a
    one-sided observation inflates this, which naturally widens the confidence band —
    the same signal that drives `NEEDS_MORE_SAMPLE` and conservatively smaller EV.
    """
    if n < 0 or not (0 <= successes <= n):
        raise ValueError(f"invalid (successes={successes}, n={n})")
    a = cfg.alpha0 + successes
    b = cfg.beta0 + (n - successes)
    s = a + b
    if s <= 2:
        return 0.5
    var = (a * b) / ((s ** 2) * (s + 1))
    return min(1.0, max(0.0, var ** 0.5))


def beta_hdi_lower(successes: int, n: int, cfg: BetaConfig, mass: float = 0.95) -> float:
    """Pessimistic lower bound: the mass-quantile of the Beta posterior.

    Because finding a closed-form HDI for a general Beta is awkward, we use the
    conservative *lower* tail quantile at (1-mass)/2 via the regularized incomplete
    beta computed with Newton on the CDF. This gives a defensible "we are 95% sure the
    true win rate is at least ~X" style limit used by the risk gate. Deterministic.
    """
    a = cfg.alpha0 + successes
    b = cfg.beta0 + (n - successes)

    def cdf(p: float) -> float:
        return _betainc_reg(a, b, p)

    lo, hi = 0.0, 1.0
    for _ in range(120):  # bisection is deterministic and robust
        mid = (lo + hi) / 2.0
        if cdf(mid) >= (1.0 - mass) / 2.0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a,b) via continued-fraction (Lentz)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    import math

    bt = math.exp(_lbeta(a, b) + a * math.log(x) + b * math.log(1.0 - x))
    if bt <= 0:
        return 0.0
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _beta_cont_frac(a, b, x) / a
    return 1.0 - bt * _beta_cont_frac(b, a, 1.0 - x) / b


def _lbeta(a: float, b: float) -> float:
    import math

    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def _beta_cont_frac(a: float, b: float, x: float, itermax: int = 300) -> float:
    """Lentz continued fraction for the incomplete beta. Deterministic."""
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itermax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        if abs(d * c - 1.0) < 1e-12:
            break
    return h


__all__ = [
    "BetaConfig",
    "beta_ci_width",
    "beta_hdi_lower",
    "beta_posterior_mean",
]