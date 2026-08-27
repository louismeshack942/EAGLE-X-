"""Harness provider: must tag every tick harness and never claim to be live."""


import pytest

from app.services.harness import HarnessProvider


@pytest.mark.asyncio
async def test_harness_ticks_are_tagged_harness():
    h = HarnessProvider(interval_ms=10)
    await h.connect("R_10")
    gen = h.ticks()
    first = await anext(gen)  # noqa: F821
    await gen.aclose()
    await h.close()
    assert first is not None
    assert first.provider == "harness"
    assert 0 <= first.last_digit <= 9
    assert first.symbol == "R_10"


@pytest.mark.asyncio
async def test_harness_close_ends_stream():
    h = HarnessProvider(interval_ms=2)
    await h.connect("R_25")
    assert h.state == "connected"
    await h.close()
    assert h.state == "disconnected"