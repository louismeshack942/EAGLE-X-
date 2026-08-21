"""Money management — stake sizing, stop-loss, profit targets, risk rules."""
from dataclasses import dataclass, field


@dataclass
class RiskRules:
    stake_pct: float = 0.10          # 10% of balance per trade
    stop_loss_pct: float = 0.20      # 20% HARD STOP
    profit_target_pct: float = 1.20  # 120% regular target
    max_profit_pct: float = 5.00     # 500% HARD STOP
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 50
    max_session_seconds: int = 4 * 3600
    cooldown_after_loss_s: float = 30.0
    cooldown_after_win_s: float = 10.0
    min_pause_s: float = 30.0
    confirmation_ticks: int = 2


DEFAULT_RULES = RiskRules()


def compute_stake(balance: float, rules: RiskRules | None = None) -> float:
    rules = rules or DEFAULT_RULES
    return round(max(0.35, balance * rules.stake_pct), 2)


def check_hard_stops(
    initial_balance: float,
    current_balance: float,
    consecutive_losses: int,
    trades_today: int,
    session_seconds: float,
    rules: RiskRules | None = None,
) -> list[str]:
    """Return list of violated hard-stop reasons, empty means OK to trade."""
    rules = rules or DEFAULT_RULES
    violations: list[str] = []
    balance = max(current_balance, 0.00001)
    drawdown = (initial_balance - balance) / max(initial_balance, 0.00001)
    growth = (balance - initial_balance) / max(initial_balance, 0.00001)
    if drawdown >= rules.stop_loss_pct:
        violations.append(f"STOP_LOSS hit: down {drawdown * 100:.1f}% (limit {rules.stop_loss_pct * 100:.0f}%)")
    if growth >= rules.max_profit_pct:
        violations.append(f"MAX_PROFIT reached: up {growth * 100:.1f}% (limit {rules.max_profit_pct * 100:.0f}%)")
    if consecutive_losses >= rules.max_consecutive_losses:
        violations.append(f"CONSECUTIVE_LOSSES: {consecutive_losses} (limit {rules.max_consecutive_losses})")
    if trades_today >= rules.max_trades_per_day:
        violations.append(f"MAX_TRADES_PER_DAY: {trades_today} (limit {rules.max_trades_per_day})")
    if session_seconds >= rules.max_session_seconds:
        violations.append(f"MAX_SESSION_TIME: {session_seconds:.0f}s (limit {rules.max_session_seconds}s)")
    return violations


def cooldown_for(result: str | None, rules: RiskRules | None = None) -> float:
    rules = rules or DEFAULT_RULES
    if result == "loss":
        return rules.cooldown_after_loss_s
    if result == "win":
        return rules.cooldown_after_win_s
    return rules.min_pause_s
