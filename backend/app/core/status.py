"""Connection / data status model shared by backend and frontend.

Canonical user-facing states (no silent failures):
"""

from __future__ import annotations

from enum import Enum


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    AUTH_REQUIRED = "auth_required"
    MARKET_UNAVAILABLE = "market_unavailable"


class DataState(str, Enum):
    LIVE = "live"
    STALE = "stale"
    EMPTY = "empty"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


__all__ = ["ConnectionState", "DataState"]