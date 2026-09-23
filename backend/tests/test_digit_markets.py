"""Live-tick digit precision and the digit-market board.

Two real defects are pinned here:

1. `Tick.digit` inferred decimal precision from the float, so any quote with a
   trailing zero lost it. JD10 quotes 2dp - a quote of 95382.30 stringifies as
   "95382.3" and read back as digit 3 instead of 0. Every 2dp/4dp market
   (jump, bear/bull) had a biased digit distribution because of it. Deriv
   stamps `pip_size` on each tick and that is authoritative.

2. The configured board advertised 15 digit markets, but 4 of them
   (1HZ150V/1HZ200V/1HZ250V/1HZ300V) have no digit contracts at all - Deriv
   answers OfferingsInvalidSymbol. Deriv exposes exactly 20 digit-capable
   symbols; the board must match that set rather than a guessed superset.
"""
from app.config import get_settings
from app.models.tick import Tick

# Every symbol Deriv lists DIGIT* contracts for, verified live via
# contracts_for on the public endpoint (2026-09-20).
DIGIT_MARKETS = {
    "R_10", "R_25", "R_50", "R_75", "R_100",
    "1HZ10V", "1HZ15V", "1HZ25V", "1HZ30V", "1HZ50V",
    "1HZ75V", "1HZ90V", "1HZ100V",
    "JD10", "JD25", "JD50", "JD75", "JD100",
    "RDBEAR", "RDBULL",
}
# Stream ticks but offer no digit contract - must never be on this board.
NON_DIGIT = {
    "1HZ150V", "1HZ200V", "1HZ250V", "1HZ300V",
    "BOOM1000", "BOOM500", "CRASH1000", "CRASH500",
    "stpRNG", "stpRNG2", "RB100", "RB200",
    "WLDAUD", "WLDEUR", "frxEURUSD", "cryBTCUSD", "OTC_DJI",
}


def _syms():
    return [s.strip() for s in get_settings().deriv_active_symbols.split(",") if s.strip()]


class TestBoardIsEveryDigitMarket:
    def test_board_is_exactly_the_digit_capable_set(self):
        assert set(_syms()) == DIGIT_MARKETS

    def test_no_duplicates(self):
        syms = _syms()
        assert len(syms) == len(set(syms))

    def test_phantom_symbols_are_not_advertised(self):
        """The old board listed these; none can take a digit trade."""
        present = set(_syms()) & NON_DIGIT
        assert present == set(), f"non-digit markets advertised: {sorted(present)}"

    def test_symbols_that_actually_offer_digits_are_present(self):
        """1HZ15V and 1HZ90V stream live and offer digits - they were missing."""
        assert {"1HZ15V", "1HZ90V"} <= set(_syms())

    def test_all_five_market_families_covered(self):
        syms = set(_syms())
        assert {"R_10", "R_50", "R_100"} <= syms          # random index
        assert {"1HZ10V", "1HZ100V"} <= syms              # 1-second volatility
        assert {"JD10", "JD100"} <= syms                  # jump index
        assert {"RDBEAR", "RDBULL"} <= syms               # daily reset


class TestDigitUsesPipSize:
    def test_trailing_zero_is_preserved_on_2dp(self):
        """95382.30 on a 2dp market is digit 0, not 3."""
        t = Tick(symbol="JD10", quote=95382.30, raw={"tick": {"pip_size": 2}})
        assert t.digit == 0

    def test_nonzero_last_digit_on_2dp(self):
        t = Tick(symbol="JD10", quote=95382.35, raw={"tick": {"pip_size": 2}})
        assert t.digit == 5

    def test_trailing_zero_on_4dp(self):
        t = Tick(symbol="RDBEAR", quote=1012.5120, raw={"tick": {"pip_size": 4}})
        assert t.digit == 0

    def test_trailing_zero_on_3dp(self):
        t = Tick(symbol="1HZ15V", quote=13494.780, raw={"tick": {"pip_size": 3}})
        assert t.digit == 0

    def test_accepts_unwrapped_raw_shape(self):
        """raw may be the tick dict itself, not wrapped in {"tick": ...}."""
        t = Tick(symbol="JD10", quote=95382.30, raw={"pip_size": 2})
        assert t.digit == 0

    def test_stamped_digit_still_wins(self):
        """The demo feed's explicit digit remains authoritative."""
        t = Tick(symbol="X", quote=1.0, raw={"digit": 7, "tick": {"pip_size": 2}})
        assert t.digit == 7

    def test_falls_back_when_pip_size_absent(self):
        """Legacy ticks with no pip_size keep the old inference."""
        t = Tick(symbol="R_100", quote=605.1234)
        assert t.digit == 4

    def test_pip_size_zero_is_treated_as_absent(self):
        """A falsy pip_size must not crash - fall back to inference."""
        t = Tick(symbol="X", quote=605.1234, raw={"tick": {"pip_size": 0}})
        assert t.digit == 4

    def test_real_live_payload_round_trip(self):
        """The exact shape Deriv sent for JD10, trailing zero included."""
        payload = {"tick": {"quote": 95382.30, "pip_size": 2, "symbol": "JD10"}}
        t = Tick(symbol="JD10", quote=95382.30, raw=payload)
        assert t.digit == 0
        assert t.to_dict()["digit"] == 0
