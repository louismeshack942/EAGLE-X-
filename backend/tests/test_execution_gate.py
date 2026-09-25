"""Execution Gate tests.

The audit finding this closes: the CF consulted exactly one precision layer
(truth_engine.proven_edges) before staking real money, while eagle, organism,
super_profit, bottom_up and lightning were built, tested, and never wired into
the execution path. They could describe a bad trade; they could not stop one.
"""
import os

import pytest

from app.services import execution_gate


@pytest.fixture(autouse=True)
def _preserve_global_tape():
    """These tests push skew tape into the *global* tick queue, because the
    gate reads it through bottom_up_engine. The session fixture fills that same
    queue with R_100 tape, so clearing it here would starve every later test
    that needs R_100 -- which is exactly what happened. Snapshot and restore
    the buffers instead of mutating shared state.
    """
    from app.core.queue import tick_queue

    with tick_queue._lock:
        saved = {sym: list(buf) for sym, buf in tick_queue._buffers.items()}
    try:
        yield
    finally:
        with tick_queue._lock:
            tick_queue._buffers.clear()
            for sym, ticks in saved.items():
                from collections import deque

                tick_queue._buffers[sym] = deque(ticks, maxlen=tick_queue._maxlen)


def _play(contract="OVER", digit=6):
    return {"name": f"{contract} {digit}", "type": contract, "digit": digit,
            "ev": 0.05, "payout": 1.9}


# ---------------- mode handling ----------------

class TestModes:
    def test_default_is_advisory(self, monkeypatch):
        monkeypatch.delenv("EXECUTION_GATE_MODE", raising=False)
        assert execution_gate.mode() == "advisory"

    def test_unknown_mode_falls_back_to_advisory(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GATE_MODE", "yolo")
        assert execution_gate.mode() == "advisory"

    def test_off_never_blocks(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GATE_MODE", "off")
        v = execution_gate.check_play(_play(), "R_100")
        assert v["allowed"] is True

    def test_advisory_reports_but_permits(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GATE_MODE", "advisory")
        v = execution_gate.check_play(_play(), "R_100")
        assert v["allowed"] is True
        assert v["mode"] == "advisory"

    def test_enforce_blocks_when_a_layer_vetoes(self, monkeypatch):
        """Force a single layer to veto and confirm enforce turns that into a
        refusal. Patches a real layer, not a helper that does not exist."""
        import app.services.super_profit as sp

        monkeypatch.setattr(
            sp.super_profit_engine, "decide",
            lambda *a, **k: {"final": "REJECT", "contract": "OVER", "barrier": 6,
                             "failed_gates": ["consensus", "uncertainty"]},
            raising=False,
        )
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        v = execution_gate.check_play(_play("OVER", 6), "R_100")
        assert any("super_profit" in b for b in v["blockers"])
        assert v["allowed"] is False

    def test_same_veto_does_not_block_in_advisory(self, monkeypatch):
        import app.services.super_profit as sp

        monkeypatch.setattr(
            sp.super_profit_engine, "decide",
            lambda *a, **k: {"final": "REJECT", "contract": "OVER", "barrier": 6,
                             "failed_gates": ["consensus"]},
            raising=False,
        )
        monkeypatch.setenv("EXECUTION_GATE_MODE", "advisory")
        v = execution_gate.check_play(_play("OVER", 6), "R_100")
        assert any("super_profit" in b for b in v["blockers"])
        assert v["allowed"] is True  # reported, not enforced


# ---------------- veto semantics ----------------

class TestVetoOnly:
    def test_gate_cannot_promote_a_rejected_play(self, monkeypatch):
        """The gate has no promote path. A play that arrives is either allowed
        or refused -- it can never be manufactured here."""
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        v = execution_gate.check_play(_play(), "R_100")
        assert set(v.keys()) >= {"allowed", "blockers", "layers", "mode"}
        assert "promote" not in v
        # allowed is a function of blockers alone -- no positive requirement
        # can make it True that blockers do not make False.
        assert v["allowed"] == (not v["blockers"])

    def test_would_block_is_reported_in_every_mode(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GATE_MODE", "advisory")
        v = execution_gate.check_play(_play(), "R_100")
        assert "would_block" in v
        assert isinstance(v["blockers"], list)


# ---------------- fail closed ----------------

class TestFailClosed:
    def test_layer_exception_becomes_a_blocker(self, monkeypatch):
        """A gate that opens when it breaks is not a gate."""
        import app.services.super_profit as sp

        def boom(*_a, **_k):
            raise RuntimeError("brain exploded")

        monkeypatch.setattr(sp.super_profit_engine, "decide", boom, raising=False)
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        v = execution_gate.check_play(_play(), "R_100")
        assert v["layers"]["super_profit"].get("error")
        assert any("super_profit" in b for b in v["blockers"])

    def test_missing_candidate_is_a_blocker_not_an_approval(self, monkeypatch):
        """No opinion is not consent: an unevaluated contract must not pass."""
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        v = execution_gate.check_play(_play("OVER", 6), "NONEXISTENT_SYMBOL")
        assert v["layers"]["bottom_up"].get("evaluated") is False
        assert v["allowed"] is False


# ---------------- exact-contract matching ----------------

class TestExactContractMatching:
    def test_verdict_is_not_transferred_between_barriers(self):
        """A verdict about OVER is not a verdict about OVER 6. Judging must
        match on (contract, barrier) exactly, never on family alone."""
        ctx = {"bottom_up": {"candidates": {
            ("OVER", 6): {"contract": "OVER", "barrier": 6, "decision": "PASS",
                          "grade": "A", "auto_executable": True}}},
            "super_profit": {}, "eagle": {}, "risk": {}}

        layers, blockers = execution_gate._judge(
            {"type": "OVER", "digit": 6}, ctx)
        assert layers["bottom_up"]["evaluated"] is True
        assert not blockers

        # Same family, different barrier -> no candidate, no implied approval.
        layers, blockers = execution_gate._judge(
            {"type": "OVER", "digit": 7}, ctx)
        assert layers["bottom_up"]["evaluated"] is False
        assert any("not evaluated" in b for b in blockers)

        # Different family, same barrier -> also no candidate.
        layers, blockers = execution_gate._judge(
            {"type": "UNDER", "digit": 6}, ctx)
        assert layers["bottom_up"]["evaluated"] is False


# ---------------- the two properties that make this a gate, not a ban -------

class TestGateDiscriminates:
    """A gate that always blocks is a blanket ban, and a gate that always
    opens is decoration. Both directions must be demonstrated on real tape.

    This was not hypothetical: the first implementation vetoed a play whenever
    eagle/super_profit rejected *their own separate pick* on that symbol,
    which blocked every play at every skew level. Measured, then fixed.
    """

    @staticmethod
    def _skewed(p, n=3000, seed=11):
        import random
        from app.core.queue import tick_queue
        from app.models.tick import Tick

        random.seed(seed)
        tick_queue.clear("GATE_SKEW")
        for _ in range(n):
            d = 0 if random.random() < p else random.randint(1, 9)
            tick_queue.push(Tick(symbol="GATE_SKEW", quote=float(d),
                                 raw={"digit": d}, provider="deriv_live"))

    def _best(self, monkeypatch):
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        best = None
        for c in ("MATCHES", "OVER", "UNDER", "ODD", "EVEN", "DIFFERS"):
            if c in ("MATCHES", "DIFFERS"):
                bs = range(10)
            elif c == "OVER":
                bs = range(0, 9)
            elif c == "UNDER":
                bs = range(1, 10)
            else:
                bs = [None]
            for b in bs:
                v = execution_gate.check_play(
                    {"name": f"{c} {b}", "type": c, "digit": b}, "GATE_SKEW")
                if best is None or len(v["blockers"]) < len(best[1]["blockers"]):
                    best = (f"{c} {b}", v)
        return best

    def test_enforce_blocks_a_fair_board(self, monkeypatch):
        self._skewed(0.10)  # uniform digits -> no real edge
        name, v = self._best(monkeypatch)
        assert v["allowed"] is False, f"{name} should not pass on a fair board"

    def test_enforce_allows_a_genuine_edge(self, monkeypatch):
        self._skewed(0.45)  # digit 0 at 45% -> a real, large edge
        name, v = self._best(monkeypatch)
        assert v["allowed"] is True, (
            f"enforce mode blocked every play including {name} -- a gate that "
            f"can never open is a blanket ban. blockers={v['blockers']}")
        assert v["layers"]["bottom_up"]["grade"] in ("A+", "A")

    def test_a_layer_refusing_its_own_pick_is_not_a_veto(self, monkeypatch):
        """eagle/super_profit pick their own contract. Their refusal of that
        pick must not veto a different, independently-validated play."""
        import app.services.eagle as eg
        import app.services.super_profit as sp

        monkeypatch.setattr(
            eg.eagle_engine, "strike",
            lambda *a, **k: {"final": "NO_TRADE", "contract": "DIFFERS",
                             "barrier": 3, "failed_gates": ["base_execute"]},
            raising=False,
        )
        monkeypatch.setattr(
            sp.super_profit_engine, "decide",
            lambda *a, **k: {"final": "REJECT", "contract": "DIFFERS",
                             "barrier": 3, "failed_gates": ["consensus"]},
            raising=False,
        )
        self._skewed(0.45)
        monkeypatch.setenv("EXECUTION_GATE_MODE", "enforce")
        # A play on a DIFFERENT contract than the layers chose.
        v = execution_gate.check_play(
            {"name": "OVER 0", "type": "OVER", "digit": 0}, "GATE_SKEW")
        assert not any("eagle" in b for b in v["blockers"])
        assert not any("super_profit" in b for b in v["blockers"])


class TestOneEvaluationPerSymbol:
    def test_shared_context_avoids_rerunning_layers(self, monkeypatch):
        """A scan judges several plays. The layers must be asked once about the
        symbol, not once per play -- otherwise a six-play scan runs the whole
        stack six times inside the trading loop."""
        import app.services.super_profit as sp

        calls = {"n": 0}
        real = sp.super_profit_engine.decide

        def counted(*a, **k):
            calls["n"] += 1
            return real(*a, **k)

        monkeypatch.setattr(sp.super_profit_engine, "decide", counted, raising=False)
        ctx = execution_gate.evaluate_symbol("R_100")
        assert calls["n"] == 1
        for barrier in range(4):
            execution_gate.check_play({"type": "OVER", "digit": barrier},
                                      "R_100", context=ctx)
        assert calls["n"] == 1, "layers were re-run per play"

    def test_context_is_optional(self, monkeypatch):
        """Calling without a context still works (one-shot callers)."""
        v = execution_gate.check_play({"type": "OVER", "digit": 6}, "R_100")
        assert "bottom_up" in v["layers"]
