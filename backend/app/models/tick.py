"""Tick data model — the atomic unit of EAGLE-X market data."""
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Tick(BaseModel):
    symbol: str
    quote: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: Literal["deriv_live", "demo"] = "demo"
    quality: int = 100
    raw: Optional[dict] = None

    @property
    def digit(self) -> int:
        """Last digit of the quote at its own decimal precision, matching how
        Deriv digit contracts read the final displayed digit. Sources may
        carry an authoritative digit in raw["digit"] when their quote is a
        rounded float whose trailing zeros cannot be recovered (demo feed).

        Deriv stamps every live tick with `pip_size` — the contract's decimal
        count — and it is authoritative. Inferring precision from the float
        instead loses trailing zeros: JD10 quotes 2dp, so a quote of 95382.30
        stringifies as "95382.3" and a last-digit read would report 3, not 0.
        That silently biases every digit distribution on 2dp and 4dp markets.
        """
        if self.raw and self.raw.get("digit") is not None:
            return int(self.raw["digit"])
        pip_size = None
        if self.raw:
            tick = self.raw.get("tick") or self.raw
            pip_size = tick.get("pip_size") if isinstance(tick, dict) else None
        if pip_size:
            frac = f"{self.quote:.{int(pip_size)}f}".split(".")[1]
            return int(frac[-1])
        s = f"{self.quote:.10f}".rstrip("0")
        if "." in s:
            frac = s.split(".")[1]
            return int(frac[-1]) if frac else 0
        return int(s[-1])

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "quote": self.quote,
            "timestamp": self.timestamp.isoformat(),
            "provider": self.provider,
            "quality": self.quality,
            "digit": self.digit,
        }
