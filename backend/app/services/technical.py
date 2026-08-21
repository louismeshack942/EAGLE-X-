"""Technical indicators, volume estimates, sentiment scaffolding."""
import statistics
from typing import List

from app.core.queue import tick_queue


def sma(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    return [sum(values[i - period + 1:i + 1]) / period for i in range(period - 1, len(values))]


def ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    alpha = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def rsi(values: List[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    diffs = [b - a for a, b in zip(values[:-1], values[1:])]
    gains = [max(d, 0) for d in diffs[-period:]]
    losses = [max(-d, 0) for d in diffs[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def stdev_of_returns(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    rets = [(b - a) / a for a, b in zip(values[:-1], values[1:]) if a]
    return statistics.pstdev(rets) if len(rets) > 1 else 0.0


def macd(values: List[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict | None:
    if len(values) < slow:
        return None
    fast_e = ema(values, fast)
    slow_e = ema(values, slow)
    if not fast_e or not slow_e:
        return None
    align = min(len(fast_e), len(slow_e))
    macd_line = [f - s for f, s in zip(fast_e[-align:], slow_e[-align:])]
    signal_line = ema(macd_line, signal) if len(macd_line) >= signal else []
    return {
        "macd": round(macd_line[-1], 6),
        "signal": round(signal_line[-1], 6) if signal_line else None,
        "histogram": round(macd_line[-1] - (signal_line[-1] if signal_line else 0), 6),
    }


def bollinger(values: List[float], period: int = 20, mult: float = 2.0) -> dict | None:
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / period
    sd = statistics.pstdev(window)
    return {
        "middle": round(mean, 4),
        "upper": round(mean + mult * sd, 4),
        "lower": round(mean - mult * sd, 4),
        "bandwidth": round((2 * mult * sd) / mean * 100, 2) if mean else 0.0,
    }


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        h, l, c = highs[i], lows[i], closes[i - 1]
        trs.append(max(h - l, abs(h - c), abs(l - c)))
    return round(sum(trs[-period:]) / period, 6)


class TechnicalEngine:
    def __init__(self, queue=None):
        self.queue = queue or tick_queue

    def analyze(self, symbol: str, window: int = 100) -> dict:
        ticks = self.queue.recent(symbol, limit=window)
        quotes = [t.quote for t in ticks]
        out: dict = {"symbol": symbol, "count": len(quotes)}
        out["sma_10"] = round(sma(quotes, 10)[-1], 4) if sma(quotes, 10) else None
        out["ema_10"] = round(ema(quotes, 10)[-1], 4) if ema(quotes, 10) else None
        out["rsi"] = rsi(quotes)
        out["macd"] = macd(quotes)
        out["bollinger"] = bollinger(quotes)
        out["volatility"] = round(stdev_of_returns(quotes) * 100, 4)
        out["volume_estimate"] = round(len(quotes) * (1 + out["volatility"] / 100), 1)
        # crude sentiment from RSI
        if out["rsi"] is not None:
            out["sentiment"] = "BULLISH" if out["rsi"] > 60 else ("BEARISH" if out["rsi"] < 40 else "NEUTRAL")
        else:
            out["sentiment"] = "NEUTRAL"
        return out


technical_engine = TechnicalEngine()
