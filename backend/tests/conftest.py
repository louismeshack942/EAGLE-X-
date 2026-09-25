import asyncio
import os
import tempfile
from pathlib import Path

import pytest

# Point the journal at a throwaway file BEFORE app modules import it. Tests
# that add entries (persistence, bottom_up, super_profit, eagle, truth_engine)
# used to append to backend/data/store.json — the production journal that the
# scorecard and the CF's adaptive z-threshold read. That contamination is what
# made a 66.67% win rate out of nothing but test fixtures.
_STORE = Path(tempfile.mkdtemp(prefix="eaglex-test-")) / "store.json"
os.environ.setdefault("EAGLEX_STORE_PATH", str(_STORE))

# Guard: tests must never be able to write the production store. The chat
# persists conversations through that same store, and eight CHATX turns from an
# earlier run were found in backend/data/store.json and rendered in the live UI.
# The env var above is what prevents it, so it is asserted in
# test_isolation.py rather than trusted.
# A blanket block on "chat_history_default" writes was tried and rejected: it
# broke every legitimate default-conversation test. Isolation is the right
# layer, not a per-key veto.

# Analytics filters out synthetic ticks by default (a generated digit must
# never reach Market Master or the Truth Engine). Tests build their own tapes
# from bare `Tick`s, which default to provider "demo", so they opt back in
# explicitly. Production never sets this.
os.environ.setdefault("EAGLEX_ALLOW_SYNTHETIC", "1")

from app.services.demo_generator import DemoGenerator  # noqa: E402
from app.core.queue import tick_queue  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def warm_ticks():
    """Fill the tick queue deterministically for all session tests."""
    demo = DemoGenerator(interval_ms=1)

    async def fill():
        n = 0
        async for tick in demo.stream("R_100"):
            tick_queue.push(tick)
            n += 1
            if n >= 500:
                break

    asyncio.run(fill())
