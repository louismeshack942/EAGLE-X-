"""Virtual Bank — the club treasurer.

Your idea #101: the account is split in two.

- **Current balance** — the spendable pile the CF stakes from. Stakes are
  always sized off THIS number, never off the vault.
- **Virtual bank (vault)** — the protected pile. 60% of every profit is
  swept here the moment it lands; losses NEVER touch it. The vault can only
  shrink when the manager explicitly withdraws back to current.

Total account = current + vault.

The bank is a passive ledger: the auto trader syncs it at session start
(sync_opening) and reports every settled trade (record_pnl). It never
changes the trader's own balance math — it only decides how much of the
account is spendable. State is persisted to disk so the vault survives
restarts and deploys.
"""
import threading
from datetime import datetime, timezone

from app.services.persistence import settings_store

_STATE_KEY = "virtual_bank_state"
_lock = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class VirtualBank:
    DEFAULT_SPLIT = 0.60  # 60% of every profit goes to the vault

    def __init__(self) -> None:
        self.opening_balance: float = 0.0
        self.current: float = 0.0        # spendable
        self.vault: float = 0.0          # protected profits
        self.split_ratio: float = self.DEFAULT_SPLIT
        self.total_profit: float = 0.0   # lifetime gross profit since sync
        self.total_loss: float = 0.0
        self.synced: bool = False
        self.history: list[dict] = []
        self._load()

    # ---------------- persistence ----------------
    def _load(self) -> None:
        state = settings_store.get(_STATE_KEY)
        if isinstance(state, dict):
            self.opening_balance = float(state.get("opening_balance", 0.0))
            self.current = float(state.get("current", 0.0))
            self.vault = float(state.get("vault", 0.0))
            self.split_ratio = float(state.get("split_ratio", self.DEFAULT_SPLIT))
            self.total_profit = float(state.get("total_profit", 0.0))
            self.total_loss = float(state.get("total_loss", 0.0))
            self.synced = bool(state.get("synced", False))
            self.history = list(state.get("history", []))[-200:]

    def _save(self) -> None:
        settings_store.set(_STATE_KEY, {
            "opening_balance": self.opening_balance,
            "current": self.current,
            "vault": self.vault,
            "split_ratio": self.split_ratio,
            "total_profit": self.total_profit,
            "total_loss": self.total_loss,
            "synced": self.synced,
            "history": self.history[-200:],
        })

    def _log(self, kind: str, amount: float, note: str) -> None:
        self.history.append({
            "ts": _utcnow(),
            "kind": kind,
            "amount": round(amount, 4),
            "current": round(self.current, 2),
            "vault": round(self.vault, 2),
            "total": round(self.total, 2),
            "note": note,
        })
        self.history = self.history[-200:]

    # ---------------- derived ----------------
    @property
    def total(self) -> float:
        return self.current + self.vault

    def spendable(self) -> float:
        """The number stakes are sized from: the current (spendable) balance."""
        return self.current

    # ---------------- operations ----------------
    def sync_opening(self, balance: float) -> None:
        """Start-of-session sync: everything the trader owns becomes current."""
        with _lock:
            self.opening_balance = float(balance)
            self.current = float(balance)
            self.vault = 0.0
            self.total_profit = 0.0
            self.total_loss = 0.0
            self.synced = True
            self._log("sync", balance, "session opened — balance moved to current")
            self._save()

    def record_pnl(self, pnl: float, note: str = "trade") -> dict:
        """Split every settled trade.

        Profit: split_ratio (default 60%) is swept to the vault, the rest
        stays spendable. Loss: taken from current ONLY — the vault is
        untouchable. Returns the split for logging/telegram.
        """
        with _lock:
            if not self.synced:
                self.sync_opening(0.0)
            pnl = float(pnl)
            split = {"pnl": round(pnl, 4), "to_vault": 0.0, "to_current": 0.0}
            if pnl > 0:
                sweep = round(pnl * self.split_ratio, 4)
                keep = round(pnl - sweep, 4)
                self.vault += sweep
                self.current += keep
                self.total_profit += pnl
                split.update({"to_vault": sweep, "to_current": keep})
                self._log("sweep", sweep, f"{note}: profit split — vault +${sweep:.2f}, current +${keep:.2f}")
            elif pnl < 0:
                self.current += pnl  # pnl is negative
                self.total_loss += abs(pnl)
                split.update({"to_current": pnl})
                self._log("loss", pnl, f"{note}: loss absorbed by current balance")
            self._save()
            return split

    def withdraw(self, amount: float) -> dict:
        """Manager moves money vault -> current (un-protects it)."""
        with _lock:
            amount = max(0.0, float(amount))
            moved = min(amount, self.vault)
            self.vault -= moved
            self.current += moved
            self._log("withdraw", moved, f"manager withdrew ${moved:.2f} from the vault")
            self._save()
            return {"moved": round(moved, 2), "vault": round(self.vault, 2), "current": round(self.current, 2)}

    def deposit(self, amount: float) -> dict:
        """Manager moves money current -> vault (protects it)."""
        with _lock:
            amount = max(0.0, float(amount))
            moved = min(amount, max(0.0, self.current))
            self.current -= moved
            self.vault += moved
            self._log("deposit", moved, f"manager protected ${moved:.2f} in the vault")
            self._save()
            return {"moved": round(moved, 2), "vault": round(self.vault, 2), "current": round(self.current, 2)}

    def set_split(self, ratio: float) -> dict:
        with _lock:
            self.split_ratio = max(0.0, min(0.95, float(ratio)))
            self._log("config", self.split_ratio, f"profit split set to {self.split_ratio * 100:.0f}% vault")
            self._save()
            return {"split_ratio": self.split_ratio}

    # ---------------- reporting ----------------
    def status(self) -> dict:
        return {
            "synced": self.synced,
            "opening_balance": round(self.opening_balance, 2),
            "current_balance": round(self.current, 2),
            "vault_balance": round(self.vault, 2),
            "total_balance": round(self.total, 2),
            "split_ratio": self.split_ratio,
            "split_label": f"{int(round(self.split_ratio * 100))}% of every profit is locked in the vault",
            "total_profit": round(self.total_profit, 2),
            "total_loss": round(self.total_loss, 2),
            "net_profit": round(self.total_profit - self.total_loss, 2),
            "spendable": round(self.spendable(), 2),
            "protected_pct": round(self.vault / self.total * 100, 1) if self.total > 0 else 0.0,
        }

    def recent_history(self, limit: int = 20) -> list[dict]:
        return list(reversed(self.history[-limit:]))


virtual_bank = VirtualBank()
