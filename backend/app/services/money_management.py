"""Money management — stake sizing, stop-loss, profit targets, risk rules."""
from dataclasses import dataclass, field


@dataclass
class RiskRules:
    stake_pct: float = 0.10          # 10% of balance per trade (cap)
    stop_loss_pct: float = 0.20      # 20% HARD STOP
    # Manager's ruling: the daily profit target is 500% of the CURRENT
    # balance — not the opening balance. Every time the balance grows, the
    # target grows with it. Not less, not more. This makes the target a
    # moving horizon: the GK never blows the whistle on a winning run; he
    # only defends the loss side.
    profit_target_pct: float = 5.00  # 500% of CURRENT balance (trailing)
    max_profit_pct: float = 5.00     # alias kept for the hard-stop check
    max_consecutive_losses: int = 3  # season wall (benching fires earlier, at 2)
    max_trades_per_day: int = 50
    max_session_seconds: int = 4 * 3600
    cooldown_after_loss_s: float = 30.0
    cooldown_after_win_s: float = 10.0
    min_pause_s: float = 30.0
    confirmation_ticks: int = 2
    kelly_fraction: float = 0.25     # fractional Kelly (quarter-Kelly, conservative)
    max_kelly_pct: float = 0.10      # Kelly can never exceed the 10% cap
    drawdown_derisk_at: float = 0.10 # start cutting stake at 10% drawdown
    drawdown_floor: float = 0.35     # stake multiplier at the stop-loss wall


def profit_target(current_balance: float, rules: RiskRules | None = None) -> float:
    """Daily profit target: 500% of the CURRENT balance, always.

    Start at $10 → target $50. Balance grows to $25 → target becomes $125.
    The number you see is always 500% of what is in the account right now.
    """
    rules = rules or DEFAULT_RULES
    return round(current_balance * rules.profit_target_pct, 2)


DEFAULT_RULES = RiskRules()


def compute_stake(balance: float, rules: RiskRules | None = None) -> float:
    rules = rules or DEFAULT_RULES
    return round(max(0.35, balance * rules.stake_pct), 2)


def kelly_fraction(p_win: float, payout: float) -> float:
    """Full Kelly fraction for a bet with win prob p_win and payout multiplier.

    f* = (p*(b+1) - 1) / b  where b = payout - 1 (net odds).
    Returns 0.0 for negative-edge bets — Kelly says don't play.
    """
    b = max(payout - 1.0, 1e-9)
    f = (p_win * (b + 1.0) - 1.0) / b
    return max(0.0, f)


def kelly_stake(
    p_win: float,
    payout: float,
    balance: float,
    rules: RiskRules | None = None,
) -> float:
    """World-class GK sizing: quarter-Kelly, capped at the 10% rule.

    Returns 0.0 when Kelly says the bet has no edge — the GK refuses to
    come off his line for a losing play.
    """
    rules = rules or DEFAULT_RULES
    f = kelly_fraction(p_win, payout) * rules.kelly_fraction
    f = min(f, rules.max_kelly_pct)
    if f <= 0:
        return 0.0
    return round(max(0.35, balance * f), 2)


def drawdown_multiplier(initial_balance: float, balance: float, rules: RiskRules | None = None) -> float:
    """Scale stake down as drawdown deepens: 1.0 at par, floor near the wall."""
    rules = rules or DEFAULT_RULES
    if initial_balance <= 0:
        return 1.0
    dd = max(0.0, (initial_balance - balance) / initial_balance)
    if dd <= rules.drawdown_derisk_at:
        return 1.0
    span = max(rules.stop_loss_pct - rules.drawdown_derisk_at, 1e-9)
    depth = min(1.0, (dd - rules.drawdown_derisk_at) / span)
    return round(1.0 - depth * (1.0 - rules.drawdown_floor), 3)


def risk_state(
    initial_balance: float,
    balance: float,
    consecutive_losses: int,
    rules: RiskRules | None = None,
) -> dict:
    """GK's live form card: drawdown, exposure posture, and a 40-99 rating."""
    rules = rules or DEFAULT_RULES
    dd = max(0.0, (initial_balance - balance) / max(initial_balance, 1e-9))
    mult = drawdown_multiplier(initial_balance, balance, rules)
    posture = (
        "FULL_ATTACK" if dd < 0.05 else
        "BALANCED" if dd < rules.drawdown_derisk_at else
        "CAUTIOUS" if dd < rules.stop_loss_pct * 0.75 else
        "DEFEND"
    )
    rating = int(round(99 - dd * 120 - consecutive_losses * 3))
    rating = max(40, min(99, rating))
    return {
        "drawdown_pct": round(dd * 100, 2),
        "stake_multiplier": mult,
        "posture": posture,
        "rating": rating,
        "profit_target": profit_target(balance, rules),   # 500% of CURRENT balance
        "daily_profit": round(balance - initial_balance, 2),
    }


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
    daily_profit = balance - initial_balance
    if drawdown >= rules.stop_loss_pct:
        violations.append(f"STOP_LOSS hit: down {drawdown * 100:.1f}% (limit {rules.stop_loss_pct * 100:.0f}%)")
    # Manager's ruling: the cap is 500% of the CURRENT balance, so it rises
    # with every win. (Because the target always sits 5x ahead of the live
    # balance, this stop effectively never fires — the GK defends the loss
    # side and lets winning runs play out, exactly as ordered.)
    target = profit_target(balance, rules)
    if daily_profit >= target:
        violations.append(
            f"MAX_PROFIT reached: +${daily_profit:.2f} (target 500% of current balance = ${target:.2f})"
        )
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
