"""The Treasurer, the Guard, and the Scout — the 101-idea upgrade.

Virtual bank (60/40 profit split), risk guard (kill switch, money limits,
streak halving, modes, tilt), table scout (chi-square, momentum, multi-window,
calibration, hot hours), and every new endpoint.
"""
import random

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import scout as scout_svc
from app.services.risk_guard import RiskGuard
from app.services.virtual_bank import VirtualBank

client = TestClient(app)


class TestVirtualBank:
    def test_profit_split_60_40(self):
        b = VirtualBank()
        b.sync_opening(100.0)
        split = b.record_pnl(10.0)
        assert split["to_vault"] == 6.0 and split["to_current"] == 4.0
        assert b.current == 104.0
        assert b.vault == 6.0
        assert b.total == 110.0

    def test_losses_never_touch_vault(self):
        b = VirtualBank()
        b.sync_opening(100.0)
        b.record_pnl(20.0)          # vault 12, current 108
        b.record_pnl(-150.0)        # catastrophic loss
        assert b.vault == 12.0      # vault untouched
        assert b.current == -42.0

    def test_stake_base_is_current_not_total(self):
        b = VirtualBank()
        b.sync_opening(100.0)
        b.record_pnl(10.0)
        # total is 110 but spendable is 104 — the CF can only re-risk current
        assert b.spendable() == 104.0
        assert b.total == 110.0

    def test_withdraw_and_deposit(self):
        b = VirtualBank()
        b.sync_opening(100.0)
        b.record_pnl(10.0)  # vault 6
        w = b.withdraw(100.0)  # capped at vault contents
        assert w["moved"] == 6.0 and b.vault == 0.0 and b.current == 110.0
        d = b.deposit(50.0)
        assert d["moved"] == 50.0 and b.vault == 50.0

    def test_split_ratio_adjustable_and_capped(self):
        b = VirtualBank()
        b.set_split(0.5)
        assert b.split_ratio == 0.5
        b.set_split(2.0)  # nonsense -> capped at 0.95
        assert b.split_ratio == 0.95

    def test_history_records_sweeps(self):
        b = VirtualBank()
        b.sync_opening(10.0)
        b.record_pnl(5.0)
        h = b.recent_history()
        assert any(e["kind"] == "sweep" for e in h)

    def test_status_shape(self):
        b = VirtualBank()
        b.sync_opening(50.0)
        s = b.status()
        for k in ("current_balance", "vault_balance", "total_balance",
                  "split_ratio", "spendable", "protected_pct"):
            assert k in s


class TestRiskGuard:
    def test_kill_switch_blocks_everything(self):
        g = RiskGuard()
        g.kill("test panic")
        v = g.check(0.0)
        assert any(x.startswith("KILL_SWITCH") for x in v)
        g.release()
        assert g.check(0.0) == []

    def test_daily_loss_limit_in_dollars(self):
        g = RiskGuard()
        g.set_limits(daily_loss_limit=20.0, session_take_profit=0, max_trades_per_hour=0)
        assert g.check(-19.0) == []
        assert any("DAILY_LOSS_LIMIT" in x for x in g.check(-20.0))
        g.set_limits(daily_loss_limit=0)

    def test_take_profit_banks_the_session(self):
        g = RiskGuard()
        g.set_limits(daily_loss_limit=0, session_take_profit=30.0, max_trades_per_hour=0)
        assert g.check(29.0) == []
        assert any("TAKE_PROFIT" in x for x in g.check(30.0))
        g.set_limits(session_take_profit=0)

    def test_max_trades_per_hour(self):
        g = RiskGuard()
        g.open_session(100.0)
        g.set_limits(max_trades_per_hour=3)
        for _ in range(3):
            g.record_trade(100.0)
        assert any("MAX_TRADES_PER_HOUR" in x for x in g.check(0.0))
        g.set_limits(max_trades_per_hour=0)

    def test_streak_halving(self):
        g = RiskGuard()
        g.streak_halving = True
        assert g.streak_multiplier(0) == 1.0
        assert g.streak_multiplier(1) == 0.5
        assert g.streak_multiplier(3) == 0.125
        g.streak_halving = False
        assert g.streak_multiplier(5) == 1.0

    def test_cooldown_escalator(self):
        g = RiskGuard()
        assert g.cooldown_escalator(30.0, 0) == 30.0
        assert g.cooldown_escalator(30.0, 1) == 60.0
        assert g.cooldown_escalator(30.0, 10) == 300.0  # capped

    def test_modes(self):
        g = RiskGuard()
        assert g.set_mode("COACH")["mode"] == "COACH"
        assert g.needs_approval()
        assert "error" in g.set_mode("NONSENSE")
        g.set_mode("FULL_MANUAL")
        assert any("FULL_MANUAL" in x for x in g.check(0.0))
        g.set_mode("FULL_AUTO")

    def test_coach_approval_lifecycle(self):
        g = RiskGuard()
        item = g.queue_approval({"name": "DIFFERS on 3"})
        assert g.next_pending()["id"] == item["id"]
        r = g.resolve_approval(item["id"], False)
        assert r["status"] == "rejected"
        assert g.next_pending() is None

    def test_tilt_detector(self):
        g = RiskGuard()
        g.open_session(100.0)
        for _ in range(3):
            g.record_trade(99.0, manual=True)
        assert g.tilt_warning("loss") is not None
        assert g.tilt_warning("win") is None


class TestScout:
    def test_chi_square_flags_skewed_table(self):
        rng = random.Random(7)
        fair = [rng.randrange(10) for _ in range(600)]
        assert scout_svc.chi_square_digit(fair)["level"] in ("fair", "skewed")
        skewed = [3] * 5 + [rng.randrange(10) for _ in range(595)]
        # digit 3 massively underfed vs fair 60 expected -> large chi2
        assert scout_svc.chi_square_digit(skewed)["chi2"] > 16.92

    def test_chi_square_insufficient_data(self):
        assert scout_svc.chi_square_digit([1, 2, 3])["level"] == "insufficient_data"

    def test_dirichlet_shrinks_to_fair(self):
        est = scout_svc.dirichlet_estimates([7] * 5)
        # tiny sample: even an all-7 window collapses toward 10%
        assert est["7"] < 60.0
        assert abs(sum(est.values()) - 100.0) < 0.5

    def test_momentum_rising(self):
        digits = [0] * 270 + [1] * 30 + [0] * 0
        # digit 1 absent for 270 then hot for 30 -> rising
        assert scout_svc.momentum_rising(digits + [0] * 0, 1, recent=30) is True
        assert scout_svc.momentum_rising([5] * 600, 5) is False

    def test_scan_tables_verdicts_on_empty(self):
        out = scout_svc.scan_tables(["NO_SUCH_SYMBOL"], window=100)
        assert out["tables"][0]["tradeable"] is False
        assert "summary" in out

    def test_heatmap_shape(self):
        out = scout_svc.heatmap(["R_100"], window=50)
        assert "z_scores" in out and "R_100" in out["z_scores"]
        assert len(out["z_scores"]["R_100"]) == 10

    def test_calibration_and_hot_hours_run(self):
        cal = scout_svc.calibration()
        assert "buckets" in cal and "verdict" in cal
        hours = scout_svc.performance_by_hour()
        assert "by_hour_utc" in hours and "current_window_ok" in hours

    def test_journal_breakdown(self):
        out = scout_svc.journal_breakdown()
        for k in ("by_symbol", "by_contract", "longest_win_streak", "longest_loss_streak"):
            assert k in out


class TestNewEndpoints:
    def test_bank_endpoints(self):
        s = client.get("/bank")
        assert s.status_code == 200
        body = s.json()
        assert "current_balance" in body and "vault_balance" in body
        assert client.get("/bank/history").status_code == 200
        r = client.post("/bank/deposit", json={"amount": 1.0})
        assert r.status_code == 200 and "moved" in r.json()
        r = client.post("/bank/withdraw", json={"amount": 1.0})
        assert r.status_code == 200
        r = client.post("/bank/split", json={"split_ratio": 0.6})
        assert r.json()["split_ratio"] == 0.6

    def test_guard_endpoints(self):
        assert client.get("/guard").status_code == 200
        r = client.post("/guard/kill", params={"reason": "test"})
        assert r.json()["killed"] is True
        assert client.get("/guard").json()["killed"] is True
        r = client.post("/guard/release")
        assert r.json()["killed"] is False
        r = client.post("/guard/mode", json={"mode": "COACH"})
        assert r.json()["mode"] == "COACH"
        client.post("/guard/mode", json={"mode": "FULL_AUTO"})
        r = client.post("/guard/limits", json={"daily_loss_limit": 15.0})
        assert r.json()["daily_loss_limit"] == 15.0
        client.post("/guard/limits", json={"daily_loss_limit": 0})
        assert client.get("/guard/approvals").status_code == 200

    def test_scout_endpoints(self):
        for path in ("/scout/tables", "/scout/heatmap", "/scout/calibration",
                     "/scout/hot-hours", "/scout/breakdown"):
            r = client.get(path)
            assert r.status_code == 200, path

    def test_kill_switch_blocks_manual_trade(self):
        client.post("/guard/kill", params={"reason": "test block"})
        r = client.post("/trade", json={"symbol": "R_100", "direction": "CALL", "amount": 1.0, "duration": 5})
        assert r.json().get("status") == "error"
        assert "KILL_SWITCH" in r.json().get("error", "")
        client.post("/guard/release")

    def test_trader_status_exposes_bank_and_guard(self):
        r = client.get("/auto-trader/status")
        assert r.status_code == 200
        s = r.json()
        assert "bank" in s and "guard" in s
        assert "stake_base" in s and "streak_multiplier" in s


class TestTraderBankIntegration:
    @pytest.mark.anyio
    async def test_place_trade_splits_profit_into_vault(self):
        from app.services.auto_trader import AutoTrader
        from app.services.virtual_bank import virtual_bank
        at = AutoTrader()
        at.mode = "paper"
        at._session_active = True
        at.balance = 10.0
        virtual_bank.sync_opening(10.0)
        contract = {
            "name": "DIFFERS on 3", "type": "DIFFERS", "digit": 3,
            "symbol": "R_100", "observed_edge": 5.0, "confidence": 90.0,
        }
        for _ in range(30):  # enough paper trades that SOME win lands
            await at.place_trade(contract, 1.0)
            if virtual_bank.vault > 0:
                break
        assert at.trades_today == 30 or virtual_bank.vault > 0
        # every win swept 60% to the vault; losses never touched it
        st = virtual_bank.status()
        assert st["vault_balance"] >= 0
        if st["total_profit"] > 0:
            assert st["vault_balance"] == pytest.approx(st["total_profit"] * 0.6, abs=0.02)
        at._session_active = False
