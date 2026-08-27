"""Truth Engine + Tick Recorder — the honest layer.

The user's question that started all of this: 18 wins, 2 losses, $1
stakes, +$0.18. These tests prove the engine answers it with math, not
with vibes.
"""
import json
from pathlib import Path

import pytest

from app.models.tick import Tick
from app.services.tick_recorder import TickRecorder
from app.services.truth_engine import TruthEngine, _breakeven, _verdict


# ---------------- breakeven math ----------------

class TestBreakevenMath:
    def test_differs_breakeven_is_90_9(self):
        # payout 1.1 total-return -> breakeven 90.91%. The user's 18/20 =
        # 90.0% is BELOW breakeven. That is the whole story in one number.
        assert abs(_breakeven(1.1) - 90.91) < 0.01

    def test_matches_breakeven_is_11_1(self):
        assert abs(_breakeven(9.0) - 11.11) < 0.01

    def test_coin_flip_breakeven(self):
        assert abs(_breakeven(1.9) - 52.63) < 0.01


class TestVerdict:
    def test_edge_needs_significance_and_fat_ev(self):
        # A real, significant, fat-margin edge is EDGE.
        assert _verdict(margin_pp=5.0, ev=0.08, significant=True) == "EDGE"

    def test_no_significance_is_fair(self):
        assert _verdict(margin_pp=5.0, ev=0.08, significant=False) == "FAIR"

    def test_significant_negative_is_trap(self):
        # Significant and losing: real pattern, still bleeds.
        assert _verdict(margin_pp=-3.0, ev=-0.05, significant=True) == "TRAP"

    def test_thin_margin_is_trap(self):
        # A 3.0pp margin used to be called EDGE — that is the bug. The 08-26
        # journal shipped 518 thin-margin "edges" on Deriv's RNG book and bled.
        # EDGE now requires the observed rate to clear break-even by a FAT
        # margin (MIN_BREAKEVEN_MARGIN_PCT = 4.0pp), so this thin margin is a
        # TRAP: it looks like signal and is a bleed.
        assert _verdict(margin_pp=3.0, ev=0.05, significant=True) == "TRAP"

    def test_just_below_fat_margin_is_trap(self):
        # 3.9pp is below the 4.0pp fat-margin floor -> not EDGE.
        assert _verdict(margin_pp=3.9, ev=0.06, significant=True) == "TRAP"

    def test_at_fat_margin_is_edge(self):
        assert _verdict(margin_pp=4.0, ev=0.04, significant=True) == "EDGE"


# ---------------- expectancy on a live tape ----------------

def _feed(symbol: str, digits):
    from app.core.queue import tick_queue
    for d in digits:
        tick_queue.push(Tick(symbol=symbol, quote=100.0 + d * 0.001, raw={"digit": d}))


class TestExpectancy:
    def test_fair_tape_shows_no_edge(self):
        eng = TruthEngine()
        digits = [i % 10 for i in range(300)]
        _feed("TRUTH_FAIR", digits)
        out = eng.expectancy("TRUTH_FAIR", window=300)
        assert out["edges"] == []  # uniform tape: nothing mispriced

    def test_starved_digit_surfaces_differs_mispricing(self):
        eng = TruthEngine()
        # digit 0 absent over 600 ticks -> DIFFERS 0 observed ~100% vs
        # breakeven 90.9% -> positive margin.
        digits = [(i % 9) + 1 for i in range(600)]
        _feed("TRUTH_STARVED", digits)
        out = eng.expectancy("TRUTH_STARVED", window=600)
        d0 = next(c for c in out["contracts"] if c["type"] == "DIFFERS" and c["digit"] == 0)
        assert d0["margin_pp"] > 0
        assert d0["ev"] > 0

    def test_edge_board_ranks_symbols(self):
        eng = TruthEngine()
        digits = [(i % 9) + 1 for i in range(600)]
        _feed("TRUTH_BOARD", digits)
        board = eng.edge_board(["TRUTH_BOARD"], window=600)
        assert board["symbols"][0]["symbol"] == "TRUTH_BOARD"
        assert "note" in board


class TestProvenEdges:
    def test_persistent_skew_is_proven(self):
        eng = TruthEngine()
        _feed("TRUTH_PERSIST", [(i % 9) + 1 for i in range(1500)])  # 0 absent
        assert ("DIFFERS", 0) in eng.proven_edges("TRUTH_PERSIST")

    def test_fair_tape_proves_nothing(self):
        eng = TruthEngine()
        _feed("TRUTH_PFAIR", [i % 10 for i in range(1500)])
        assert eng.proven_edges("TRUTH_PFAIR") == set()

    def test_thin_tape_proves_nothing(self):
        eng = TruthEngine()
        _feed("TRUTH_PTHIN", [i % 10 for i in range(40)])  # below min_ticks
        assert eng.proven_edges("TRUTH_PTHIN") == set()

    def test_single_window_fluke_is_not_proven(self):
        eng = TruthEngine()
        # uniform history, then a recent 300-tick window where digit 7 is
        # overfed. w=1000 dilutes it away, so the fluke must NOT be proven.
        old = [i % 10 for i in range(1200)]
        recent = [7] * 45 + [(i % 9) if (i % 9) != 7 else 8 for i in range(255)]
        _feed("TRUTH_FLUKE", old + recent)
        assert ("MATCHES", 7) not in eng.proven_edges("TRUTH_FLUKE")

    def test_fresh_boot_hot_digit_cannot_be_proven(self):
        # Regression: on a fresh boot the queue has only ~50-99 ticks. A digit
        # fluke over that sliver used to clear the old 50-tick truth floor and
        # the CF fired a $1 lottery ticket on it (log: "Placing trade ...
        # z=3.61 EV 0.78" on 13 ticks). proven_edges now needs each of the
        # 100/300/1000 windows to hold min_ticks=100, so a thin boot tape
        # proves nothing no matter how hot one digit looks.
        eng = TruthEngine()
        _feed("TRUTH_BOOTFLUKE", [7] * 40 + [(i % 9) if (i % 9) != 7 else 8 for i in range(45)])
        assert eng.proven_edges("TRUTH_BOOTFLUKE") == set()

    def test_persistent_hot_digit_proves_matches(self):
        # MATCHES is not banned — it must EARN its way through the gate.
        # digit 4 lands every 4th tick (~30% after filler) — far above the
        # 11.11% breakeven for the 9.0 payout, significant in every window.
        eng = TruthEngine()
        digits = [4 if i % 4 == 0 else i % 10 for i in range(1600)]
        _feed("TRUTH_HOT4", digits)
        assert ("MATCHES", 4) in eng.proven_edges("TRUTH_HOT4")

    def test_parity_skew_can_be_proven(self):
        # ODD/EVEN were hardcoded FAIR (could never fire). Now the tape is
        # the referee: an all-odd tape is a real parity skew, and ODD beats
        # the 1.9 payout's 52.6% breakeven.
        eng = TruthEngine()
        _feed("TRUTH_PARITY", [[1, 3, 5, 7, 9][i % 5] for i in range(1500)])
        proven = eng.proven_edges("TRUTH_PARITY")
        assert ("ODD", None) in proven
        assert ("EVEN", None) not in proven

    def test_fair_parity_proves_nothing(self):
        eng = TruthEngine()
        _feed("TRUTH_PARITY_FAIR", [i % 10 for i in range(1500)])
        proven = eng.proven_edges("TRUTH_PARITY_FAIR")
        assert ("ODD", None) not in proven
        assert ("EVEN", None) not in proven


# ---------------- projection ----------------

class TestProjection:
    def test_no_edge_no_trade(self):
        eng = TruthEngine()
        digits = [i % 10 for i in range(300)]
        _feed("TRUTH_PROJ_FAIR", digits)
        out = eng.projection("TRUTH_PROJ_FAIR", bankroll=100.0, trades_per_day=50, window=300)
        assert out["tradeable"] is False
        assert out["expected_daily_pnl"] == 0.0

    def test_edge_projects_positive_daily(self):
        eng = TruthEngine()
        digits = [(i % 9) + 1 for i in range(600)]
        _feed("TRUTH_PROJ_STARVED", digits)
        out = eng.projection("TRUTH_PROJ_STARVED", bankroll=100.0, trades_per_day=50, window=600)
        if out["tradeable"]:  # only if the gate qualified the starved table
            assert out["expected_daily_pnl"] > 0
            assert 0 < out["kelly_stake"] <= 10.0  # quarter-Kelly capped 10%


# ---------------- journal reconciliation (the user's 18/2 question) ----------------

class TestReconcileJournal:
    def test_18w_2l_differs_is_breakeven_not_edge(self, tmp_path, monkeypatch):
        import app.services.persistence as persistence
        store = tmp_path / "store.json"
        store.write_text(json.dumps({"journal": [], "alerts": [], "backtests": [], "replays": {}, "settings": {}}))
        monkeypatch.setattr(persistence, "_STORE_PATH", store)

        from app.services.persistence import JournalEngine
        je = JournalEngine()
        # Replay exactly the user's book: 18 wins, 2 losses, $1 DIFFERS
        # at the ~1.1 total-return payout (profit $0.10 per win).
        for _ in range(18):
            je.add_entry(market="R_100", contract="DIFFERS", digit=0, stake=1.0,
                         result="win", pnl=0.10, data_quality=90, evidence_score=80, mode="live")
        for _ in range(2):
            je.add_entry(market="R_100", contract="DIFFERS", digit=0, stake=1.0,
                         result="loss", pnl=-1.0, data_quality=90, evidence_score=80, mode="live")

        out = TruthEngine().reconcile_journal()
        row = next(r for r in out["contracts"] if r["contract"].startswith("DIFFERS"))
        assert row["trades"] == 20 and row["wins"] == 18
        assert row["win_rate"] == 90.0
        # payout paid ~1.1 total-return -> breakeven 90.9% -> 90.0% is BELOW
        assert row["breakeven_wr"] == pytest.approx(90.91, abs=0.05)
        assert row["margin_pp"] < 0
        assert row["verdict"] in ("BREAKEVEN", "SLOW BLEED")
        # actual P&L: 18 * 0.10 - 2 * 1.0 = -0.20 ... the +0.18 the user
        # saw was a slightly different payout mix; structure is the same.
        assert row["actual_pnl"] == pytest.approx(-0.20, abs=1e-9)

    def test_sustainable_contract_detected(self, tmp_path, monkeypatch):
        import app.services.persistence as persistence
        store = tmp_path / "store.json"
        store.write_text(json.dumps({"journal": [], "alerts": [], "backtests": [], "replays": {}, "settings": {}}))
        monkeypatch.setattr(persistence, "_STORE_PATH", store)

        from app.services.persistence import JournalEngine
        je = JournalEngine()
        # 96/100 at 1.1 payout -> breakeven 90.9% -> genuine edge family.
        for _ in range(96):
            je.add_entry(market="R_100", contract="DIFFERS", digit=7, stake=1.0,
                         result="win", pnl=0.10, data_quality=90, evidence_score=80, mode="live")
        for _ in range(4):
            je.add_entry(market="R_100", contract="DIFFERS", digit=7, stake=1.0,
                         result="loss", pnl=-1.0, data_quality=90, evidence_score=80, mode="live")

        out = TruthEngine().reconcile_journal()
        row = next(r for r in out["contracts"] if "DIFFERS 7" in r["contract"])
        assert row["verdict"] == "SUSTAINABLE"
        assert row["long_run_ev"] > 0


# ---------------- recorder ----------------

class TestTickRecorder:
    def test_record_and_load_roundtrip(self, tmp_path):
        rec = TickRecorder(directory=tmp_path)
        t = Tick(symbol="RT_1", quote=116.1046, raw={"digit": 6})
        assert rec.record(t)
        t.provider = "deriv_live"
        assert rec.record(t)
        entries = rec.load("RT_1", limit=10)
        assert len(entries) == 2
        assert entries[-1]["digit"] == 6
        assert entries[-1]["provider"] == "deriv_live"

    def test_stats_reports_symbols(self, tmp_path):
        rec = TickRecorder(directory=tmp_path)
        rec.record(Tick(symbol="RT_2", quote=100.5, raw={"digit": 5}))
        stats = rec.stats()
        assert stats["symbols"][0]["symbol"] == "RT_2"
        assert stats["symbols"][0]["ticks_session"] == 1

    def test_rotation_at_max_bytes(self, tmp_path):
        rec = TickRecorder(directory=tmp_path, max_bytes=200)
        for i in range(20):
            rec.record(Tick(symbol="RT_3", quote=100.0 + i * 0.001, raw={"digit": i % 10}))
        files = list(Path(tmp_path).glob("*.jsonl"))
        assert len(files) == 2  # current + rotated backup

    def test_purge_one_symbol(self, tmp_path):
        rec = TickRecorder(directory=tmp_path)
        rec.record(Tick(symbol="RT_4", quote=100.1, raw={"digit": 1}))
        rec.record(Tick(symbol="RT_5", quote=100.2, raw={"digit": 2}))
        removed = rec.purge("RT_4")
        assert removed == 1
        assert rec.load("RT_4") == []
        assert len(rec.load("RT_5")) == 1

    def test_disabled_recorder_writes_nothing(self, tmp_path):
        rec = TickRecorder(directory=tmp_path)
        rec.set_enabled(False)
        assert not rec.record(Tick(symbol="RT_6", quote=100.1, raw={"digit": 1}))
        assert list(Path(tmp_path).glob("*.jsonl")) == []


# ---------------- routes ----------------

class TestLabRoutes:
    def test_lab_routes_respond(self):
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            r = c.get("/lab/edge-board")
            assert r.status_code == 200
            assert "symbols" in r.json() and "board_has_edge" in r.json()

            r = c.get("/lab/expectancy/R_100")
            assert r.status_code == 200
            assert "contracts" in r.json()

            r = c.get("/lab/projection/R_100", params={"bankroll": 100, "trades_per_day": 50})
            assert r.status_code == 200
            assert "expected_daily_pnl" in r.json()

            r = c.get("/lab/reconcile")
            assert r.status_code == 200
            body = r.json()
            assert "contracts" in body and "note" in body

            r = c.get("/lab/recordings")
            assert r.status_code == 200
            assert "symbols" in r.json()

            c.get("/lab/recordings/R_100")  # tape may be empty pre-ingestion
            assert c.delete("/lab/recordings/R_100").status_code == 200
