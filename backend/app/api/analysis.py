"""Phase 2 analysis API — real statistical digit analysis over tick windows.

Endpoints follow the Phase 2 spec (§14) with sub-resource paths for the specific
panels. All responses carry an explicit `source` and per-window data-quality state.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.analysis_engine import OVER_UNDER_BARRIERS, analysis_manager
from app.services.window_engine import DEFAULT_WINDOWS

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _valid_window(window: int | None) -> int | None:
    if window is None:
        return None
    if window not in DEFAULT_WINDOWS:
        raise HTTPException(status_code=400, detail=f"window must be one of {DEFAULT_WINDOWS}")
    return window


@router.get("/{symbol}")
def analysis(symbol: str, window: int | None = None):
    w = _valid_window(window)
    return analysis_manager.snapshot(symbol, window=w)


@router.get("/{symbol}/digits")
def analysis_digits(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "connection_state": snap["connection_state"],
        "window": win.get("size") if win else None,
        **win.get("digit_frequency", {}),
    }


@router.get("/{symbol}/gaps")
def analysis_gaps(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "window": win.get("size") if win else None,
        "gaps": win.get("gaps", {}),
    }


@router.get("/{symbol}/streaks")
def analysis_streaks(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "window": win.get("size") if win else None,
        **win.get("streaks", {}),
    }


@router.get("/{symbol}/quality")
def analysis_quality(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "connection_state": snap["connection_state"],
        "window": win.get("size") if win else None,
        "data_quality": win.get("data_quality", {}),
    }


@router.get("/{symbol}/windows")
def analysis_windows(symbol: str):
    snap = analysis_manager.snapshot(symbol)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "multi_window": snap["multi_window"],
        "windows": [
            {
                "size": k,
                "n": v["n"],
                "data_quality": v["data_quality"]["state"],
                "most_frequent": v.get("digit_frequency", {}).get("most_frequent", -1),
            }
            for k, v in snap.get("windows", {}).items()
        ],
    }


@router.get("/{symbol}/parity")
def analysis_parity(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "window": win.get("size") if win else None,
        **win.get("parity", {}),
    }


@router.get("/{symbol}/over-under")
def analysis_over_under(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "window": win.get("size") if win else None,
        "barriers": OVER_UNDER_BARRIERS,
        "results": win.get("over_under", {}),
    }


@router.get("/{symbol}/matches-differs")
def analysis_matches_differs(symbol: str, window: int | None = None):
    w = _valid_window(window)
    snap = analysis_manager.snapshot(symbol, window=w)
    win = _pick_window(snap, w)
    return {
        "symbol": symbol,
        "source": snap["source"],
        "window": win.get("size") if win else None,
        **win.get("matches_differs", {}),
    }


def _pick_window(snap: dict, w: int | None) -> dict:
    """Select the analysis window dict for a top-level statistic response."""
    windows = snap.get("windows", {})
    if w is not None:
        return windows.get(w, {})
    # default: use the largest available window with data, else the 100 window
    if windows:
        picked = next((windows[s] for s in sorted(windows, reverse=True)
                       if windows[s].get("n", 0) > 0), windows.get(100, {}))
        return picked
    return {}