"""Auto Trader — the CF. He never picks the play; the whole squad does.

Market Master (the team meeting) ranks every contract on every market and
stamps a verdict on each: CB's signal, LB's data quality, the analysts'
95% z-significance, the scouts' edge/EV floors, the physio room's anomaly
check. The CF receives the team's top-rated PLAY verdicts and his only job
is to put the ball in the net. GK sizes the stake. The Manager benches him
when his form drops.

Paper mode simulates trades at fair odds; live mode executes via
DerivTrader and waits for the REAL settlement before touching the books.
Paper and live logs are clearly separated; every trade is journaled.
"""
import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.core.queue import tick_queue
from app.services.deriv_trader import deriv_trader
from app.services.market_master import (
    MIN_DIFFERS_EV,
    MIN_EDGE_PCT,
    MIN_EV,
    MIN_Z_AGE_S,
    PAYOUTS,
    market_master,
)
from app.services import scout as scout_svc
from app.services.money_management import (
    check_hard_stops,
    compute_stake,
    cooldown_for,
    drawdown_multiplier,
    kelly_stake,
    risk_state,
)
from app.services.persistence import journal_engine
from app.services.risk_guard import risk_guard
from app.services.scout import performance_by_hour
from app.services.telegram import telegram_notifier
from app.services.token_vault import VAULT
from app.services.virtual_bank import virtual_bank

FLUID_MAX_PLAYS = 2          # at most two simultaneous positions
FLUID_PAIR_RATIO = 0.75      # second play must reach 75% of the top's EV
MAX_GAMES_WITHOUT_GOAL = 2   # Pep's rule: lose 2 straight → pause, don't chase
BENCH_GAMES = 3              # scans he must sit out while the team regroups
TIGHT_CONFIRM_TICKS = 3      # after a benching: one extra confirmation tick
TIGHT_MIN_Z = 2.5            # after a benching: stronger proof than the 1.96 floor
MAX_ANOMALIES = 3            # physio room: too many anomalies, nobody plays
DECISION_HISTORY_LEN = 20    # recent team decisions surfaced in status
STRIKE_ROTATION_S = 30.0     # don't repeat the exact same strike for 30s


PHEV_MIN_Z = 2.8             # PHEV: stronger proof than the standard 1.96
PHEV_MIN_EV = 0.05           # PHEV: minimum +5% real expected value
PHEV_MIN_CHI_N = 300         # PHEV: chi-square gate always on (deep window)


def _loss_set(contract: dict) -> set:
    """The digits this contract LOSES on. Two contracts whose loss sets
    overlap are not a pair — they're double exposure to the same digit.
    (2026-08-24: DIFFERS 0 + OVER 1 both lose on digit 0; one bad tick
    cost two full stakes. Never again.)"""
    t = contract.get("type")
    d = contract.get("digit")
    if d is None:
        # Names carry the digit: "DIFFERS on 3", "OVER 1", "MATCHES on 6".
        parts = (contract.get("name") or "").split()
        tail = parts[-1] if parts else ""
        d = int(tail) if tail.isdigit() else None
    if t == "DIFFERS":
        return {d}
    if t == "MATCHES":
        return set(range(10)) - {d}
    if t == "OVER":
        return set(range(0, d + 1)) if d is not None else set(range(10))
    if t == "UNDER":
        return set(range(d, 10)) if d is not None else set(range(10))
    if t == "ODD":
        return {0, 2, 4, 6, 8}
    if t == "EVEN":
        return {1, 3, 5, 7, 9}
    return set(range(10))


def _correlated(a: dict, b: dict) -> bool:
    """True when both contracts can lose on the same digit."""
    return bool(_loss_set(a) & _loss_set(b))


def select_plays(mm: dict, symbol: str) -> list[dict]:
    """The team picks the play; the CF just finishes it.

    Every contract type is eligible — MATCHES, DIFFERS, ODD, EVEN, OVER,
    UNDER — because the decision belongs to the whole squad, not to the
    striker. The gates below ARE the team: CB's signal, LB's data quality,
    the physio's anomaly count, the analysts' 95% z-significance, and the
    scouts' edge/EV floors. Whatever survives, ranked by EV, is the call.

    PRECISION LAYER (the 8W/2L upgrade): surviving the vote is not enough
    to be FIRED. The CF now rejects entries he would regret — a starving
    digit is only a strike if the table is skewed (chi-square), the skew
    survives every window (100/300/1000), the edge isn't evaporating
    (momentum), and it has HELD for a while (z-age). Fewer entries,
    cleaner entries.
    """
    dq = mm.get("data_quality", 0) or 0
    sig = mm.get("signal", "") or ""
    anomalies = mm.get("anomaly_count", 0) or 0
    if "STRONG" not in sig or dq < 70 or anomalies > MAX_ANOMALIES:
        return []
    contracts = sorted(
        mm.get("all_contracts") or mm.get("contracts") or [],
        key=lambda c: (c.get("type") == "DIFFERS", c.get("ev", -1)), reverse=True,
    )
    phev = risk_guard.mode == "PHEV"
    hev = risk_guard.mode == "HEV"
    parity = risk_guard.mode == "PARITY"
    plays: list[dict] = []
    for c in contracts:
        ev = c.get("ev", -1)
        edge = c.get("observed_edge", 0.0) or 0.0
        if parity and c.get("type") == "DIFFERS":
            continue  # PARITY: no DIFFERS — only OVER/UNDER/ODD/EVEN/MATCHES
        if hev:
            # HEV: the speed bot. Only the 95% significance gate and a real
            # positive EV stand between the market and the trigger — no z
            # floor, no EV floor, no chi-square, no rotation, no benching.
            if not c.get("significant", False):
                continue
            if ev <= 0:
                continue
            if "SUPPORT" not in (c.get("evidence") or ""):
                continue
            if plays and _correlated(plays[0], c):
                continue  # never pair two contracts that lose on the same digit
            plays.append({**c, "symbol": symbol, "data_quality": dq, "signal": sig})
            if len(plays) >= FLUID_MAX_PLAYS:
                break
            continue
        if phev:
            # PHEV gates: stronger proof and fatter real EV than the standard
            # squad. The engine only runs when the market is charging.
            if abs(c.get("z") or 0.0) < PHEV_MIN_Z:
                continue
            if ev < PHEV_MIN_EV:
                continue
        if ev <= MIN_EV or edge < MIN_EDGE_PCT:
            continue  # scouts: no positive expectation, no meaningful edge
        if not c.get("significant", False):
            continue  # analysts: deviation not proven at the 95% level
        if "SUPPORT" not in (c.get("evidence") or ""):
            continue  # CB: data does not support the direction
        # Entry quality bar: a DIFFERS strike must carry real expectancy.
        # EV +0.005 means win 10 in a row and a single miss eats it all.
        if c.get("type") == "DIFFERS" and ev < MIN_DIFFERS_EV:
            continue
        if plays and c.get("ev", 0) < plays[0].get("ev", 0) * FLUID_PAIR_RATIO:
            continue  # coach: second play must be nearly as good as the first
        if plays and _correlated(plays[0], c):
            continue  # never pair two contracts that lose on the same digit
        plays.append({**c, "symbol": symbol, "data_quality": dq, "signal": sig})
        if len(plays) >= (1 if phev else FLUID_MAX_PLAYS):  # PHEV: single strike
            break
    if not plays:
        return []

    # Strike rotation: the exact same strike never repeats within 30s.
    # HEV skips this — the speed bot fires the same angle as fast as it appears.
    if not hev:
        now = time.time()
        fresh = [p for p in plays if now - _strike_last_fired.get(p["name"], 0) >= STRIKE_ROTATION_S]
        if not fresh:
            return []  # every approved strike is on cooldown — wait for a new angle
        # The cooled-down lead drops out; the next fresh angle leads. This is
        # how MATCHES/OVER/UNDER get to fire when DIFFERS is on cooldown.
        plays = fresh

    # The precision gate: the top play must survive the table-level checks.
    # HEV skips this — the speed bot trusts the 95% significance gate alone.
    top = plays[0]
    if not hev and top.get("type") == "DIFFERS" and top.get("digit") is not None:
        table = next(
            (t for t in scout_svc.cached_scan([symbol]).get("tables", [])
             if t["symbol"] == symbol),
            None,
        )
        if table is not None:
            chi = table.get("chi2") or {}
            # chi-square needs a deep window to speak. Standard: only enforced
            # when n >= 300. PHEV: always enforced — the engine only runs on a
            # provably skewed table.
            if chi.get("n", 0) >= (PHEV_MIN_CHI_N if phev else 300) and not chi.get("skewed", False):
                return []  # one hot digit on a provably fair table is a mirage
            if phev and not chi.get("skewed", False):
                return []  # PHEV: no skew, no engine — even on shallow data
            mw = table.get("multi_window") or {}
            if mw.get("digit") == top["digit"] and not mw.get("confirmed", False):
                return []  # hot on the short window only — not a story
            if table.get("momentum_rising"):
                return []  # the edge is evaporating under your feet
            # z-age: the edge must have HELD, not appeared this second. Derive
            # it from the tick stream itself — split the cached ticks in half
            # and require the digit was ALREADY significant in the older half.
            ticks = tick_queue.recent(symbol, limit=1000)
            digits = [t.digit for t in ticks]
            if len(digits) >= 200:
                half = len(digits) // 2
                from app.services.scout import _digit_z
                z_older = _digit_z(digits[:half]).get(top["digit"], 0.0)
                if z_older > -1.96:
                    return []  # edge is only recent — let it prove it lasts
            elif scout_svc.z_age_s(symbol, top["digit"]) < MIN_Z_AGE_S:
                return []  # shallow cache: fall back to wall-clock age
    return plays


_strike_last_fired: dict[str, float] = {}


def mark_strike_fired(plays: list[dict]) -> None:
    """Record when each strike last fired so rotation can enforce variety."""
    now = time.time()
    for p in plays:
        _strike_last_fired[p["name"]] = now


class AutoTrader:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.running = False
        self.mode = "paper"
        self.initial_balance = 10.0
        self.balance = 10.0
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self.session_started: Optional[float] = None
        self.last_trade: Optional[dict] = None
        self.current_recommendation: Optional[dict] = None
        self.confirmation_ticks = 0
        self._last_conf_key = ""
        self.log: list[str] = []
        self._task: Optional[asyncio.Task] = None
        self._paper_rng = random.Random(20240821)
        self.phase = "matchday"
        self._last_scan_log = 0.0
        self.benched_until = 0
        self.benched = False
        self.tight_marking = False  # Pep's regroup: higher bar after a benching
        self._scan_count = 0
        self.decision_history: list[dict] = []
        self._session_active = False       # bank/guard hooks only fire in real sessions
        self._coach_pending_id: Optional[str] = None
        self._hot_hours_cache: tuple[float, Optional[bool]] = (0.0, None)
        self._matchday: Optional[str] = None
        self.no_trade_reasons: dict[str, str] = {}
        self.counters: dict[str, int] = {"scans": 0, "trades": 0, "aborts": 0, "gate_blocks": 0}
        self._adaptive_z: float = 1.96
        self._adaptive_z_checked: float = 0.0
        self._last_reconcile: float = 0.0
        # Shadow scoreboard: what the benched CF WOULD have done, settled
        # against the real tick stream. Proof the benching saves money.
        self._pending_shadow: list[dict] = []
        self.shadow_stats: dict[str, float] = {"wins": 0, "losses": 0, "pnl": 0.0}

    def _stake_base(self) -> float:
        """Stakes are sized off the bank's SPENDABLE balance when the bank
        is synced — profits swept to the vault never get re-risked."""
        if self._session_active and virtual_bank.synced:
            return virtual_bank.spendable()
        return self.balance

    def _phev_stake(self, base: float) -> float:
        """PHEV compounding: stake grows with session profit, but the base
        is always the session's opening balance — never the house's money.
        After a win the next stake rises; after a loss it snaps back to base."""
        if risk_guard.mode != "PHEV" or not self._session_active:
            return base
        # stake = opening + 40% of session profit (the part that stays spendable)
        spendable_profit = max(0.0, self.daily_pnl * 0.4)
        return self.initial_balance + spendable_profit

    def _current_window_ok(self) -> Optional[bool]:
        """Hot-hours filter, cached for 5 minutes. None = not enough data."""
        now = time.time()
        ts, cached = self._hot_hours_cache
        if now - ts < 300:
            return cached
        try:
            ok = performance_by_hour().get("current_window_ok")
        except Exception:  # noqa: BLE001
            ok = None
        self._hot_hours_cache = (now, ok)
        return ok

    def _rollover_matchday(self) -> None:
        """A new UTC day = a new matchday: daily counters reset, form keeps."""
        today = datetime.now(timezone.utc).date().isoformat()
        if self._matchday is None:
            self._matchday = today
            return
        if today != self._matchday:
            self._matchday = today
            self.trades_today = 0
            self.wins_today = 0
            self.losses_today = 0
            self.daily_pnl = 0.0
            self._log("NEW MATCHDAY — daily counters reset, yesterday is archived in the journal")

    def _effective_min_z(self) -> float:
        """Adaptive significance bar: cold form demands stronger proof.

        Last-20 win rate below 45% -> the bar rises 1.96 -> 2.46. Hot form
        keeps the standard 95% gate. Recomputed at most once a minute.
        """
        now = time.time()
        if now - self._adaptive_z_checked < 60:
            return self._adaptive_z
        self._adaptive_z_checked = now
        try:
            recent = journal_engine.list_entries(limit=20)
            if len(recent) >= 10:
                wr = sum(1 for e in recent if e["result"] == "win") / len(recent)
                self._adaptive_z = 2.46 if wr < 0.45 else 1.96
        except Exception:  # noqa: BLE001
            self._adaptive_z = 1.96
        return self._adaptive_z

    def _shadow_track(self, symbol: str, plays: list[dict], tick_count: int) -> None:
        """While benched, record the would-be strike for the shadow scoreboard."""
        if not plays:
            return
        top = plays[0]
        self._pending_shadow.append({
            "symbol": symbol, "name": top["name"], "type": top["type"],
            "digit": top.get("digit"), "z": top.get("z"), "ev": top.get("ev"),
            "tick_count_at_entry": tick_count, "placed_at": time.time(),
        })
        self._pending_shadow = self._pending_shadow[-10:]

    def _shadow_resolve(self, symbol: str, ticks_seen: int) -> None:
        """Settle shadow plays once 5 fresh ticks have passed since entry."""
        from app.core.queue import tick_queue
        resolved = []
        for sh in self._pending_shadow:
            if sh["symbol"] != symbol:
                continue
            fresh = ticks_seen - sh["tick_count_at_entry"]
            if fresh < 5:
                continue
            ticks = tick_queue.recent(symbol, limit=5)
            digits = [t.digit for t in ticks]
            if sh["type"] == "DIFFERS" and sh.get("digit") is not None:
                won = all(d != sh["digit"] for d in digits[:5])
                pnl = 0.10 if won else -1.0  # 1.1 payout on a 1.0 shadow stake
            else:
                won = False
                pnl = -1.0
            self.shadow_stats["wins" if won else "losses"] += 1
            self.shadow_stats["pnl"] = round(self.shadow_stats["pnl"] + pnl, 2)
            resolved.append(sh)
            self._log(
                f"SHADOW: benched call {sh['name']} would have {'WON' if won else 'LOST'} "
                f"(shadow P&L ${self.shadow_stats['pnl']:+.2f})"
            )
        for sh in resolved:
            self._pending_shadow.remove(sh)

    def _record_decision(self, symbol: str, plays: list[dict], team: dict) -> None:
        """Stamp every fresh team decision — proof the call tracks the market."""
        self.decision_history.append({
            "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            "symbol": symbol,
            "plays": [p["name"] for p in plays],
            "ev": plays[0].get("ev"),
            "z": plays[0].get("z"),
            "signal": team.get("signal"),
            "dq": team.get("data_quality"),
        })
        if len(self.decision_history) > DECISION_HISTORY_LEN:
            self.decision_history = self.decision_history[-DECISION_HISTORY_LEN:]

    def _log(self, msg: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        entry = f"{stamp} {msg}"
        self.log.append(entry)
        if len(self.log) > 200:
            self.log = self.log[-200:]

    async def start(self, mode: str = "paper", api_token: str | None = None) -> dict:
        if self.running:
            return {"status": "already running", "mode": self.mode}
        self.mode = mode if mode in ("paper", "live") else "paper"
        balance = 10.0
        if self.mode == "live":
            # Live mode plays with the REAL account — never with a made-up $10.
            token = api_token or await VAULT.get() or self.settings.deriv_api_token
            if not token:
                return {
                    "status": "error",
                    "message": "LIVE refused: no Deriv token connected. "
                               "Connect your account first (Auth panel), then start live.",
                }
            real_balance = await deriv_trader.get_balance(token)
            if real_balance is None:
                acct = await VAULT.status()
                real_balance = acct.get("balance")
            if real_balance is None:
                return {
                    "status": "error",
                    "message": "LIVE refused: could not read your Deriv balance. "
                               "Not risking a single cent blind.",
                }
            if real_balance <= 0.35:
                return {
                    "status": "error",
                    "message": f"LIVE refused: balance ${real_balance:.2f} is below the minimum stake.",
                }
            balance = float(real_balance)
            self._log(f"LIVE account connected — real balance ${balance:.2f}")
        self.running = True
        self.session_started = time.time()
        self.balance = balance
        self.initial_balance = balance
        self.daily_pnl = 0.0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self._session_active = True
        virtual_bank.sync_opening(balance)
        risk_guard.open_session(balance)
        self._hot_hours_cache = (0.0, None)
        self._coach_pending_id = None
        self._log(f"Auto Trader started ({self.mode.upper()} mode)")
        self._log(
            f"Treasurer: ${balance:.2f} in current — 60% of every profit "
            "will be locked in the virtual bank"
        )
        telegram_notifier.send_bot_status("started", self.mode)
        self._task = asyncio.create_task(self._main_loop(api_token))
        return {"status": "started", "mode": self.mode, "message": "Auto Trader started successfully."}

    async def stop(self) -> dict:
        if not self.running:
            return {"status": "not running"}
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._session_active = False
        self._log("Auto Trader stopped")
        telegram_notifier.send_bot_status("stopped", self.mode)
        return {"status": "stopped", "message": "Auto Trader stopped."}

    async def get_balance(self) -> float:
        return self.balance

    async def _simulate_contract_outcome(self, contract: dict, rng) -> tuple[bool, float]:
        """Paper-mode resolution against the FAIR odds, not our confidence.

        The CF was losing because paper mode treated displayed confidence as
        truth. It isn't — confidence is a heuristic. Here each contract plays
        out at the fair base rate for its type, nudged only by the observed
        (z-verified) edge, so a strategy with no real edge shows a real loss
        in paper too.
        """
        t = contract["type"]
        payout = contract.get("payout") or PAYOUTS.get(t, 1.9)
        edge = (contract.get("observed_edge", 0.0) or 0.0) / 100.0
        if t == "MATCHES":
            p_win = max(0.05, min(0.22, 0.10 + edge))
        elif t == "DIFFERS":
            p_win = max(0.78, min(0.95, 0.90 + edge))
        elif t in ("ODD", "EVEN"):
            p_win = max(0.40, min(0.60, 0.50 + edge))
        elif t in ("OVER", "UNDER"):
            fair = (contract.get("fair_pct", 50.0) or 50.0) / 100.0
            p_win = max(0.05, min(0.95, fair + edge))
        else:
            p_win = 0.5
        return rng.random() < p_win, payout

    async def place_trade(self, contract: dict, stake: float, api_token: str | None = None) -> dict:
        """Execute one contract. Live mode waits for the REAL settlement —
        a successful purchase is not a win. A failed step ABORTS the trade:
        no fake P&L, no phantom losses, loud logging, and no journal entry.
        """
        vault_token = await VAULT.get()
        if self.mode == "live" and (api_token or vault_token or self.settings.deriv_api_token):
            token = api_token or vault_token or self.settings.deriv_api_token
            result = await deriv_trader.place_trade(
                symbol=contract.get("symbol", self.settings.active_symbols[0]),
                contract_type=contract["type"],
                amount=stake,
                duration=contract.get("duration_seconds", 5),
                api_token=token,
                digit=contract.get("digit"),
            )
            if result.get("status") != "success":
                # ABORT: nothing settled, nothing is booked. Loud, not silent.
                msg = result.get("error", "unknown error")
                self.counters["aborts"] += 1
                self._log(
                    f"TRADE ABORTED ({result.get('step', '?')}): {contract.get('symbol')} "
                    f"{contract['name']} — {msg}. No money booked."
                )
                telegram_notifier.send_risk_alert(
                    f"Trade aborted at {result.get('step', '?')}: {msg}"
                )
                return {"won": None, "pnl": 0.0, "aborted": True}
            won = bool(result.get("won"))
            pnl = float(result.get("pnl", 0.0))
        else:
            won, payout = await self._simulate_contract_outcome(contract, self._paper_rng)
            pnl = stake * (payout - 1) if won else -stake
            # simulate contract duration quickly (paper keeps it snappy)
            await asyncio.sleep(0.5)

        self.balance += pnl
        self.daily_pnl += pnl
        self.trades_today += 1
        self.counters["trades"] += 1
        if self._session_active:
            # The Treasurer splits the money: 60% of profits locked in the
            # vault, losses hit only the spendable current balance.
            split = virtual_bank.record_pnl(pnl, note=f"{contract.get('symbol')} {contract['name']}", mode=self.mode)
            risk_guard.record_trade(self.balance)
            if split["to_vault"] > 0:
                self._log(
                    f"Treasurer: +${pnl:.2f} — vault +${split['to_vault']:.2f}, "
                    f"current +${split['to_current']:.2f} "
                    f"(spendable ${virtual_bank.spendable():.2f})"
                )
            tilt = risk_guard.tilt_warning("win" if won else "loss")
            if tilt:
                self._log(tilt)
                telegram_notifier.send_risk_alert(tilt)
        if won:
            self.wins_today += 1
            self.consecutive_losses = 0
            if self.tight_marking:
                self.tight_marking = False
                self._log("GOAL — tight marking released, CF back to normal pressing")
            result_label = "WIN"
            telegram_notifier.send_result_alert(True, pnl, contract.get("symbol"), contract["name"])
        else:
            self.losses_today += 1
            self.consecutive_losses += 1
            result_label = "LOSS"
            telegram_notifier.send_result_alert(False, pnl, contract.get("symbol"), contract["name"])
            # Pep's rule: lose possession twice and you do NOT chase the game.
            # Pause, adjust the positioning, tight-mark, and wait for the
            # right strike. The CF sits out and returns under a higher bar.
            if self.consecutive_losses >= MAX_GAMES_WITHOUT_GOAL and not self.benched:
                self.benched = True
                self.benched_until = self._scan_count + BENCH_GAMES
                self._log(
                    f"PEP'S RULE: {self.consecutive_losses} straight misses — stop, regroup, "
                    f"tight marking. No chasing. CF sits {BENCH_GAMES} scans; returns only "
                    f"for a proven strike (z>={TIGHT_MIN_Z}, {TIGHT_CONFIRM_TICKS} confirmations)"
                )
                telegram_notifier.send_risk_alert(
                    f"CF benched: {self.consecutive_losses} consecutive losses — regrouping, no chasing"
                )
            # Mode escalation: if the manager armed the ladder, enough
            # straight losses and the trigger moves to COACH by itself.
            esc = risk_guard.maybe_escalate(self.consecutive_losses)
            if esc:
                self._log(esc)
                telegram_notifier.send_risk_alert(esc)

        self.last_trade = {
            "symbol": contract.get("symbol", ""),
            "contract": contract["name"],
            "result": result_label,
            "pnl": round(pnl, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        journal_engine.add_entry(
            market=contract.get("symbol", ""),
            contract=contract["type"],
            digit=contract.get("digit"),
            stake=stake,
            result="win" if won else "loss",
            pnl=pnl,
            data_quality=contract.get("data_quality", 0.0),
            evidence_score=contract.get("confidence", 0.0),
            mode=self.mode,
            analysis_snapshot={**contract, "balance_at_entry": round(self.balance - pnl, 2)},
        )
        self._log(f"Trade resolved: {result_label} {'+' if pnl >= 0 else ''}${pnl:.2f} ({contract.get('symbol')} {contract['name']})")
        return {"won": won, "pnl": pnl}

    async def _main_loop(self, api_token: str | None) -> None:
        try:
            while self.running:
                self._scan_count += 1
                self.counters["scans"] += 1
                self._rollover_matchday()
                # Manager's bench: the CF sits out a few scans after a bad run.
                # HEV never sits — the speed bot keeps firing; the Guard's
                # dollar limits are the only brake. Pep's rule still applies
                # in every other mode.
                if self.benched and risk_guard.mode != "HEV":
                    for sym in self.settings.active_symbols:
                        try:
                            mm = market_master.analyze(sym, window=100)
                            from app.core.queue import tick_queue
                            ticks_seen = len(tick_queue.recent(sym, limit=1000))
                            self._shadow_track(sym, select_plays(mm, sym), ticks_seen)
                            self._shadow_resolve(sym, ticks_seen)
                        except Exception:  # noqa: BLE001
                            pass
                    if self._scan_count >= self.benched_until:
                        self.benched = False
                        self.consecutive_losses = 0
                        self.tight_marking = True
                        self._log(
                            "CF returns — TIGHT MARKING: no rushing the counter, "
                            f"only a proven strike (z>={TIGHT_MIN_Z}, "
                            f"{TIGHT_CONFIRM_TICKS} confirmations) until he scores"
                        )
                    else:
                        await asyncio.sleep(1.0)
                        continue
                # Live balance reconciliation: every 5 minutes, the books are
                # checked against the REAL account. Drift is logged and synced.
                if self.mode == "live" and time.time() - self._last_reconcile > 300:
                    self._last_reconcile = time.time()
                    try:
                        token = api_token or await VAULT.get() or self.settings.deriv_api_token
                        real = await deriv_trader.get_balance(token) if token else None
                        if real is not None and abs(real - self.balance) > 0.5:
                            drift = round(real - self.balance, 2)
                            self._log(f"Reconciliation: Deriv says ${real:.2f}, books said ${self.balance:.2f} — syncing ({drift:+})")
                            if self._session_active:
                                virtual_bank.record_pnl(drift, note="balance reconciliation", mode=self.mode)
                            self.balance = float(real)
                    except Exception:  # noqa: BLE001
                        pass
                symbols = self.settings.active_symbols
                best_symbol = None
                best_plays: list[dict] = []
                best_team: dict = {}
                best_board: list[dict] = []
                best_rank = (-1, -1.0)  # (top ev, data quality) — better DQ wins ties
                for sym in symbols:
                    try:
                        mm = market_master.analyze(sym, window=100)
                    except Exception:  # noqa: BLE001
                        continue
                    plays = select_plays(mm, sym)
                    if not plays:
                        # Explain-my-no-trade: why the best contract on this
                        # table sits — the verdict, in plain words.
                        board = mm.get("all_contracts") or []
                        if board:
                            self.no_trade_reasons[sym] = (
                                f"{board[0].get('name', 'top contract')}: "
                                f"{board[0].get('verdict_reason', 'team vote: bench')}"
                            )
                        continue
                    rank = (plays[0].get("ev", 0), mm.get("data_quality", 0) or 0)
                    if rank > best_rank:
                        best_rank = rank
                        best_symbol = sym
                        best_plays = plays
                        best_board = mm.get("all_contracts") or []
                        best_team = {
                            "signal": mm.get("signal"),
                            "data_quality": mm.get("data_quality"),
                            "volatility": (mm.get("volatility") or {}).get("regime"),
                            "movement": (mm.get("movement") or {}).get("regime"),
                            "anomaly_count": mm.get("anomaly_count"),
                        }
                self.phase = "matchday" if best_plays else "training"

                if best_plays:
                    key = best_symbol + ":" + "|".join(sorted(p["name"] for p in best_plays))
                    if key == self._last_conf_key:
                        self.confirmation_ticks += 1
                    else:
                        # The market moved and the team's call moved with it.
                        self._last_conf_key = key
                        self.confirmation_ticks = 1
                        self._record_decision(best_symbol, best_plays, best_team)
                    self.current_recommendation = {
                        "symbol": best_symbol,
                        "contract": best_plays[0]["name"],
                        "confidence": best_plays[0]["confidence"],
                        "ev": best_plays[0].get("ev"),
                        "digit": best_plays[0].get("digit"),
                        "z": best_plays[0].get("z"),
                        "decided_at": datetime.now(timezone.utc).isoformat(),
                        "plays": [
                            {
                                "contract": p["name"],
                                "confidence": p["confidence"],
                                "ev": p.get("ev"),
                                "digit": p.get("digit"),
                            }
                            for p in best_plays
                        ],
                        # The whole squad's verdict on this market — top 5 of
                        # the scouting board with WHY each plays or sits.
                        "board": [
                            {
                                "contract": c["name"],
                                "ev": c.get("ev"),
                                "z": c.get("z"),
                                "verdict": c.get("verdict", "PLAY" if c in best_plays else "BENCH"),
                                "reason": c.get("verdict_reason", ""),
                            }
                            for c in (best_board or [])[:5]
                        ],
                        "team": best_team,
                    }
                else:
                    self.confirmation_ticks = 0
                    self.current_recommendation = None

                session_seconds = time.time() - (self.session_started or time.time())
                violations = check_hard_stops(
                    self.initial_balance,
                    self.balance,
                    self.consecutive_losses,
                    self.trades_today,
                    session_seconds,
                )
                if violations:
                    for v in violations:
                        telegram_notifier.send_risk_alert(v)
                        self._log(f"RISK LIMIT: {v}")
                    self.running = False
                    break

                # The Risk Guard speaks money, not percentages: kill switch,
                # daily $ loss floor, session $ take-profit, hourly trade cap.
                guard_violations = risk_guard.check(self.daily_pnl, balance=self.balance)
                floor_v = virtual_bank.check_floor() if self._session_active else None
                if floor_v:
                    guard_violations.append(floor_v)
                hard = [v for v in guard_violations if not v.startswith("FULL_MANUAL")]
                if hard:
                    self.counters["gate_blocks"] += 1
                    for v in hard:
                        telegram_notifier.send_risk_alert(v)
                        self._log(f"GUARD: {v}")
                    self.running = False
                    break
                manual_only = any(v.startswith("FULL_MANUAL") for v in guard_violations)

                # Hot-hours filter: if the CF's own track record says this
                # hour is a graveyard (enough data), he stays on the bench.
                # HEV skips this — the speed bot doesn't care about history.
                window_ok = True if risk_guard.mode == "HEV" else self._current_window_ok()
                if window_ok is False:
                    now = time.time()
                    if now - self._last_scan_log > 60:
                        self._last_scan_log = now
                        self._log("Hot-hours filter: this hour has been a loser historically — CF stays on the bench")
                    await asyncio.sleep(5.0)
                    continue

                # The strike gate. Normal pressing: 2 confirmations is enough.
                # Under Pep's tight marking (after a benching): one extra
                # confirmation AND the analysts must show stronger proof —
                # we wait for the right strike, we never chase the last one.
                # BUT: overwhelming evidence (z > 3.0) fires on the spot —
                # speed bots win because they don't wait for confirmations.
                required_ticks = TIGHT_CONFIRM_TICKS if self.tight_marking else 2
                hev = risk_guard.mode == "HEV"
                # COACH: the team votes, the CF fires — but no overwhelming
                # bypass. Every strike waits for the full confirmation count.
                overwhelming = (
                    best_plays
                    and abs(best_plays[0].get("z") or 0.0) >= 3.0
                    and best_plays[0].get("ev", 0) >= 0.05
                    and not self.tight_marking
                    and risk_guard.mode != "COACH"
                )
                if hev or overwhelming:
                    required_ticks = 1  # HEV / overwhelming: fire on the spot
                if self.tight_marking and best_plays:
                    proven = [p for p in best_plays if abs(p.get("z") or 0.0) >= TIGHT_MIN_Z]
                    if len(proven) < len(best_plays):
                        best_plays = proven
                # Adaptive significance: cold form raises the proof bar.
                min_z = self._effective_min_z()
                if min_z > 1.96 and best_plays:
                    best_plays = [p for p in best_plays if abs(p.get("z") or 0.0) >= min_z]
                if manual_only and best_plays and self.confirmation_ticks >= required_ticks:
                    # FULL_MANUAL: the CF advises, you pull the trigger.
                    self.current_recommendation = self.current_recommendation or {}
                    now = time.time()
                    if now - self._last_scan_log > 30:
                        self._last_scan_log = now
                        self._log("FULL_MANUAL: play is lined up — fire it yourself from the dashboard")
                    await asyncio.sleep(5.0)
                    continue
                coach_approved = False
                # HYBRID: fires instantly on overwhelming evidence, asks you
                # on everything else. The speed of the bots, the brakes of the Guard.
                if risk_guard.mode == "HYBRID" and not overwhelming and best_plays and self.confirmation_ticks >= required_ticks:
                    if self._coach_pending_id is None:
                        item = risk_guard.queue_approval({
                            "symbol": best_symbol,
                            "plays": [{"name": p["name"], "ev": p.get("ev"), "z": p.get("z")} for p in best_plays],
                        })
                        self._coach_pending_id = item["id"]
                        self._log(f"HYBRID: play proposed ({best_plays[0]['name']}) — confirm to fire")
                        telegram_notifier.send_risk_alert(
                            f"Hybrid: {best_symbol} {best_plays[0]['name']} needs your approval"
                        )
                        await asyncio.sleep(3.0)
                        continue
                    pending = risk_guard.next_pending()
                    if pending is not None and pending["id"] == self._coach_pending_id:
                        await asyncio.sleep(3.0)
                        continue
                    resolved = next(
                        (a for a in risk_guard.pending_approvals if a["id"] == self._coach_pending_id), None
                    )
                    self._coach_pending_id = None
                    if not resolved or resolved["status"] != "approved":
                        self._log("HYBRID: play rejected — CF moves on")
                        await asyncio.sleep(3.0)
                        continue
                    coach_approved = True
                    self._log("HYBRID: play approved — CF fires")
                elif risk_guard.needs_approval() and best_plays and self.confirmation_ticks >= required_ticks:
                    # COACH mode: the team's board votes, the CF fires. The
                    # manager set the rules; the board enforces them; nobody
                    # waits for a second confirmation after the vote.
                    if self._coach_pending_id is None:
                        self._log(f"COACH: team voted PLAY on {best_plays[0]['name']} — firing automatically")
                        self._coach_pending_id = None  # no queue — the vote IS the confirmation
                if best_plays and self.confirmation_ticks >= required_ticks:
                    # Stake sizing. Manual mode: the manager's exact amount,
                    # capped only by what's spendable — no Kelly, no 10% rule,
                    # no drawdown scaling, no streak halving. The manager owns
                    # the bullet; the Guard's dollar limits own the gun.
                    # Auto mode: quarter-Kelly off SPENDABLE, all scaling on.
                    stake_base = self._phev_stake(self._stake_base())
                    manual_stake = risk_guard.stake_override
                    stakes = []
                    if manual_stake > 0:
                        stakes = [round(min(manual_stake, stake_base), 2) for _ in best_plays]
                        if stakes:
                            self._log(f"Manual stake: ${stakes[0]:.2f} per play (manager's call)")
                    else:
                        dd_mult = drawdown_multiplier(self.initial_balance, self.balance)
                        streak_mult = risk_guard.streak_multiplier(self.consecutive_losses)
                        for p in best_plays:
                            p_win = max(0.01, min(0.99, (p.get("observed_pct", 50.0) or 50.0) / 100.0))
                            payout = p.get("payout") or PAYOUTS.get(p["type"], 1.9)
                            ks = kelly_stake(p_win, payout, stake_base)
                            cap = compute_stake(stake_base)
                            stakes.append(round(min(ks, cap) * dd_mult * streak_mult, 2))
                        if streak_mult < 1.0 and stakes:
                            self._log(f"Streak halving: stakes x{streak_mult} after {self.consecutive_losses} straight misses")
                    # Exposure stagger (auto mode only): combined stakes
                    # never exceed 15% of spendable. In manual mode the
                    # manager owns the number — the Guard's dollar limits
                    # and kill switch are the cage, not a silent resizer.
                    if manual_stake <= 0:
                        total_exposure = sum(stakes)
                        exposure_cap = 0.15 * stake_base
                        if total_exposure > exposure_cap > 0:
                            scale = exposure_cap / total_exposure
                            stakes = [round(s * scale, 2) for s in stakes]
                            self._log(f"Exposure stagger: combined stakes capped at ${exposure_cap:.2f} (x{scale:.2f})")
                    plays = [p for p, s in zip(best_plays, stakes) if s >= 0.35]
                    stakes = [s for s in stakes if s >= 0.35]
                    if not plays:
                        self._log("GK refuses: Kelly says no stake justifies these plays")
                        await asyncio.sleep(5.0)
                        continue
                    if len(plays) > 1 and (self.consecutive_losses > 0 or self.tight_marking):
                        # Coach's recovery rule: after a miss (and throughout
                        # tight marking) no fluid gambles — single strike only.
                        self._log("Coach benches fluid play — single strike until he scores")
                        plays = plays[:1]
                        stakes = stakes[:1]
                    if len(plays) > 1:
                        names = " + ".join(p["name"] for p in plays)
                        splits = " + ".join(f"${s}" for s in stakes)
                        self._log(
                            f"FLUID PLAY: Kelly stakes {splits} — {best_symbol} {names}"
                        )
                    telegram_notifier.send_trade_alert(
                        best_symbol, plays[0]["name"], stakes[0],
                        plays[0].get("duration_seconds", 5),
                    )
                    self._log(
                        f"Placing trade: {best_symbol} {plays[0]['name']} "
                        f"stake={stakes[0]} (z={plays[0].get('z')}, EV {plays[0].get('ev')})"
                    )
                    outcomes = await asyncio.gather(
                        *(self.place_trade(p, s, api_token) for p, s in zip(plays, stakes))
                    )
                    mark_strike_fired(plays)  # rotation: same strike can't fire again for 30s
                    worst = "loss" if any(not o["won"] for o in outcomes) else "win"
                    speed = risk_guard.scan_speed()
                    await asyncio.sleep(
                        risk_guard.cooldown_escalator(
                            cooldown_for(worst) * speed["cooldown_mult"],
                            self.consecutive_losses,
                        )
                    )
                    # HEV: no cooldown — the speed bot fires the next scan
                    # the moment this one settles. The Guard's dollar limits
                    # are the only brake.
                else:
                    if not best_plays:
                        now = time.time()
                        if now - self._last_scan_log > 15:
                            self._last_scan_log = now
                            self._log(
                                "CF training ground: "
                                f"{len(symbols)} markets scanned — no clean pass, drilling"
                            )
                    # Every mode scans fast now: 0.3s across the board.
                    await asyncio.sleep(risk_guard.scan_speed()["loop_sleep"])
        except asyncio.CancelledError:  # normal shutdown path
            pass
        except Exception as exc:  # noqa: BLE001
            self._log(f"Error in trading loop: {exc}")
            telegram_notifier.send_risk_alert(f"Auto Trader error: {exc}")
            self.running = False

    def status(self) -> dict:
        total = self.wins_today + self.losses_today
        win_rate = round(self.wins_today / total * 100, 1) if total else 0.0
        gk = risk_state(self.initial_balance, self.balance, self.consecutive_losses)
        # CF form rating: win rate weighted, with a bonus for current streak.
        if total:
            cf_rating = int(round(40 + win_rate * 0.55 + min(self.consecutive_losses, 0)))
        else:
            cf_rating = 75  # unrated until he plays
        cf_rating = max(40, min(99, cf_rating - (5 if self.benched else 0)))
        return {
            "running": self.running,
            "mode": self.mode,
            "balance": round(self.balance, 2),
            # Manager's stake when set, else 10% of spendable (vault invisible).
            "current_stake": round(risk_guard.stake_override, 2) if risk_guard.stake_override > 0
            else compute_stake(self._stake_base()),
            "stake_mode": "manual" if risk_guard.stake_override > 0 else "auto",
            "daily_pnl": round(self.daily_pnl, 2),
            "trades_today": self.trades_today,
            "consecutive_losses": self.consecutive_losses,
            "wins_today": self.wins_today,
            "losses_today": self.losses_today,
            "win_rate": win_rate,
            "benched": self.benched,
            "tight_marking": self.tight_marking,
            "cf_rating": cf_rating,
            "gk": gk,
            "phase": self.phase,
            "status": "running" if self.running else "stopped",
            "last_trade": self.last_trade,
            "current_recommendation": self.current_recommendation,
            "decision_history": self.decision_history[-DECISION_HISTORY_LEN:],
            "confirmation_ticks": self.confirmation_ticks,
            "log": self.log[-50:],
            "bank": virtual_bank.status(),
            "guard": risk_guard.status(),
            "stake_base": round(self._stake_base(), 2),
            "streak_multiplier": risk_guard.streak_multiplier(self.consecutive_losses),
            "hot_window_ok": self._current_window_ok(),
            "matchday": self._matchday,
            "effective_min_z": self._effective_min_z(),
            "no_trade_reasons": self.no_trade_reasons,
            "counters": self.counters,
            "shadow": {
                "pending": len(self._pending_shadow),
                "wins": int(self.shadow_stats["wins"]),
                "losses": int(self.shadow_stats["losses"]),
                "pnl": round(self.shadow_stats["pnl"], 2),
            },
        }


auto_trader = AutoTrader()
