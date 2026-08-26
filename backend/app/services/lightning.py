"""Lightning-Speed Execution layer — the event-driven hot path.

Design contract (the directive's §34 loop):

  TICK ARRIVES -> read digit -> update ring buffer -> O(1) counters ->
  fast features -> FAST REJECTION -> no opportunity? RETURN ->
  deep analysis -> probability -> proposal verification -> EV ->
  risk check -> signal still fresh? -> decision -> RETURN -> async logging

Honesty constraints, unchanged from the bottom-up and super layers: speed
never bypasses statistical or risk validation (§30). The Lightning engine
DECIDES; it does not place trades. Every "execution" is an advisory record
with duplicate protection, latency profiling, and a fast failsafe.

Components:

- RingWindow: fixed-size circular buffer per symbol per window with O(1)
  digit counts — 2 counter ops per tick, never a rescan (§5/§6).
- FastFilter: Tier-1 checks in microseconds — risk veto, sample floor,
  best-case edge screen from the incremental counts (§9/§10).
- Deep path: delegates to the super ensemble ONLY for survivors (§9).
- LatencyProfiler: per-stage P50/P90/P95/P99 (§20/§31).
- EventBus: P0-P5 priority queue for anything non-critical (§13/§26/§27).
- TradeLedger: client_trade_id lifecycle CREATED -> SUBMITTED ->
  CONFIRMED/REJECTED/UNKNOWN; a timed-out request is never blindly
  retried (§29).
- Failsafe: unknown state (dead connection, stale feed, unknown risk)
  blocks every new position (§28).
"""
import math
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple

from app.core.queue import tick_queue
from app.services.bottom_up import BottomUpEngine, bottom_up_engine
from app.services.super_profit import SuperProfitEngine, super_profit_engine

# §21 performance targets (ms) — local software targets, not network promises.
TARGETS_MS = {
    "parse": 0.5,
    "features": 1.0,
    "fast_filter": 2.0,
    "deep": 20.0,
    "risk": 1.0,
    "decision_total": 10.0,
}

# §26 message priorities.
PRIORITY = {"P0_TRADE": 0, "P1_TICK": 1, "P2_PROPOSAL": 2,
            "P3_DASHBOARD": 3, "P4_ANALYTICS": 4, "P5_LOGGING": 5}

# §7 precomputed constants — nothing in the hot path recomputes these.
_LOG10 = math.log(10.0)
_BREAKEVENS = {k: 1.0 / v for k, v in
               {"MATCHES": 9.0, "OVER": 1.95, "UNDER": 1.95,
                "ODD": 1.95, "EVEN": 1.95, "DIFFERS": 1.10}.items()}


class RingWindow:
    """§5/§6: circular buffer + incremental digit counts. O(1) per tick."""

    __slots__ = ("size", "buf", "head", "filled", "counts", "total")

    def __init__(self, size: int):
        self.size = size
        self.buf = [0] * size
        self.head = 0
        self.filled = 0
        self.counts = [0] * 10
        self.total = 0

    def push(self, digit: int) -> None:
        if self.filled == self.size:
            old = self.buf[self.head]
            self.counts[old] -= 1
        else:
            self.filled += 1
        self.buf[self.head] = digit
        self.counts[digit] += 1
        self.head = (self.head + 1) % self.size
        self.total += 1

    def probs(self) -> List[float]:
        if self.filled == 0:
            return [0.1] * 10
        n = self.filled
        return [(c + 1) / (n + 10) for c in self.counts]  # Dirichlet posterior


class SymbolState:
    """Per-market lightweight state (§11). One slow market never touches
    another's state."""

    __slots__ = ("symbol", "windows", "last_tick_epoch", "last_signal",
                 "fast_ok_count", "deep_count", "last_decision_ms")

    def __init__(self, symbol: str, window_sizes: Tuple[int, ...]):
        self.symbol = symbol
        self.windows = {w: RingWindow(w) for w in window_sizes}
        self.last_tick_epoch = 0.0
        self.last_signal: Optional[dict] = None
        self.fast_ok_count = 0
        self.deep_count = 0
        self.last_decision_ms = 0.0

    @property
    def biggest(self) -> RingWindow:
        return self.windows[max(self.windows)]

    @property
    def smallest(self) -> RingWindow:
        return self.windows[min(self.windows)]


@dataclass
class StageTimes:
    parse_ms: float = 0.0
    features_ms: float = 0.0
    fast_filter_ms: float = 0.0
    deep_ms: float = 0.0
    risk_ms: float = 0.0
    total_ms: float = 0.0

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in asdict(self).items()}


class LatencyProfiler:
    """§20/§31: per-stage percentiles over a rolling sample."""

    def __init__(self, capacity: int = 500):
        self._samples: Dict[str, Deque[float]] = {
            k: deque(maxlen=capacity) for k in
            ("parse", "features", "fast_filter", "deep", "risk", "decision_total")
        }
        self.count = 0

    def record(self, stages: StageTimes) -> None:
        self._samples["parse"].append(stages.parse_ms)
        self._samples["features"].append(stages.features_ms)
        self._samples["fast_filter"].append(stages.fast_filter_ms)
        self._samples["deep"].append(stages.deep_ms)
        self._samples["risk"].append(stages.risk_ms)
        self._samples["decision_total"].append(stages.total_ms)
        self.count += 1

    @staticmethod
    def _pct(xs: List[float], p: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        idx = min(len(s) - 1, max(0, math.ceil(p * len(s)) - 1))
        return round(s[idx], 3)

    def stats(self) -> dict:
        return {
            "samples": self.count,
            "stages": {
                name: {
                    "p50": self._pct(list(xs), 0.50),
                    "p90": self._pct(list(xs), 0.90),
                    "p95": self._pct(list(xs), 0.95),
                    "p99": self._pct(list(xs), 0.99),
                    "target_ms": TARGETS_MS.get(name),
                    "within_target": self._pct(list(xs), 0.95) <= TARGETS_MS.get(name, 9e9),
                }
                for name, xs in self._samples.items()
            },
            "note": "Optimize the P95/P99 tail, never one fast trade (§31).",
        }


@dataclass
class Event:
    priority: int
    kind: str
    payload: dict
    created_at: float = field(default_factory=time.monotonic)


class EventBus:
    """§13/§26/§27: non-critical work leaves the hot path entirely. The
    dashboard consuming nothing never slows a decision."""

    def __init__(self, capacity: int = 2000):
        self._queues: Dict[int, Deque[Event]] = {p: deque(maxlen=capacity)
                                                 for p in PRIORITY.values()}
        self._handlers: List[Callable[[Event], None]] = []
        self.dropped = 0

    def emit(self, priority: str, kind: str, payload: dict) -> None:
        q = self._queues[PRIORITY[priority]]
        if len(q) == q.maxlen:
            self.dropped += 1
        q.append(Event(PRIORITY[priority], kind, payload))

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        self._handlers.append(handler)

    def drain(self, limit: int = 200) -> List[dict]:
        out = []
        for p in sorted(self._queues):
            q = self._queues[p]
            while q and len(out) < limit:
                ev = q.popleft()
                for h in self._handlers:
                    try:
                        h(ev)
                    except Exception:  # noqa: BLE001 — handlers must never break the bus
                        pass
                out.append({"priority": p, "kind": ev.kind, "payload": ev.payload})
        return out


@dataclass
class TradeRecord:
    client_trade_id: str
    symbol: str
    contract: str
    barrier: Optional[int]
    state: str  # CREATED | SUBMITTED | CONFIRMED | REJECTED | UNKNOWN
    created_at: float
    updated_at: float
    detail: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["age_s"] = round(time.monotonic() - self.created_at, 3)
        return d


class TradeLedger:
    """§29: duplicate protection. A timed-out request is UNKNOWN — resolve
    it before any retry, never blind-resubmit."""

    UNKNOWN_AFTER_S = 10.0

    def __init__(self):
        self._records: Dict[str, TradeRecord] = {}

    def create(self, client_trade_id: str, symbol: str, contract: str,
               barrier: Optional[int], detail: str = "") -> Optional[TradeRecord]:
        if client_trade_id in self._records:
            return None  # duplicate id — refused
        rec = TradeRecord(client_trade_id, symbol, contract, barrier,
                          "CREATED", time.monotonic(), time.monotonic(), detail)
        self._records[client_trade_id] = rec
        return rec

    def transition(self, client_trade_id: str, state: str, detail: str = "") -> Optional[TradeRecord]:
        rec = self._records.get(client_trade_id)
        if rec is None:
            return None
        allowed = {
            "CREATED": {"SUBMITTED", "REJECTED"},
            "SUBMITTED": {"CONFIRMED", "REJECTED", "UNKNOWN"},
            "UNKNOWN": {"CONFIRMED", "REJECTED"},
            "CONFIRMED": set(),
            "REJECTED": set(),
        }
        if state not in allowed.get(rec.state, set()):
            return None
        rec.state = state
        rec.detail = detail or rec.detail
        rec.updated_at = time.monotonic()
        return rec

    def expire_unknowns(self) -> List[str]:
        now = time.monotonic()
        expired = []
        for rec in self._records.values():
            if rec.state == "SUBMITTED" and now - rec.updated_at > self.UNKNOWN_AFTER_S:
                rec.state = "UNKNOWN"
                rec.detail = "confirmation timed out — resolve before any retry (§29)"
                rec.updated_at = now
                expired.append(rec.client_trade_id)
        return expired

    def open_unknowns(self) -> List[dict]:
        return [r.to_dict() for r in self._records.values() if r.state == "UNKNOWN"]

    def snapshot(self) -> dict:
        states = Counter(r.state for r in self._records.values())
        return {
            "records": len(self._records),
            "by_state": dict(states),
            "unknowns": self.open_unknowns(),
            "note": "UNKNOWN is a lock, not a retry cue: the original request "
                    "must be resolved before capital moves again (§29).",
        }


class LightningEngine:
    """§34 the lightning loop, advisory-only (no broker calls)."""

    def __init__(self, queue=None, layer: BottomUpEngine = None,
                 ensemble: SuperProfitEngine = None,
                 window_sizes: Tuple[int, ...] = (50, 250, 1000)):
        self.queue = queue or tick_queue
        self._layer = layer or bottom_up_engine
        self._ensemble = ensemble or super_profit_engine
        self.window_sizes = window_sizes
        self.symbols: Dict[str, SymbolState] = {}
        self.profiler = LatencyProfiler()
        self.bus = EventBus()
        self.ledger = TradeLedger()
        self.connection = {"connected": True, "last_tick_epoch": 0.0,
                           "reconnect_count": 0, "rtt_ms": None}
        self.max_signal_age_ticks = self._layer.config.max_signal_age_ticks

    # ---------------- §28 fast failsafe ----------------
    def failsafe(self, symbol: str) -> Optional[str]:
        if not self.connection["connected"]:
            return "connection lost — no new positions"
        st = self.symbols.get(symbol)
        if st is None or st.last_tick_epoch == 0.0:
            return "no ticks seen for this market"
        age = time.monotonic() - st.last_tick_epoch
        if age > 30.0:
            return f"stale feed ({age:.0f}s since last tick) — no new positions"
        self.ledger.expire_unknowns()
        unknowns = self.ledger.open_unknowns()
        if unknowns:
            return f"{len(unknowns)} execution(s) in UNKNOWN state — resolve first (§29)"
        return None

    # ---------------- the hot path ----------------
    def on_tick(self, tick) -> dict:
        """§8: the minimal hot path. Only this symbol is touched (§4)."""
        t0 = time.perf_counter_ns()
        stages = StageTimes()

        # --- parse ---
        symbol, digit = tick.symbol, tick.digit
        stages.parse_ms = (time.perf_counter_ns() - t0) / 1e6

        # --- O(1) feature update (§5/§6) ---
        t1 = time.perf_counter_ns()
        st = self.symbols.get(symbol)
        if st is None:
            st = self.symbols[symbol] = SymbolState(symbol, self.window_sizes)
            # warm from the queue once — never per tick
            for t in self.queue.recent(symbol, limit=max(self.window_sizes)):
                for w in st.windows.values():
                    w.push(t.digit)
        for w in st.windows.values():
            w.push(digit)
        st.last_tick_epoch = time.monotonic()
        self.connection["last_tick_epoch"] = st.last_tick_epoch
        stages.features_ms = (time.perf_counter_ns() - t1) / 1e6

        # --- Tier 1 fast filter (§9/§10) ---
        t2 = time.perf_counter_ns()
        verdict = self._fast_filter(st)
        stages.fast_filter_ms = (time.perf_counter_ns() - t2) / 1e6
        if verdict is not None:
            stages.total_ms = (time.perf_counter_ns() - t0) / 1e6
            self.profiler.record(stages)
            self.bus.emit("P4_ANALYTICS", "fast_reject", {"symbol": symbol, **verdict})
            return {"symbol": symbol, "decision": "SKIP", "reason": verdict["reason"],
                    "stages": stages.to_dict()}

        # --- Tier 2 deep analysis (§9) — only for survivors ---
        st.fast_ok_count += 1
        t3 = time.perf_counter_ns()
        decision = self._ensemble.decide(symbol)
        stages.deep_ms = (time.perf_counter_ns() - t3) / 1e6
        st.deep_count += 1

        # --- risk + freshness (§10/§19) ---
        t4 = time.perf_counter_ns()
        fail = self.failsafe(symbol)
        final = decision.get("final", "REJECT")
        if fail:
            final = "REJECT"
            decision["failsafe"] = fail
        stages.risk_ms = (time.perf_counter_ns() - t4) / 1e6

        stages.total_ms = (time.perf_counter_ns() - t0) / 1e6
        st.last_decision_ms = stages.total_ms
        self.profiler.record(stages)
        self.bus.emit("P3_DASHBOARD", "decision", {
            "symbol": symbol, "final": final, "total_ms": round(stages.total_ms, 3)})
        if final == "EXECUTE":
            self.bus.emit("P2_PROPOSAL", "verify_proposal", {
                "symbol": symbol, "contract": decision.get("contract"),
                "barrier": decision.get("barrier"),
                "note": "advisory: the live path must fetch a FRESH proposal and "
                        "re-run the gates with the real payout before money moves (§18)"})
        return {
            "symbol": symbol,
            "decision": final,
            "stages": stages.to_dict(),
            "ensemble": {k: decision.get(k) for k in
                         ("contract", "barrier", "ev", "edge", "meta_score",
                          "uncertainty", "regime", "failed_gates", "failsafe")},
            "note": "Speed never bypassed validation: the fast path only SKIPS, "
                    "it never EXECUTES without the deep ensemble (§30).",
        }

    # ---------------- Tier 1 (§9/§10/§17) ----------------
    def _fast_filter(self, st: SymbolState) -> Optional[dict]:
        """Microseconds-only screen from incremental counts. Returns None when
        a candidate MIGHT exist, else a reject reason."""
        big = st.biggest
        if big.filled < self._layer.config.min_sample:
            return {"reason": f"insufficient_sample ({big.filled})"}
        # §10 short-circuit: with a flat, stable distribution every contract
        # fails the safety margin — deep analysis is pure waste (§9: STOP).
        margin = self._layer.config.min_edge
        post = big.probs()
        pmax, pmin = max(post), min(post)
        small = st.smallest
        sp = small.probs() if small.filled >= 25 else post
        divergence = max(abs(a - b) for a, b in zip(post, sp))
        matches_ok = pmax >= _BREAKEVENS["MATCHES"] + margin
        differs_ok = (1.0 - pmin) >= _BREAKEVENS["DIFFERS"] + margin
        if not matches_ok and not differs_ok and divergence < margin:
            return {"reason": "no_plausible_edge: distribution inside margin on every window"}
        return None

    # ---------------- §32 dashboard ----------------
    def dashboard(self) -> dict:
        self.ledger.expire_unknowns()
        active = [s for s, st in self.symbols.items() if st.biggest.total > 0]
        prof = self.profiler.stats()
        stages = prof["stages"]
        return {
            "websocket": "CONNECTED" if self.connection["connected"] else "DISCONNECTED",
            "last_tick_age_s": (round(time.monotonic() - self.connection["last_tick_epoch"], 1)
                                if self.connection["last_tick_epoch"] else None),
            "reconnect_count": self.connection["reconnect_count"],
            "tick_processing_ms": stages["features"]["p50"],
            "fast_filter_ms": stages["fast_filter"]["p50"],
            "deep_signal_ms": stages["deep"]["p50"],
            "risk_ms": stages["risk"]["p50"],
            "decision_total_ms": stages["decision_total"]["p50"],
            "decision_p95_ms": stages["decision_total"]["p95"],
            "decision_p99_ms": stages["decision_total"]["p99"],
            "within_targets": all(s["within_target"] for s in stages.values()),
            "markets_active": len(active),
            "markets": active,
            "fast_passes": sum(st.fast_ok_count for st in self.symbols.values()),
            "deep_runs": sum(st.deep_count for st in self.symbols.values()),
            "ledger": self.ledger.snapshot(),
            "note": "LOW LATENCY + HIGH SELECTIVITY + VALIDATED EDGE + STRICT RISK "
                    "= maximum defensible profitability (§35). Advisory only — "
                    "this engine never places a trade.",
        }

    # ---------------- §29 execution ledger API ----------------
    def begin_trade(self, client_trade_id: str, symbol: str, contract: str,
                    barrier: Optional[int], detail: str = "") -> dict:
        rec = self.ledger.create(client_trade_id, symbol, contract, barrier, detail)
        if rec is None:
            return {"ok": False, "error": "duplicate client_trade_id — refused (§29)"}
        return {"ok": True, "record": rec.to_dict()}

    def update_trade(self, client_trade_id: str, state: str, detail: str = "") -> dict:
        rec = self.ledger.transition(client_trade_id, state, detail)
        if rec is None:
            return {"ok": False, "error": "unknown id or illegal transition"}
        return {"ok": True, "record": rec.to_dict()}


lightning_engine = LightningEngine()
