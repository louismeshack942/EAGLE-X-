"""AI Copilot Chat — a conversation with the platform.

One place to ask the platform questions and get answers grounded in the live
tape, the truth engine and the journal. Different from the mission planner: the
planner answers "what can I trade on these barriers", this answers "what is
going on with the platform" — including the questions the platform should
answer honestly when the answer is "there is no edge" or "the tape is thin".

Two things make this different from a generic chatbot:

- It can say NO. A chat that only ever reports what it can measure is a better
  instrument than one that invents an answer, so `no_edge` and `stale_tape`
  are first-class verdicts, not fallbacks.
- It is honest about what it is NOT. Every reply carries `grounded` (was this
  answered from live data?) and `limitations`. There is no LLM here — this is
  arithmetic over the live tape, so it must never read like an opinion.

Conversations are persisted through the same JSON store the rest of the app
uses, so history survives a restart on any host with a writable disk. On
ephemeral hosts (Render free tier) history is naturally lost with the process —
the reply says so rather than pretending otherwise.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from app.config import get_settings
from app.services.ai_copilot import ai_copilot
from app.services.auto_trader import auto_trader
from app.services.cockpit import live_digit_counts
from app.services.deriv_client import LIVE_STATE
from app.services.mission import parse_request
from app.services.persistence import journal_engine, settings_store
from app.services.risk_guard import risk_guard
from app.services.shell import real_trade_budget
from app.services.truth_engine import truth_engine
from app.services.virtual_bank import virtual_bank
from app.core.queue import tick_queue

logger = logging.getLogger(__name__)

MAX_TURNS = 200

SUGGESTIONS = [
    "what is the probability of over 5 on R_100",
    "is any market showing a real edge right now",
    "how fresh is the tape",
    "why is the platform not trading",
    "how am I doing today",
    "what are the safety limits",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(conversation_id: str) -> str:
    return f"chat_history_{conversation_id}"


def _count_key(conversation_id: str) -> str:
    return f"chat_exchanges_{conversation_id}"


class PlatformChat:
    """Conversational front door to the platform's own analytics."""

    # ---- conversation storage -------------------------------------------------

    def history(self, conversation_id: str = "default",
                limit: int = 50) -> List[dict]:
        turns = settings_store.get(_key(conversation_id), []) or []
        return turns[-limit:]

    def clear(self, conversation_id: str = "default") -> dict:
        settings_store.set(_key(conversation_id), [])
        settings_store.set(_count_key(conversation_id), 0)
        return {"conversation_id": conversation_id, "cleared": True}

    def exchanges(self, conversation_id: str = "default") -> int:
        """Monotonic exchange count.

        Derived from a separate counter, not from len(history): history is
        truncated at MAX_TURNS, so a length-derived count silently freezes at
        the cap and misreports a long conversation as a short one.
        """
        try:
            return int(settings_store.get(_count_key(conversation_id), 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _append(self, conversation_id: str, role: str, text: str,
                meta: Optional[dict] = None) -> dict:
        turns = settings_store.get(_key(conversation_id), []) or []
        turn = {
            "id": str(uuid.uuid4()),
            "role": role,
            "text": text,
            "timestamp": _now(),
            "meta": meta or {},
        }
        turns.append(turn)
        settings_store.set(_key(conversation_id), turns[-MAX_TURNS:])
        return turn

    # ---- the front door -------------------------------------------------------

    def ask(self, message: str, conversation_id: str = "default",
            symbol: Optional[str] = None) -> dict:
        question = (message or "").strip()
        if not question:
            return {"error": "ask me something - the message was empty"}

        self._append(conversation_id, "you", question)

        symbol = symbol or "R_100"
        try:
            reply = self._route(question, symbol)
        except Exception as exc:  # noqa: BLE001 - a chat must not 500
            logger.warning("chat routing failed: %s", exc)
            reply = {
                "text": f"I could not answer that: {exc}",
                "topic": "error",
                "grounded": False,
                "limitations": ["The answer raised an error, so nothing here is measured."],
                "data": {},
            }

        turn = self._append(conversation_id, "platform", reply["text"], {
            "topic": reply.get("topic"),
            "grounded": reply.get("grounded"),
        })
        count = self.exchanges(conversation_id) + 1
        settings_store.set(_count_key(conversation_id), count)

        return {
            "conversation_id": conversation_id,
            "question": question,
            "answer": reply["text"],
            "topic": reply.get("topic", "general"),
            "grounded": reply.get("grounded", False),
            "limitations": reply.get("limitations", []),
            "data": reply.get("data", {}),
            "turn_id": turn["id"],
            "timestamp": turn["timestamp"],
            "exchanges": count,
        }

    # ---- intent routing -------------------------------------------------------

    def _route(self, q: str, symbol: str) -> dict:
        low = q.lower()

        if self._has(low, ["probability", "chance", "odds", "how likely",
                           "percent", "win rate of", "what are the odds"]):
            return self._probability(q, symbol)
        if self._has(low, ["fresh", "stale", "tape", "how many ticks", "data quality",
                           "is the data", "ticks recorded", "feed"]):
            return self._tape(low, symbol)
        if self._has(low, ["edge", "any market", "worth trading", "opportunity",
                           "what should i trade", "best market"]):
            return self._edges(symbol)
        if self._has(low, ["why is the platform", "not trading", "why no trade",
                           "nothing trading", "why not trading", "idle", "why am i not"]):
            return self._why_no_trade(low, symbol)
        if self._has(low, ["safety", "kill switch", "limits", "how much can",
                           "am i protected", "guard", "real money"]):
            return self._safety()
        if self._has(low, ["health", "system status", "is it working", "connected",
                           "uptime", "running", "status of"]):
            return self._health()
        if self._has(low, ["how am i doing", "pnl", "profit", "today", "scorecard",
                           "my results", "balance"]):
            return self._performance()
        if self._has(low, ["help", "what can i ask", "what can you do",
                           "commands", "examples"]):
            return self._help()

        # Everything else is a domain question the existing copilot already
        # knows how to answer honestly (trades, risk, bank, guard, hour, ...).
        answer = ai_copilot.ask(q, symbol)
        return {
            "text": answer.get("answer", "no answer"),
            "topic": "platform",
            "grounded": True,
            "limitations": [
                "Answered by the platform's rule-based analytics engine; no LLM is involved.",
            ],
            "data": {"symbol": answer.get("symbol")},
        }

    @staticmethod
    def _has(low: str, needles) -> bool:
        return any(n in low for n in needles)

    @staticmethod
    def _measure(question: str, symbols, named):
        """Measure a probability question, honouring a market named in it."""
        from app.services.mission import mission_planner
        return mission_planner.probability(
            question, symbols, symbol=named if named in symbols else None)

    # ---- topics ---------------------------------------------------------------

    def _probability(self, q: str, symbol: str) -> dict:
        parsed = parse_request(q)
        named = parsed.get("symbol")
        symbols = get_settings().active_symbols
        if not parsed.get("predictions"):
            return {
                "text": (
                    "Tell me the barrier and I will measure it — for example "
                    "\"what is the probability of over 5 on R_100\". Without a "
                    "barrier there is nothing to measure, and I would rather say "
                    "that than guess."
                ),
                "topic": "probability", "grounded": False,
                "limitations": ["No barrier was given, so nothing was measured."],
                "data": {},
            }
        plan = self._measure(q, symbols, named)
        return {
            "text": plan.get("answer", ""),
            "topic": "probability",
            "grounded": bool(plan.get("best", {}).get("n")),
            "limitations": [
                "Past-digit frequencies only — they describe the tape, not the next tick.",
                "Read from live ticks; nothing is placed.",
            ],
            "data": {"verdict": plan.get("verdict"), "best": plan.get("best")},
        }

    def _tape(self, low: str, symbol: str) -> dict:
        counts, n = live_digit_counts(symbol, 250)
        buffered = tick_queue.count(symbol)
        latest = tick_queue.latest(symbol)
        age = None
        if latest:
            try:
                ts = latest.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = round(
                    (datetime.now(timezone.utc) - ts).total_seconds(), 1)
            except Exception:  # noqa: BLE001
                age = None

        fresh = n >= 250 and (age is None or age <= 30)
        verdict = "FRESH" if fresh else "THIN" if n else "NO TAPE"
        text = (
            f"{symbol}: {n} live ticks in the 250-tick window, {buffered} buffered "
            f"in total. Latest tick {age}s ago. Tape is "
            + ("fresh — safe to measure on." if fresh else
               "thin — any edge computed on this is not trustworthy yet.")
        )
        return {
            "text": text,
            "topic": "tape",
            "grounded": n > 0,
            "limitations": [
                "Only live ticks count; synthetic ticks are excluded from every measurement.",
            ],
            "data": {"symbol": symbol, "live_ticks": n, "buffered": buffered,
                     "age_seconds": age, "verdict": verdict},
        }

    def _edges(self, symbol: str) -> dict:
        symbols = get_settings().active_symbols
        found = []
        for sym in symbols:
            try:
                for ctype, digit in sorted(truth_engine.proven_edges(sym)):
                    found.append({"symbol": sym, "contract": ctype, "digit": digit})
            except Exception as exc:  # noqa: BLE001
                logger.warning("edge scan failed for %s: %s", sym, exc)

        if not found:
            return {
                "text": (
                    f"I checked all {len(symbols)} markets and found NO proven edge. "
                    "An edge only counts if it survives the 100/300/1000-tick windows "
                    "simultaneously — a single window manufactures flukes. Nothing "
                    "qualifies right now, so the honest answer is: do not trade."
                ),
                "topic": "edges", "grounded": len(symbols) > 0,
                "limitations": [
                    "A market can carry a real skew that this gate cannot yet prove; "
                    "the gate errs toward refusing.",
                ],
                "data": {"checked": len(symbols), "proven": []},
            }

        lines = ", ".join(f"{f['symbol']} {f['contract']} {f['digit']}" for f in found[:10])
        return {
            "text": (
                f"{len(found)} proven edge(s) survived every window: {lines}. "
                "These held on the 100, 300 and 1000-tick windows at once."
            ),
            "topic": "edges", "grounded": True,
            "limitations": [
                "Past-digit frequencies only — a surviving skew is not a guarantee.",
                "Advisory: the platform places nothing.",
            ],
            "data": {"checked": len(symbols), "proven": found},
        }

    def _why_no_trade(self, low: str, symbol: str) -> dict:
        reasons = []
        g = risk_guard.status()
        if g.get("killed"):
            reasons.append(f"the kill switch is down ({g.get('kill_reason', 'manual stop')})")
        if real_trade_budget.report().get("locked"):
            reasons.append("the real-trade certification budget is exhausted and locked")

        symbols = get_settings().active_symbols
        provable = 0
        for sym in symbols:
            try:
                if truth_engine.proven_edges(sym):
                    provable += 1
            except Exception:  # noqa: BLE001
                continue
        if provable == 0:
            reasons.append(
                f"no market shows a proven edge across all windows (checked {len(symbols)})")

        counts, n = live_digit_counts(symbol, 250)
        if n < 250:
            reasons.append(f"the tape is thin on {symbol} ({n}/250 live ticks)")

        st = auto_trader.status()
        if st.get("benched"):
            reasons.append("the trader is benched to reset after consecutive losses")

        if not reasons:
            reasons.append("nothing is blocking — there is simply no positive-EV play")

        return {
            "text": "Nothing is firing because " + "; ".join(reasons)
                    + ". Not trading is a result, not a fault.",
            "topic": "no_trade", "grounded": True,
            "limitations": [
                "This lists the gates that refuse. It cannot prove an untested edge is absent.",
            ],
            "data": {"reasons": reasons, "guard": {"killed": g.get("killed")}},
        }

    def _safety(self) -> dict:
        g = risk_guard.status()
        b = real_trade_budget.report()
        s = get_settings()
        used = b.get("total_trades", 0)
        cap = b.get("max_allowed", 60)
        return {
            "text": (
                f"Adviser-only mode is {'ON' if getattr(s, 'cf_adviser_only', True) else 'OFF'}, "
                f"so no real order can be placed. Kill switch: "
                f"{'DOWN' if g.get('killed') else 'live'}. Real test trades used: "
                f"{used} of {cap}"
                + (" (LOCKED — needs a human reset)." if b.get("locked") else ".")
                + " Analysis never touches an account."
            ),
            "topic": "safety", "grounded": True,
            "limitations": ["These limits describe the platform, not your own risk tolerance."],
            "data": {"guard": {"killed": g.get("killed"), "kill_reason": g.get("kill_reason")},
                     "budget": {"used": used, "cap": cap, "locked": b.get("locked")},
                     "adviser_only": getattr(s, "cf_adviser_only", True)},
        }

    def _health(self) -> dict:
        mode = getattr(LIVE_STATE, "mode", "unknown")
        label = LIVE_STATE.to_dict().get("data_label")
        symbols = get_settings().active_symbols
        streaming = [s for s in symbols if tick_queue.count(s)]
        return {
            "text": (
                f"Mode: {mode}. Feed label: {label}. {len(streaming)}/{len(symbols)} "
                "markets are streaming live ticks. "
                "Postgres and Redis are unavailable on this host by design; "
                "persistence is JSON files."
            ),
            "topic": "health", "grounded": bool(streaming),
            "limitations": [
                "History is lost on restart on hosts with an ephemeral disk.",
            ],
            "data": {"mode": mode, "data_label": label,
                     "streaming": len(streaming), "configured": len(symbols)},
        }

    def _performance(self) -> dict:
        j = journal_engine.dashboard()
        b = virtual_bank.status()
        bank = (
            f"Bank: ${b['current_balance']:.2f} spendable, ${b['vault_balance']:.2f} protected."
            if b.get("synced") else "The virtual bank has not opened a session yet."
        )
        return {
            "text": (
                f"Today: {j['trades_today']} trades, {j['wins']}W/{j['losses']}L "
                f"({j['win_rate']}% win rate), net ${j['net_pnl']:+.2f}. {bank}"
            ),
            "topic": "performance", "grounded": True,
            "limitations": [
                "Counts only what was journaled; a small sample proves nothing on its own.",
            ],
            "data": {"journal": j, "bank": b},
        }

    def _help(self) -> dict:
        return {
            "text": "Ask me things like: " + " · ".join(SUGGESTIONS),
            "topic": "help", "grounded": False,
            "limitations": [],
            "data": {"suggestions": SUGGESTIONS},
        }


platform_chat = PlatformChat()
