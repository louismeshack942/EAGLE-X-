"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Card, Row, Pill } from "@/components/ui";

export default function RiskEngine({ refreshMs = 3000 }: { refreshMs?: number }) {
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const data = await apiGet<any>("/auto-trader/status");
        if (mounted) { setStatus(data); setError(null); }
      } catch (e: any) {
        if (mounted) setError(String(e.message ?? e));
      }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  const balance = status?.balance ?? 0;
  // Stake is 10% of the SPENDABLE balance — the vault is invisible to the
  // GK, so the stake never counts protected profit. Backend computes the
  // same number; the panel only displays what the backend decided.
  const stake = status?.current_stake ?? (status?.stake_base ?? balance) * 0.1;
  const stopLoss = (status?.stake_base ?? balance) * 0.2;
  // Manager's ruling: the daily profit target is ALWAYS 500% of the current
  // balance — it rises the moment the balance rises. Not less, not more.
  const profitTarget = status?.gk?.profit_target ?? balance * 5;

  return (
    <Card pos="GK" emoji="🛡️" title="RISK ENGINE" actions={<Pill label={status?.running ? "ACTIVE" : "IDLE"} status={status?.running ? "running" : "neutral"} pulse={Boolean(status?.running)} />}>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Balance" value={fmtUsd(balance)} />
      <Row label="Stake (10%)" value={fmtUsd(stake)} />
      <Row label="Stop-Loss (20%)" value={fmtUsd(stopLoss)} accent="#f85149" />
      <Row label="Daily Profit Target (500% of current balance)" value={fmtUsd(profitTarget)} accent="#3fb950" />
      <Row label="P&L Today" value={fmtUsd(status?.daily_pnl)} accent={status?.daily_pnl >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Trades Today" value={status?.trades_today ?? 0} />
      <Row label="Consecutive Losses" value={status?.consecutive_losses ?? 0} accent={status?.consecutive_losses >= 2 ? "#f85149" : undefined} />
      {status?.tight_marking && (
        <Row label="⚠ PEP'S RULE" value="TIGHT MARKING — waiting for a proven strike" accent="#d29922" />
      )}
    </Card>
  );
}
