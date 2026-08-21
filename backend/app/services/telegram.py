"""Telegram notifications — real alerts when configured; no-op when not.

Token and chat ID come from environment only, never hardcoded.
"""
import json
import logging
import urllib.request
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def send_message(self, message: str) -> bool:
        if not self.enabled:
            logger.debug("telegram not configured — suppressed: %s", message)
            return False
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": message[:4000],
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram send failed: %s", exc)
            return False

    def send_trade_alert(self, symbol: str, contract: str, stake: float, duration: int) -> bool:
        return self.send_message(f"🦅 TRADE PLACED: {symbol} {contract} stake=${stake:.2f} duration={duration}s")

    def send_result_alert(self, won: bool, pnl: float, symbol: Optional[str] = None, contract: Optional[str] = None) -> bool:
        emoji = "✅" if won else "❌"
        label = "WON" if won else "LOST"
        sign = "+" if pnl >= 0 else ""
        return self.send_message(f"{emoji} TRADE {label}: {sign}${pnl:.2f} {symbol or ''} {contract or ''}")

    def send_risk_alert(self, reason: str) -> bool:
        return self.send_message(f"🛑 RISK ALERT: {reason}")

    def send_bot_status(self, status: str, mode: str) -> bool:
        return self.send_message(f"🤖 Auto Trader {status} ({mode.upper()} mode)")


telegram_notifier = TelegramNotifier()
