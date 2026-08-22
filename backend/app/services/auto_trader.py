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
from app.services.deriv_trader import deriv_trader
from app.services.market_master import MIN_EDGE_PCT, MIN_EV, PAYOUTS, market_master
from app.services.money_management import (
    check_hard_stops,
    compute_stake,
    cooldown_for,
    drawdown_multiplier,
    kelly_stake,
    risk_state,
)
from app.services.persistence import journal_engine
from app.services.telegram import telegram_notifier
from app.services.token_vault import VAULT

FLUID_MAX_PLAYS = 2          # at most two simultaneous positions
FLUID_PAIR_RATIO = 0.75      # second play must reach 75% of the top's EV
MAX_GAMES_WITHOUT_GOAL = 2   # Pep's rule: lose 2 straight → pause, don't chase
BENCH_GAMES = 3              # scans he must sit out while the team regroups
TIGHT_CONFIRM_TICKS = 3      # after a benching: one extra confirmation tick
TIGHT_MIN_Z = 2.5            # after a benching: stronger proof than the 1.96 floor
MAX_ANOMALIES = 3            # physio room: too many anomalies, nobody plays
DECISION_HISTORY_LEN = 20    # recent team decisions surfaced in status


def select_plays(mm: dict, symbol: str) -> list[dict]:
    """The team picks the play; the CF just finishes it.

    Every contract type is eligible — MATCHES, DIFFERS, ODD, EVEN, OVER,
    UNDER — because the decision belongs to the whole squad, not to the
    striker. The gates below ARE the team: CB's signal, LB's data quality,
    the physio's anomaly count, the analysts' 95% z-significance, and the
    scouts' edge/EV floors. Whatever survives, ranked by EV, is the call.
    """
    dq = mm.get("data_quality", 0) or 0
    sig = mm.get("signal", "") or ""
    anomalies = mm.get("anomaly_count", 0) or 0
    if "STRONG" not in sig or dq < 70 or anomalies > MAX_ANOMALIES:
        return []
    contracts = sorted(
        mm.get("all_contracts") or mm.get("contracts") or [],
        key=lambda c: c.get("ev", -1), reverse=True,
    )
    plays: list[dict] = []
    for c in contracts:
        ev = c.get("ev", -1)
        edge = c.get("observed_edge", 0.0) or 0.0
        if ev <= MIN_EV or edge < MIN_EDGE_PCT:
            continue  # scouts: no positive expectation, no meaningful edge
        if not c.get("significant", False):
            continue  # analysts: deviation not proven at the 95% level
        if "SUPPORT" not in (c.get("evidence") or ""):
            continue  # CB: data does not support the direction
        if plays and c.get("ev", 0) < plays[0].get("ev", 0) * FLUID_PAIR_RATIO:
            continue  # coach: second play must be nearly as good as the first
        plays.append({**c, "symbol": symbol, "data_quality": dq, "signal": sig})
        if len(plays) >= FLUID_MAX_PLAYS:
            break
    return plays


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
        self._log(f"Auto Trader started ({self.mode.upper()} mode)")
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
        payout = PAYOUTS.get(t, 1.9)
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
                duration=contract.get("duration_seconds", 60),
                api_token=token,
                digit=contract.get("digit"),
            )
            if result.get("status") != "success":
                # ABORT: nothing settled, nothing is booked. Loud, not silent.
                msg = result.get("error", "unknown error")
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
            analysis_snapshot=contract,
        )
        self._log(f"Trade resolved: {result_label} {'+' if pnl >= 0 else ''}${pnl:.2f} ({contract.get('symbol')} {contract['name']})")
        return {"won": won, "pnl": pnl}

    async def _main_loop(self, api_token: str | None) -> None:
        try:
            while self.running:
                self._scan_count += 1
                # Manager's bench: the CF sits out a few scans after a bad run.
                if self.benched:
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

                # The strike gate. Normal pressing: 2 confirmations is enough.
                # Under Pep's tight marking (after a benching): one extra
                # confirmation AND the analysts must show stronger proof —
                # we wait for the right strike, we never chase the last one.
                required_ticks = TIGHT_CONFIRM_TICKS if self.tight_marking else 2
                if self.tight_marking and best_plays:
                    proven = [p for p in best_plays if abs(p.get("z") or 0.0) >= TIGHT_MIN_Z]
                    if len(proven) < len(best_plays):
                        best_plays = proven
                if best_plays and self.confirmation_ticks >= required_ticks:
                    # GK sizes the stake: quarter-Kelly per play, capped at 10%
                    # of balance, scaled down as drawdown deepens.
                    dd_mult = drawdown_multiplier(self.initial_balance, self.balance)
                    stakes = []
                    for p in best_plays:
                        p_win = max(0.01, min(0.99, (p.get("observed_pct", 50.0) or 50.0) / 100.0))
                        payout = PAYOUTS.get(p["type"], 1.9)
                        ks = kelly_stake(p_win, payout, self.balance)
                        cap = compute_stake(self.balance)
                        stakes.append(round(min(ks, cap) * dd_mult, 2))
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
                        plays[0].get("duration_seconds", 60),
                    )
                    self._log(
                        f"Placing trade: {best_symbol} {plays[0]['name']} "
                        f"stake={stakes[0]} (z={plays[0].get('z')}, EV {plays[0].get('ev')})"
                    )
                    outcomes = await asyncio.gather(
                        *(self.place_trade(p, s, api_token) for p, s in zip(plays, stakes))
                    )
                    worst = "loss" if any(not o["won"] for o in outcomes) else "win"
                    await asyncio.sleep(cooldown_for(worst))
                else:
                    if not best_plays:
                        now = time.time()
                        if now - self._last_scan_log > 15:
                            self._last_scan_log = now
                            self._log(
                                "CF training ground: "
                                f"{len(symbols)} markets scanned — no clean pass, drilling"
                            )
                    await asyncio.sleep(1.0)
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
            "current_stake": compute_stake(self.balance),
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
        }


auto_trader = AutoTrader()
