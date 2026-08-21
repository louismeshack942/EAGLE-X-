import asyncio

import pytest

from app.services.demo_generator import DemoGenerator
from app.core.queue import tick_queue


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
