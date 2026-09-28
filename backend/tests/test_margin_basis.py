"""A reported margin must be on the same basis as the decision it explains.

Live incident (UNDER 7 scan). The copilot said, in one breath:

    Nothing cleared its own breakeven by a real margin, so there is no edge
    to take. Best reading was 67.8% confidence at +7.0pp vs breakeven.

Those are two DIFFERENT quantities glued together:

    67.8%  = the Wilson LOWER BOUND (called "confidence")
    +7.0pp = computed from the RAW rate (77.0 - 70.0)

On the confidence basis that same row is 67.8 - 70.0 = MINUS 2.2pp. The
sentence therefore described a sub-breakeven reading as being above breakeven.
The gate decides on the Wilson bound, so the board and the summary must report
the margin on that basis - and label the raw rate separately, so the two can
never be read as each other. This is the same rule as the phantom-edge class
of bug: the number shown must be the number that decided.
"""
from datetime import datetime, timezone

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services.cockpit import MIN_EDGE_PP
from app.services.mission import MissionPlanner
from app.services.tick_recorder import tick_recorder


def _push(symbol, digits):
    tick_queue.clear(symbol)
    tick_recorder.purge(symbol)
    for d in digits:
        tick_queue.push(Tick(
            symbol=symbol, quote=float(d), raw={"digit": int(d)},
            provider="deriv_live", timestamp=datetime.now(timezone.utc)))


# 77% below 7 -> raw UNDER 7 is 77.0% vs 70.0% breakeven (+7.0pp raw), but the
# Wilson lower bound at this depth is BELOW breakeven. The exact live case.
_STRONG_RAW_WEAK_LB = ["3"] * 77 + ["8"] * 23


class TestMarginBasis:
    def _plan(self, digits=None, **kw):
        _push("B_1HZ75V", digits or _STRONG_RAW_WEAK_LB)
        return MissionPlanner().plan(
            "scan all markets for under 7 and best entry point",
            ["B_1HZ75V"], predictions=[{"side": "UNDER", "barrier": 7}], **kw)

    def test_verdict_is_no_edge_when_the_bound_misses(self):
        """The gate is right - the bound is below breakeven, so no edge."""
        plan = self._plan()
        assert plan["verdict"] == "NO_EDGE_FOUND", plan["verdict"]

    def test_best_reading_does_not_pair_a_bound_with_a_raw_margin(self):
        """The exact contradiction: 'X% confidence at +Ypp' where X < breakeven
        and Y > 0. If confidence is below breakeven the stated margin must not
        be positive."""
        plan = self._plan()
        answer = plan["answer"]
        best = plan["best_entry"]
        margin = round(best["confidence"] - best["breakeven_pct"], 1)
        assert margin < 0, "fixture must be sub-breakeven on the bound"
        # The raw +7.0pp must never appear as the headline margin.
        assert "+7.0pp" not in answer, answer
        # Every "X% confidence ... (+Ypp)" pair in the answer must have Y < 0,
        # i.e. no positive margin shown beside its own confidence.
        import re
        for conf, sign, mag in re.findall(
                r"(\d+\.\d+)% confidence vs \d+\.\d+% breakeven "
                r"\(([+-])(\d+\.\d+)pp\)", answer):
            assert sign == "-", (
                "a confidence bound below breakeven was paired with a positive "
                "margin: " + answer)

    def test_board_reports_the_deciding_margin_not_the_raw_one(self):
        """Every board line must carry the basis the verdict used, so the board
        cannot appear to contradict the verdict."""
        plan = self._plan()
        assert plan["verdict"] == "NO_EDGE_FOUND"
        for row in plan["board"]:
            assert "confidence_margin_pp" in row, row
            assert abs(row["confidence_margin_pp"]
                       - (row["confidence"] - row["breakeven_pct"])) < 0.05, row
            # The verdict says no edge, so no board row may show a positive
            # margin on the deciding basis.
            assert row["confidence_margin_pp"] < MIN_EDGE_PP, row

    def test_raw_and_bound_are_both_labelled(self):
        """A reader must be able to tell the observed rate from the bound."""
        plan = self._plan()
        b = plan["best_entry"]
        assert b["observed_pct"] == 77.0
        assert b["confidence"] < b["breakeven_pct"]
        assert b["raw_edge_pp"] > 0        # the honest, clearly-labelled raw edge
        assert b["confidence_margin_pp"] < 0

    def test_a_real_edge_reports_a_positive_margin_on_both_bases(self):
        """When the bound genuinely clears breakeven, the margin is positive and
        the two bases agree in sign (no false restraint either)."""
        _push("B_GOOD", ["3"] * 82 + ["8"] * 18)
        plan = MissionPlanner().plan(
            "scan all markets for under 7 and best entry point",
            ["B_GOOD"], predictions=[{"side": "UNDER", "barrier": 7}],
            window=100)
        # A genuine edge takes the ladder path; every selected row must show a
        # positive margin on the deciding basis.
        assert plan["verdict"] in ("FULL_LADDER", "PARTIAL_LADDER"), plan["verdict"]
        assert plan["selected"], plan["answer"]
        for c in plan["selected"]:
            assert c["play"]["confidence"] > c["play"]["breakeven_pct"], c["play"]
