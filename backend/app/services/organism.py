"""The Conveyor-Belt organism — one continuous decision body.

Not six features: one flow. Every tick enters the same pipeline, every
stage feeds the next, and the loop never ends:

  DATA ARMOR (validate tick)
    -> SPEED CORE (O(1) incremental update, fast brain)
    -> EAGLE EYE (three-horizon market state)
    -> PRECISION (candidate ranking)
    -> COMPETITION (candidate auction)
    -> VENOM (adversarial toxicity attack)
    -> STRENGTH (defensive gates: connection, risk, failsafe, ledger)
    -> FINAL GATE (crosshair)
    -> STRIKE | REJECT
    -> RESULT -> SELF-CRITIQUE -> EVOLUTION -> back to VISION

The control spine (§17) is an explicit state machine:
OBSERVING -> ANALYZING -> CANDIDATE -> VALIDATING -> ARMED -> EXECUTING
-> CONFIRMING -> RECORDED -> LEARNING -> HARDENING -> OBSERVING, with
FAILURE -> SAFE_STATE reachable from every state. No undefined states.

Speed-of-Sound contract (engineering target, not a network promise):
the internal pipeline is non-blocking; deep analysis only runs for fast-
brain survivors; database/dashboard/logging are async bus events; every
stage is timestamped for P50/P90/P95/P99 tail profiling. Speed never
bypasses precision or safety (§11). Advisory-only: STRIKE emits an armed
execution card; no broker call is made here.
"""
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from app.services.bottom_up import bottom_up_engine, BottomUpEngine
from app.services.eagle import EagleEngine, eagle_engine
from app.services.lightning import LightningEngine, lightning_engine
from app.services.pro_trader import RNG_NOTE
from app.services.super_profit import SuperProfitEngine, super_profit_engine

# §17 control spine states
SPINE = ("OBSERVING", "ANALYZING", "CANDIDATE", "VALIDATING", "ARMED",
         "EXECUTING", "CONFIRMING", "RECORDED", "LEARNING", "HARDENING",
         "SAFE_STATE")

# Tanker immutable rules (§31) — the learning system may never rewrite these.
IMMUTABLE_RULES = (
    "max_account_exposure",
    "max_drawdown",
    "emergency_stop",
    "duplicate_trade_protection",
    "position_reconciliation",
    "speed_never_bypasses_validation",
)


@dataclass
class StageTrace:
    stage: str
    ms: float
    outcome: str  # pass | reject | skip

    def to_dict(self) -> dict:
        return {"stage": self.stage, "ms": round(self.ms, 3), "outcome": self.outcome}


@dataclass
class SpineTransition:
    state: str
    at: float = field(default_factory=time.monotonic)
    reason: str = ""


class ControlSpine:
    """§17: one state machine through the entire architecture."""

    ALLOWED = {
        "OBSERVING": {"ANALYZING", "SAFE_STATE"},
        "ANALYZING": {"CANDIDATE", "OBSERVING", "SAFE_STATE"},
        "CANDIDATE": {"VALIDATING", "OBSERVING", "SAFE_STATE"},
        "VALIDATING": {"ARMED", "OBSERVING", "SAFE_STATE"},
        "ARMED": {"EXECUTING", "OBSERVING", "SAFE_STATE"},
        "EXECUTING": {"CONFIRMING", "SAFE_STATE"},
        "CONFIRMING": {"RECORDED", "SAFE_STATE"},
        "RECORDED": {"LEARNING"},
        "LEARNING": {"HARDENING"},
        "HARDENING": {"OBSERVING"},
        "SAFE_STATE": {"OBSERVING"},
    }

    def __init__(self):
        self.state = "OBSERVING"
        self.history: List[SpineTransition] = [SpineTransition("OBSERVING")]

    def go(self, state: str, reason: str = "") -> bool:
        if state not in self.ALLOWED.get(self.state, set()):
            return False
        self.state = state
        self.history.append(SpineTransition(state, reason=reason))
        if len(self.history) > 200:
            self.history = self.history[-200:]
        return True

    def fail(self, reason: str) -> None:
        if self.state != "SAFE_STATE":
            self.state = "SAFE_STATE"
            self.history.append(SpineTransition("SAFE_STATE", reason=reason))


class Organism:
    """The single body. Composes the existing layers; adds nothing they
    already own, and enforces that no stage dominates another (§16)."""

    def __init__(self, lightning: LightningEngine = None, layer: BottomUpEngine = None,
                 ensemble: SuperProfitEngine = None, eagle: EagleEngine = None):
        self._lightning = lightning or lightning_engine
        self._layer = layer or bottom_up_engine
        self._ensemble = ensemble or super_profit_engine
        self._eagle = eagle or eagle_engine
        self.spine = ControlSpine()
        self.cycles = 0
        self.strikes = 0
        self.rejects = 0
        self.stage_times: Dict[str, List[float]] = {s: [] for s in (
            "data_armor", "speed", "vision", "precision", "competition",
            "venom", "strength", "final_gate", "total")}

    # ---------------- Stage 0: data armor ----------------
    def _data_armor(self, tick) -> Optional[str]:
        d = tick.digit
        if not isinstance(d, int) or not 0 <= d <= 9:
            return "invalid digit"
        if not tick.symbol:
            return "unknown symbol"
        if tick.quote is None or tick.quote <= 0:
            return "invalid quote"
        return None

    # ---------------- the belt ----------------
    def process(self, tick, user_id: str = "default",
                risk_blocked: bool = False) -> dict:
        t0 = time.perf_counter_ns()
        trace: List[StageTrace] = []
        self.cycles += 1
        symbol = tick.symbol

        def mark(stage: str, started_ns: int, outcome: str) -> None:
            ms = (time.perf_counter_ns() - started_ns) / 1e6
            trace.append(StageTrace(stage, ms, outcome))
            self.stage_times[stage].append(ms)

        # Stage 0 — DATA ARMOR
        s0 = time.perf_counter_ns()
        bad = self._data_armor(tick)
        mark("data_armor", s0, "reject" if bad else "pass")
        if bad:
            self.spine.fail(f"data armor: {bad}")
            self.spine.go("OBSERVING")
            self.rejects += 1
            return self._result(symbol, "REJECT", f"data armor: {bad}", trace, t0)

        self.spine.go("ANALYZING")

        # Stage 1 — SPEED CORE (hot path + fast brain)
        s1 = time.perf_counter_ns()
        hot = self._lightning.on_tick(tick)
        mark("speed", s1, "pass")
        if hot["decision"] == "SKIP":
            self.spine.go("OBSERVING", "fast brain: no candidate")
            self.rejects += 1
            return self._result(symbol, "SKIP", hot["reason"], trace, t0)

        # Stage 2 — EAGLE VISION
        s2 = time.perf_counter_ns()
        state = self._eagle.market_state(symbol)
        mark("vision", s2, "pass")
        if state.get("state") == "NO_DATA" or not state.get("data_ok"):
            self.spine.go("SAFE_STATE", "vision: market state untrusted")
            self.spine.go("OBSERVING")
            self.rejects += 1
            return self._result(symbol, "REJECT", "vision: untrusted market state", trace, t0)

        self.spine.go("CANDIDATE")

        # Stage 3 — PRECISION (candidate ranking via eagle strike)
        s3 = time.perf_counter_ns()
        strike = self._eagle.strike(symbol, risk_blocked=risk_blocked, user_id=user_id)
        mark("precision", s3, "pass")
        if strike.get("final") != "STRIKE":
            self.spine.go("OBSERVING", strike.get("reason", "precision: no strike"))
            self.rejects += 1
            return self._result(symbol, "REJECT",
                                strike.get("reason", "precision gates failed"), trace, t0,
                                eagle=strike)

        # Stage 4 — COMPETITION (this candidate vs the field)
        s4 = time.perf_counter_ns()
        auction = self._ensemble.auction([symbol], user_id=user_id,
                                         risk_blocked=risk_blocked)
        mark("competition", s4, "pass")
        if not auction.get("winner"):
            self.spine.go("OBSERVING", "competition: no auction winner")
            self.rejects += 1
            return self._result(symbol, "REJECT", "lost the opportunity auction", trace, t0,
                                eagle=strike)

        self.spine.go("VALIDATING")

        # Stage 5 — VENOM (attack the survivor)
        s5 = time.perf_counter_ns()
        venom = self._venom(strike, state)
        mark("venom", s5, "reject" if venom["toxic"] else "pass")
        if venom["toxic"]:
            self.spine.go("OBSERVING", f"venom: {venom['reason']}")
            self.rejects += 1
            return self._result(symbol, "KILL", venom["reason"], trace, t0,
                                eagle=strike, venom=venom)

        # Stage 6 — TANKER STRENGTH (defensive body)
        s6 = time.perf_counter_ns()
        strength = self._strength(symbol, risk_blocked)
        mark("strength", s6, "reject" if strength else "pass")
        if strength:
            self.spine.go("SAFE_STATE", strength)
            self.spine.go("OBSERVING")
            self.rejects += 1
            return self._result(symbol, "REJECT", strength, trace, t0, eagle=strike)

        # Stage 7 — FINAL GATE (crosshair)
        s7 = time.perf_counter_ns()
        crosshair = {
            "symbol": symbol,
            "contract": strike["contract"],
            "barrier": strike["barrier"],
            "probability": strike["probability"],
            "breakeven": strike["breakeven"],
            "edge": strike["edge"],
            "ev": strike["ev"],
            "band": strike["band"],
            "entry_precision_score": strike["entry_precision_score"],
            "signal_state": strike.get("signal_state"),
        }
        mark("final_gate", s7, "pass")
        self.spine.go("ARMED")

        # Stage 8 — STRIKE (advisory execution card; no broker call)
        self.spine.go("EXECUTING")
        self.strikes += 1
        card = {
            "crosshair": crosshair,
            "execution": "ARMED — advisory card. The live executor must fetch a "
                         "FRESH proposal, re-run the gates with the real payout, "
                         "and only then buy (one-tick recheck, eagle §18).",
            "risk": "ACCEPTABLE",
        }
        # Stage 9/10 — confirm + record happen asynchronously on the bus
        self.spine.go("CONFIRMING")
        self.spine.go("RECORDED")
        self._lightning.bus.emit("P0_TRADE", "armed_strike", card)
        self._lightning.bus.emit("P4_ANALYTICS", "self_critique",
                                 {"symbol": symbol, "band": strike["band"]})
        self.spine.go("LEARNING")
        self.spine.go("HARDENING")
        self.spine.go("OBSERVING")
        return self._result(symbol, "STRIKE", "all stages survived", trace, t0,
                            eagle=strike, venom=venom, card=card)

    # ---------------- Stage 5 venom ----------------
    def _venom(self, strike: dict, state: dict) -> dict:
        """☠️ attack the candidate: kill weak signals, no sympathy."""
        reasons = []
        if strike.get("uncertainty", 1.0) > 0.30:
            reasons.append(f"uncertainty {strike['uncertainty']}")
        if strike.get("contradiction", 0.0) > 0.15:
            reasons.append(f"contradiction {strike['contradiction']}")
        if (strike.get("signal_lifetime") or {}).get("edge_slope", 0.0) < 0:
            reasons.append("edge decaying")
        if strike.get("horizons", {}).get("spike_only"):
            reasons.append("short-horizon spike without baseline support")
        if strike.get("band") == "NO_TRADE":
            reasons.append("precision band NO_TRADE")
        if state.get("regime") in ("UNSTABLE", "HIGH_ANOMALY"):
            reasons.append(f"regime {state.get('regime')}")
        toxic = bool(reasons)
        return {"toxic": toxic, "reason": "; ".join(reasons) if reasons else "",
                "note": "TRADE or KILL. No 'maybe'." if toxic else
                        "the candidate survived the attack"}

    # ---------------- Stage 6 strength ----------------
    def _strength(self, symbol: str, risk_blocked: bool) -> Optional[str]:
        if risk_blocked:
            return "risk engine locked — TRADING = LOCKED"
        return self._lightning.failsafe(symbol)

    # ---------------- result ----------------
    def _result(self, symbol: str, decision: str, reason: str,
                trace: List[StageTrace], t0_ns: int, **extra) -> dict:
        total_ms = (time.perf_counter_ns() - t0_ns) / 1e6
        self.stage_times["total"].append(total_ms)
        for k in self.stage_times:
            if len(self.stage_times[k]) > 500:
                self.stage_times[k] = self.stage_times[k][-500:]
        out = {
            "symbol": symbol,
            "decision": decision,
            "reason": reason,
            "spine": self.spine.state,
            "total_ms": round(total_ms, 3),
            "trace": [t.to_dict() for t in trace],
            "note": RNG_NOTE,
        }
        out.update(extra)
        return out

    # ---------------- profiling ----------------
    @staticmethod
    def _pct(xs: List[float], p: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        return round(s[min(len(s) - 1, max(0, int(p * len(s)) - 1))], 3)

    def performance(self) -> dict:
        return {
            "cycles": self.cycles,
            "strikes": self.strikes,
            "rejects": self.rejects,
            "selectivity": round(self.strikes / self.cycles, 4) if self.cycles else 0.0,
            "stages_ms": {
                k: {"p50": self._pct(v, 0.50), "p90": self._pct(v, 0.90),
                    "p95": self._pct(v, 0.95), "p99": self._pct(v, 0.99)}
                for k, v in self.stage_times.items()
            },
            "note": "Internal pipeline targets only — network/API latency is "
                    "outside the application's control. Tail (P95/P99) is what "
                    "matters. Speed is the bloodstream, never the decision.",
        }

    def spine_status(self) -> dict:
        return {
            "state": self.spine.state,
            "recent": [{"state": t.state, "reason": t.reason}
                       for t in self.spine.history[-15:]],
            "immutable_rules": list(IMMUTABLE_RULES),
            "note": "No stage dominates another: speed cannot override risk, "
                    "venom cannot blacklist on a small sample, learning cannot "
                    "rewrite the immutable rules.",
        }


organism = Organism()
