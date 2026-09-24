"""Platform chat — the "ask the platform anything" surface.

The tests care about two things beyond "does it answer": that it refuses to
pretend (an edge question on a fair tape must come back empty rather than
invent one), and that it never 500s, because a chat that breaks on a weird
question is worse than no chat.

Hermeticity note: `live_digit_counts` prefers the on-disk tape and only then
the in-memory queue, and `get_settings().active_symbols` is the real market
list. Both would leak recorded production ticks and live symbols into these
tests, so every test here drives a FAKE symbol ("CHATX") that has no disk file
and stubs `active_symbols` down to it.
"""
import pytest
from datetime import datetime, timezone

from app.core.queue import tick_queue
from app.models.tick import Tick
from app.services import chat as chat_mod
from app.services.chat import PlatformChat, SUGGESTIONS

SYM = "CHATX"


class _FakeSettings:
    active_symbols = [SYM]
    cf_adviser_only = True


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No disk tape, no real symbols, no leftover ticks or history.

    History persists through the JSON store, so a conversation id reused across
    runs would carry stale turns into a fresh assertion. Every id used here is
    cleared on both sides of the test.
    """
    ids = ("t1", "a", "b", "n", "z", "persist-check", "route-t",
           "cap-check", "reset-check", "flat-check", "shape-check", "lim-check")
    c = PlatformChat()
    for cid in ids:
        c.clear(cid)
    tick_queue.clear()
    monkeypatch.setattr(chat_mod, "get_settings", lambda: _FakeSettings())
    yield
    for cid in ids:
        c.clear(cid)
    tick_queue.clear()


def _push(digits, symbol=SYM):
    for d in digits:
        tick_queue.push(Tick(
            symbol=symbol, quote=1000 + d, provider="deriv_live",
            timestamp=datetime.now(timezone.utc), raw={"digit": d, "pip_size": 2}))


def _flat(n=400):
    return [(i % 10) for i in range(n)]


def _skewed(n=1200):
    return [7 if i % 10 < 6 else 3 for i in range(n)]


class TestChatBasics:
    def test_empty_message_is_refused_not_answered(self):
        out = PlatformChat().ask("")
        assert "error" in out

    def test_returns_the_contract_shape(self):
        _push(_flat())
        out = PlatformChat().ask("how fresh is the tape")
        for key in ("answer", "topic", "grounded", "limitations", "conversation_id",
                    "turn_id", "timestamp", "exchanges"):
            assert key in out

    def test_a_weird_question_does_not_raise(self):
        _push(_flat())
        out = PlatformChat().ask("!!! ??? \x00 nonsense")
        assert "answer" in out

    def test_help_lists_suggestions(self):
        out = PlatformChat().ask("what can you do")
        assert out["topic"] == "help"
        assert len(SUGGESTIONS) >= 4

    def test_every_reply_carries_limitations_or_topic(self):
        _push(_flat())
        for q in ["how fresh is the tape", "help", "how am i doing"]:
            out = PlatformChat().ask(q)
            assert out["topic"]
            assert isinstance(out["limitations"], list)


class TestConversation:
    def test_history_records_both_sides(self):
        _push(_flat())
        c = PlatformChat()
        c.ask("how fresh is the tape", conversation_id="t1")
        hist = c.history("t1")
        assert [h["role"] for h in hist] == ["you", "platform"]
        assert hist[0]["text"] == "how fresh is the tape"
        c.clear("t1")

    def test_conversations_are_isolated(self):
        _push(_flat())
        c = PlatformChat()
        c.ask("how fresh is the tape", conversation_id="a")
        assert c.history("b") == []
        c.clear("a")

    def test_exchange_counter_grows(self):
        _push(_flat())
        c = PlatformChat()
        one = c.ask("help", conversation_id="n")
        two = c.ask("help", conversation_id="n")
        assert two["exchanges"] > one["exchanges"]
        c.clear("n")

    def test_clear_empties_history(self):
        _push(_flat())
        c = PlatformChat()
        c.ask("help", conversation_id="z")
        c.clear("z")
        assert c.history("z") == []

    def test_history_survives_a_new_instance(self):
        """History is persisted, so a fresh object still sees it."""
        _push(_flat())
        PlatformChat().ask("help", conversation_id="persist-check")
        assert len(PlatformChat().history("persist-check")) == 2
        PlatformChat().clear("persist-check")

    def test_exchange_count_keeps_growing_past_the_history_cap(self):
        """Regression: the count was derived from len(history), which is
        truncated at MAX_TURNS, so it froze at the cap instead of reporting a
        long conversation. It must stay monotonic past the cap."""
        c = PlatformChat()
        cid = "cap-check"
        c.clear(cid)
        for _ in range(chat_mod.MAX_TURNS + 3):
            out = c.ask("help", conversation_id=cid)
        assert out["exchanges"] == chat_mod.MAX_TURNS + 3
        assert out["exchanges"] > chat_mod.MAX_TURNS
        # History is still bounded, so the counter is genuinely separate.
        assert len(c.history(cid, limit=99999)) <= chat_mod.MAX_TURNS
        c.clear(cid)

    def test_clear_resets_the_exchange_count(self):
        c = PlatformChat()
        cid = "reset-check"
        c.clear(cid)
        c.ask("help", conversation_id=cid)
        c.clear(cid)
        assert c.exchanges(cid) == 0

    def test_history_flattens_reply_metadata(self):
        """Regression: topic/grounded were stored under "meta" but the UI reads
        them at the top level, so every restored reply rendered as
        'not measured' even when it had been measured."""
        _push(_flat())
        c = PlatformChat()
        cid = "flat-check"
        c.clear(cid)
        c.ask("how fresh is the tape", conversation_id=cid, symbol=SYM)
        reply = c.history(cid)[1]
        assert reply["role"] == "platform"
        assert reply.get("topic") == "tape"
        assert reply.get("grounded") is True
        assert "meta" not in reply
        c.clear(cid)

    def test_history_and_ask_agree_on_metadata(self):
        """A restored reply must expose the same topic/grounded as the live one,
        because that is what the UI's 'measured' marker is driven by. Content
        lives under `answer` on a fresh reply and `text` in the stored turn;
        the UI reads both, so the test asserts reachability rather than
        pretending the two keys are the same."""
        _push(_flat())
        c = PlatformChat()
        cid = "shape-check"
        c.clear(cid)
        live = c.ask("how fresh is the tape", conversation_id=cid, symbol=SYM)
        restored = c.history(cid)[1]
        assert live["topic"] == restored["topic"]
        assert live["grounded"] == restored["grounded"]
        assert live["answer"] and restored["text"]
        assert live["answer"] == restored["text"]
        c.clear(cid)


    def test_history_keeps_limitations(self):
        """Limitations were stored nowhere, so a restored reply silently lost
        its caveats - the honesty layer vanished on reload."""
        _push(_flat())
        c = PlatformChat()
        cid = "lim-check"
        c.clear(cid)
        live = c.ask("how fresh is the tape", conversation_id=cid, symbol=SYM)
        restored = c.history(cid)[1]
        assert live["limitations"]
        assert restored.get("limitations") == live["limitations"]
        c.clear(cid)


class TestTapeTopic:
    def test_thin_tape_is_called_thin(self):
        _push(_flat(20))
        out = PlatformChat().ask("is the tape fresh", symbol=SYM)
        assert out["topic"] == "tape"
        assert out["data"]["verdict"] != "FRESH"
        assert "thin" in out["answer"].lower()

    def test_deep_tape_is_called_fresh(self):
        _push(_flat(400))
        out = PlatformChat().ask("is the tape fresh", symbol=SYM)
        assert out["data"]["live_ticks"] >= 250
        assert out["data"]["verdict"] == "FRESH"

    def test_no_tape_at_all_is_reported_honestly(self):
        out = PlatformChat().ask("is the tape fresh", symbol=SYM)
        assert out["data"]["verdict"] == "NO TAPE"
        assert out["grounded"] is False

    def test_age_is_never_rendered_as_none(self):
        """Regression: with an empty queue the age was None and the text read
        'Latest tick Nones ago'. It must read as unavailable, never as None."""
        _push(_flat(1))
        out = PlatformChat().ask("is the tape fresh", symbol=SYM)
        assert "Nones" not in out["answer"]
        assert "None" not in out["answer"]

    def test_disk_age_reads_the_ts_key(self, monkeypatch):
        """The recorded entry names its time `ts`, not `timestamp`; reading the
        wrong key silently produced an age of None."""
        written = datetime.now(timezone.utc).isoformat()
        monkeypatch.setattr(chat_mod.tick_recorder, "load",
                            lambda s, limit=1: [{"ts": written, "digit": 4}])
        age = chat_mod._disk_age(SYM)
        assert age is not None and age >= 0

    def test_disk_age_is_none_without_a_tape(self, monkeypatch):
        monkeypatch.setattr(chat_mod.tick_recorder, "load",
                            lambda s, limit=1: [])
        assert chat_mod._disk_age(SYM) is None


class TestEdgeHonesty:
    def test_fair_tape_yields_no_proven_edge(self):
        """The load-bearing honesty test: a flat tape must NOT manufacture an
        edge. This is the bug class that produced the phantom-edge session."""
        _push(_flat(1200))
        out = PlatformChat().ask("is any market showing a real edge right now")
        assert out["topic"] == "edges"
        assert out["data"]["proven"] == []
        assert "no proven edge" in out["answer"].lower()

    def test_edge_scan_reports_the_markets_it_checked(self):
        out = PlatformChat().ask("any edge worth trading")
        assert out["data"]["checked"] == 1

    def test_persistent_skew_is_never_invented(self):
        """A strong skew may be proven or absent, but must never crash and
        must never be reported on a symbol that was not checked."""
        _push(_skewed())
        out = PlatformChat().ask("is there an edge")
        assert out["topic"] == "edges"
        assert isinstance(out["data"]["proven"], list)
        for f in out["data"]["proven"]:
            assert f["symbol"] == SYM


class TestNoTradeTopic:
    def test_explains_why_nothing_fires(self):
        _push(_flat(400))
        out = PlatformChat().ask("why is the platform not trading", symbol=SYM)
        assert out["topic"] == "no_trade"
        assert out["data"]["reasons"]

    def test_says_the_tape_is_thin_when_it_is(self):
        _push(_flat(10))
        out = PlatformChat().ask("why no trade", symbol=SYM)
        joined = " ".join(out["data"]["reasons"]).lower()
        assert "thin" in joined

    def test_reports_no_edge_when_none_is_proven(self):
        _push(_flat(400))
        out = PlatformChat().ask("why not trading", symbol=SYM)
        joined = " ".join(out["data"]["reasons"]).lower()
        assert "no market shows a proven edge" in joined


class TestSafetyTopic:
    def test_safety_reports_adviser_only(self):
        out = PlatformChat().ask("what are the safety limits")
        assert out["topic"] == "safety"
        assert out["data"]["adviser_only"] is True

    def test_safety_exposes_budget_without_secrets(self):
        out = PlatformChat().ask("am i protected")
        blob = str(out).lower()
        for leak in ("token", "password", "secret", "api_key", "otp"):
            assert leak not in blob

    def test_budget_uses_real_cap_names(self):
        out = PlatformChat().ask("safety limits")
        assert out["data"]["budget"]["cap"] == 60


class TestPerformanceTopic:
    def test_performance_answers_without_a_session(self):
        out = PlatformChat().ask("how am i doing today")
        assert out["topic"] == "performance"
        assert "trades" in out["answer"].lower()

    def test_nothing_opens_a_session(self):
        before = PlatformChat().ask("how am i doing")["data"]["bank"].get("synced")
        after = PlatformChat().ask("how am i doing")["data"]["bank"].get("synced")
        assert before == after


class TestProbabilityViaChat:
    def test_measures_a_named_market(self):
        _push(_flat(400))
        out = PlatformChat().ask(f"what is the probability of over 5 on {SYM}")
        assert out["topic"] == "probability"

    def test_asks_for_a_barrier_instead_of_guessing(self):
        _push(_flat(400))
        out = PlatformChat().ask("what is the probability")
        assert out["topic"] == "probability"
        assert out["grounded"] is False
        assert "barrier" in out["answer"].lower()


class TestRoutes:
    def test_ask_route(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            res = c.post("/chat/ask", json={"message": "how fresh is the tape"})
        assert res.status_code == 200
        assert "answer" in res.json()

    def test_suggestions_route(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            res = c.get("/chat/suggestions")
        assert res.status_code == 200
        assert len(res.json()["suggestions"]) >= 4

    def test_history_and_clear_routes(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            c.post("/chat/ask", json={"message": "help", "conversation_id": "route-t"})
            hist = c.get("/chat/history", params={"conversation_id": "route-t"}).json()
            assert len(hist["messages"]) >= 2
            c.post("/chat/clear", params={"conversation_id": "route-t"})
            after = c.get("/chat/history", params={"conversation_id": "route-t"}).json()
            assert after["messages"] == []

    def test_route_rejects_missing_message(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            res = c.post("/chat/ask", json={})
        assert res.status_code == 422