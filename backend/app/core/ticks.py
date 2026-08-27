"""Normalized tick model + last-digit extraction.

Transparent, deterministic definitions used across EAGLE-X:

- tick.quote:      a float price (numeric)
- tick.epoch_ms:   integer millisecond timestamp
- last_digit(q):   integer in 0..9 computed from the *decimal digits of the quote*.
  For prices that are integers (common on synthetic indices), the last digit is the
  trailing integer digit once the quote is rendered at its natural precision.

We define precision explicitly so behavior is deterministic and testable:
1. Format the float using a fixed precision (spec default 6 significant decimals),
   then strip, then take the last character - digit.
"""

from __future__ import annotations

from dataclasses import dataclass

DIGITS = list(range(10))

# Natural rendering precision used to derive the last digit from a quote.
QUOTE_DECIMALS = 6


def _render_for_digit(value: float, precision: int = QUOTE_DECIMALS) -> str:
    s = f"{value:.{precision}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-", "-0"):
        return "0"
    return s


def last_digit_from_quote(value: float, precision: int = QUOTE_DECIMALS) -> int:
    """Return the last digit (0-9) of the numeric representation of `value`.

    -1 is never returned for a valid finite number; -1 signals "no digit".
    """
    if value is None or value != value:  # NaN
        return -1
    s = _render_for_digit(float(value), precision)
    # handle sign so "-123" -> trailing "3"
    last_char = s[-1]
    if last_char.isdigit():
        return int(last_char)
    return -1


def last_digits_from_quotes(values, precision: int = QUOTE_DECIMALS) -> list[int]:
    return [last_digit_from_quote(v, precision) for v in values]


@dataclass
class NormalizedTick:
    """A canonical, validated tick after normalization."""

    symbol: str
    epoch_ms: int
    quote: float
    last_digit: int = -1
    provider: str = "deriv_live"  # deriv_live | harness | demo

    def __post_init__(self) -> None:
        if self.last_digit < 0 or self.last_digit > 9:
            self.last_digit = last_digit_from_quote(self.quote)


def normalize_tick(
    *,
    symbol: str,
    epoch_ms: int,
    quote: float,
    provider: str = "deriv_live",
) -> NormalizedTick:
    """Build a NormalizedTick with strict input validation."""
    if not symbol or not isinstance(symbol, str):
        raise ValueError("symbol must be a non-empty string")
    if epoch_ms is None or int(epoch_ms) <= 0:
        raise ValueError(f"epoch_ms must be a positive integer, got {epoch_ms!r}")
    if quote is None or not isinstance(quote, (int, float)):
        raise ValueError(f"quote must be numeric, got {quote!r}")
    q = float(quote)
    if q != q or q in (float("inf"), float("-inf")):
        raise ValueError(f"quote must be finite, got {quote!r}")
    return NormalizedTick(
        symbol=symbol,
        epoch_ms=int(epoch_ms),
        quote=q,
        last_digit=last_digit_from_quote(q),
        provider=provider,
    )


__all__ = ["NormalizedTick", "normalize_tick", "last_digit_from_quote", "last_digits_from_quotes", "DIGITS"]