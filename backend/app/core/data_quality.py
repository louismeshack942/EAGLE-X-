"""Data-quality states for analysis results.

Canonical, honest states. Analysis never fabricates a QUALIFIED recommendation from
insufficient/stale/invalid data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Minimum sample size before any statistical claim is attempted.
MIN_SAMPLE = 25
# A window is 'complete' once it holds >= this fraction of its requested size.
COMPLETE_FRACTION = 1.0
# Freshness floor: if the newest tick is older than this many seconds, data is STALE.
STALE_AFTER_SECONDS = 15.0


class DataQualityState(str, Enum):
    DATA_READY = "DATA_READY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    INVALID = "INVALID"


@dataclass
class DataQuality:
    state: DataQualityState = DataQualityState.INSUFFICIENT_DATA
    sample_size: int = 0
    window_complete: bool = False
    window_size: int = 0
    newest_epoch_ms: int = 0
    now_ms: int = 0
    age_seconds: float | None = None
    duplicate_ticks: int = 0
    invalid_ticks: int = 0
    source: str = ""  # harness | deriv_live | recorded
    connection_state: str = ""  # connected | disconnected | ...

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "sample_size": self.sample_size,
            "window_complete": self.window_complete,
            "window_size": self.window_size,
            "newest_epoch_ms": self.newest_epoch_ms,
            "now_ms": self.now_ms,
            "age_seconds": self.age_seconds,
            "duplicate_ticks": self.duplicate_ticks,
            "invalid_ticks": self.invalid_ticks,
            "source": self.source,
            "connection_state": self.connection_state,
        }


def assess_data_quality(
    *,
    n: int,
    window_size: int,
    newest_epoch_ms: int,
    now_ms: int,
    duplicate_ticks: int,
    invalid_ticks: int,
    source: str,
    connection_state: str,
) -> DataQuality:
    """Compute the data-quality state for a window. Deterministic and honest.

    Priority:
      1. INVALID          when invalid ticks dominate or the source is unknown/empty.
      2. DISCONNECTED     when the feed is not 'connected'.
      3. STALE            when we have data but it is old.
      4. INSUFFICIENT     when sample is below MIN_SAMPLE.
      5. DATA_READY       otherwise.
    """
    q = DataQuality(
        sample_size=n,
        window_size=window_size,
        window_complete=n >= int(window_size * COMPLETE_FRACTION),
        newest_epoch_ms=newest_epoch_ms,
        now_ms=now_ms,
        duplicate_ticks=duplicate_ticks,
        invalid_ticks=invalid_ticks,
        source=source,
        connection_state=connection_state,
    )
    if now_ms and newest_epoch_ms:
        q.age_seconds = round((now_ms - newest_epoch_ms) / 1000.0, 2)

    if invalid_ticks > n and n == 0 and source not in ("harness", "deriv_live", "recorded"):
        q.state = DataQualityState.INVALID
        return q

    disconnected = connection_state not in ("connected", "")
    if disconnected and n == 0:
        q.state = DataQualityState.DISCONNECTED
        return q

    if n >= MIN_SAMPLE and q.age_seconds is not None and q.age_seconds > STALE_AFTER_SECONDS:
        q.state = DataQualityState.STALE
        return q

    if n < MIN_SAMPLE:
        q.state = DataQualityState.INSUFFICIENT_DATA
        return q

    q.state = DataQualityState.DATA_READY
    return q


__all__ = [
    "COMPLETE_FRACTION",
    "DataQuality",
    "DataQualityState",
    "MIN_SAMPLE",
    "STALE_AFTER_SECONDS",
    "assess_data_quality",
]