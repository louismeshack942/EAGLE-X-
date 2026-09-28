"""The scan must not claim a window it did not measure.

Live incident: the scan answered "20 markets with live tape (window 250)"
while the tape actually held 63 ticks (R_*) and 125 ticks (1HZ*/JD*). The
window is a PROMISE; printing 250 when only 63 ticks exist is a lie about the
evidence, and every probability on the board inherits it.

The tape was also thin by exactly the amounts above because the feed had only
just reconnected - which is precisely when an honest platform must say so.
"""
import re
from datetime import datetime, timezone

import pytest

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.mission import DEFAULT_WINDOW, MissionPlanner
from app.services.tick_recorder import tick_recorder


def _push(symbol, n, provider="deriv_live"):
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for i in range(n):
        d = (7, 8, 9, 0, 1, 2, 3, 4, 5, 6)[i % 10]
        tick_queue.push(Tick(
            symbol=symbol, quote=float(d), raw={"digit": d},
            provider=provider, timestamp=datetime.now(timezone.utc)))


class TestWindowHonesty:
    def test_scan_does_not_claim_more_tape_than_exists(self):
        _push("W_THIN", 63)
        plan = MissionPlanner().scan_report(
            "scan all markets over 3 entry point 5 runs", ["W_THIN"])
        assert plan["n"] == 63, plan.get("n")
        assert plan["window_requested"] == DEFAULT_WINDOW
        assert plan["window_effective"] == 63
        assert plan["sample_complete"] is False

    def test_answer_never_prints_a_window_larger_than_the_tape(self):
        _push("W_THIN", 63)
        plan = MissionPlanner().scan_report(
            "scan all markets over 3 entry point 5 runs", ["W_THIN"])
        answer = plan["answer"]
        # No "window <n>" claim above the real depth.
        for claimed in re.findall(r"window (\d+)", answer):
            assert int(claimed) <= 63, answer

    def test_answer_states_the_real_depth_and_flags_it(self):
        _push("W_THIN", 63)
        plan = MissionPlanner().scan_report(
            "scan all markets over 3 entry point 5 runs", ["W_THIN"])
        assert "63" in plan["answer"]
        low = plan["answer"].lower()
        assert ("thin" in low or "warming" in low or "only" in low), plan["answer"]

    def test_full_tape_reports_the_requested_window_unchanged(self):
        _push("W_FULL", 300)
        plan = MissionPlanner().scan_report(
            "scan all markets over 3 entry point 5 runs", ["W_FULL"],
            window=250)
        assert plan["window_effective"] == 250
        assert plan["sample_complete"] is True
        assert "thin" not in plan["answer"].lower()

    def test_deep_and_thin_markets_mixed(self):
        """The image the owner saw: some markets deep, some thin."""
        _push("THIN", 63)
        _push("DEEP", 300)
        plan = MissionPlanner().scan_report(
            "scan all markets over 3 entry point 5 runs", ["THIN", "DEEP"],
            window=250)
        assert plan["sample_complete"] is False
        by = {m["symbol"]: m for m in plan["markets"]}
        assert by["THIN"]["n"] == 63
        assert by["DEEP"]["n"] == 250
        # Every market is still listed - the board is the answer.
        assert set(by) == {"THIN", "DEEP"}
