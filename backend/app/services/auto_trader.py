"""Auto Trader — autonomous trading loop with strict risk management.

Paper mode simulates trades; live mode executes via DerivTrader.
Paper and live logs are clearly separated; every trade is journaled.
"""
import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Optional

from app.config import get_settings
from app.services.deriv_trader import deriv_trader
from app.services.market_master import market_master
from app.services.money_management import check_hard_stops, compute_stake, cooldown_for
from app.services.persistence import journal_engine
from app.services.telegram import telegram_notifier
from app.services.token_vault import VAULT

FLUID_MAX_PLAYS = 2          # at most two simultaneous positions
FLUID_MIN_CONFIDENCE = 60    # absolute floor for any play
FLUID_PAIR_RATIO = 0.75      # second play must reach 75% of the top's confidence


def select_plays(mm: dict, symbol: str) -> list[dict]:
    """The team feeds SS: pick up to FLUID_MAX_PLAYS contracts on one market.

    Gate: STRONG signal + data quality >= 70, then every contract with
    supportive evidence above the confidence floor. A second play only joins
    when its confidence is within FLUID_PAIR_RATIO of the top — when ODD says
    100 and MATCHES says 100, we play both and split the stake.
    """
    dq = mm.get("data_quality", 0) or 0
    sig = mm.get("signal", "") or ""
    if "STRONG" not in sig or dq < 70:
        return []
    contracts = sorted(
        mm.get("contracts") or [], key=lambda c: c.get("score", 0), reverse=True
    )
    plays: list[dict] = []
    for c in contracts:
        conf = c.get("confidence", 0) or 0
        if conf < FLUID_MIN_CONFIDENCE or "SUPPORT" not in (c.get("evidence") or ""):
            continue
        if plays and conf < plays[0]["confidence"] * FLUID_PAIR_RATIO:
            continue
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
        self.running = True
        self.session_started = time.time()
        self.balance = 10.0
        self.initial_balance = 10.0
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
        """Paper-mode contract resolution, statistically-faithful-ish."""
        t = contract["type"]
        conf = contract.get("confidence", 50.0)
        p = conf / 100.0
        if t == "MATCHES":
            payout = 9.0
            win = rng.random() < min(0.15, p * 0.15)
        elif t == "DIFFERS":
            payout = 1.1
            win = rng.random() < min(0.95, p * 1.05)
        elif t in ("ODD", "EVEN"):
            payout = 1.9
            win = rng.random() < min(0.55, max(0.45, p))
        elif t in ("OVER", "UNDER"):
            payout = 1.9
            win = rng.random() < min(0.6, max(0.4, p))
        else:
            payout = 1.9
            win = rng.random() < 0.5
        return win, payout

    async def place_trade(self, contract: dict, stake: float, api_token: str | None = None) -> dict:
        vault_token = await VAULT.get()
        if self.mode == "live" and (api_token or vault_token or self.settings.deriv_api_token):
            token = api_token or vault_token or self.settings.deriv_api_token
            result = await deriv_trader.place_trade(
                symbol=contract.get("symbol", self.settings.active_symbols[0]),
                contract_type=contract["type"],
                amount=stake,
                duration=contract.get("duration_seconds", 60),
                api_token=token,
            )
            won = result.get("status") == "success"
            pnl = (result.get("payout") or stake) - stake if won else -stake
        else:
            won_fr_action, payout = await self._simulate_contract_outcome(contract, self._paper_rng)
            won = won_fr_action
            pnl = stake * (payout - 1) if won else -stake
            # simulate contract duration quickly (paper keeps it snappy)
            await asyncio.sleep(0.5)

        self.balance += pnl
        self.daily_pnl += pnl
        self.trades_today += 1
        if won:
            self.wins_today += 1
            self.consecutive_losses = 0
            result_label = "WIN"
            telegram_notifier.send_result_alert(True, pnl, contract.get("symbol"), contract["name"])
        else:
            self.losses_today += 1
            self.consecutive_losses += 1
            result_label = "LOSS"
            telegram_notifier.send_result_alert(False, pnl, contract.get("symbol"), contract["name"])

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
                symbols = self.settings.active_symbols
                best_symbol = None
                best_plays: list[dict] = []
                best_team: dict = {}
                best_score = -1
                for sym in symbols:
                    try:
                        mm = market_master.analyze(sym, window=100)
                    except Exception:  # noqa: BLE001
                        continue
                    plays = select_plays(mm, sym)
                    if not plays:
                        continue
                    if plays[0]["score"] > best_score:
                        best_score = plays[0]["score"]
                        best_symbol = sym
                        best_plays = plays
                        best_team = {
                            "signal": mm.get("signal"),
                            "data_quality": mm.get("data_quality"),
                            "volatility": (mm.get("volatility") or {}).get("regime"),
                            "movement": (mm.get("movement") or {}).get("regime"),
                            "anomaly_count": mm.get("anomaly_count"),
                        }

                if best_plays:
                    key = best_symbol + ":" + "|".join(sorted(p["name"] for p in best_plays))
                    if key == self._last_conf_key:
                        self.confirmation_ticks += 1
                    else:
                        self._last_conf_key = key
                        self.confirmation_ticks = 1
                    self.current_recommendation = {
                        "symbol": best_symbol,
                        "contract": best_plays[0]["name"],
                        "confidence": best_plays[0]["confidence"],
                        "digit": best_plays[0].get("digit"),
                        "plays": [
                            {
                                "contract": p["name"],
                                "confidence": p["confidence"],
                                "digit": p.get("digit"),
                            }
                            for p in best_plays
                        ],
                        "team": best_team,
                    }
                else:
                    self.confirmation_ticks = 0

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

                if best_plays and self.confirmation_ticks >= 2:
                    # Stake is 10% of the CURRENT balance, every trade — as the
                    # account grows, the stake grows with it. Same percentage.
                    total_stake = compute_stake(self.balance)
                    plays = best_plays
                    per = round(total_stake / len(plays), 2)
                    if len(plays) > 1 and per < 0.35:
                        # Deriv minimum stake: fall back to the single top play.
                        plays = plays[:1]
                        per = total_stake
                    if len(plays) > 1:
                        names = " + ".join(p["name"] for p in plays)
                        self._log(
                            f"FLUID PLAY: splitting stake ${total_stake} into "
                            f"{len(plays)} x ${per} — {best_symbol} {names}"
                        )
                    telegram_notifier.send_trade_alert(
                        best_symbol, plays[0]["name"], per,
                        plays[0].get("duration_seconds", 60),
                    )
                    self._log(
                        f"Placing trade: {best_symbol} {plays[0]['name']} "
                        f"stake={per} (conf {plays[0]['confidence']}%)"
                    )
                    outcomes = await asyncio.gather(
                        *(self.place_trade(p, per, api_token) for p in plays)
                    )
                    worst = "loss" if any(not o["won"] for o in outcomes) else "win"
                    await asyncio.sleep(cooldown_for(worst))
                else:
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:  # normal shutdown path
            pass
        except Exception as exc:  # noqa: BLE001
            self._log(f"Error in trading loop: {exc}")
            telegram_notifier.send_risk_alert(f"Auto Trader error: {exc}")
            self.running = False

    def status(self) -> dict:
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
            "status": "running" if self.running else "stopped",
            "last_trade": self.last_trade,
            "current_recommendation": self.current_recommendation,
            "confirmation_ticks": self.confirmation_ticks,
            "log": self.log[-50:],
        }


auto_trader = AutoTrader()
