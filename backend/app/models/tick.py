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
        """Last digit of the quote (final digit of the price string)."""
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
