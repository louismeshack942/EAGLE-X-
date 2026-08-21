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
  const stake = balance * 0.1;
  const stopLoss = balance * 0.2;

  return (
    <Card title="🛡️ RISK ENGINE — GK" actions={<Pill label={status?.running ? "ACTIVE" : "IDLE"} color={status?.running ? "#3fb950" : "#8b949e"} /> }>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Balance" value={fmtUsd(balance)} />
      <Row label="Stake (10%)" value={fmtUsd(stake)} />
      <Row label="Stop-Loss (20%)" value={fmtUsd(stopLoss)} accent="#f85149" />
      <Row label="P&L Today" value={fmtUsd(status?.daily_pnl)} accent={status?.daily_pnl >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Trades Today" value={status?.trades_today ?? 0} />
      <Row label="Consecutive Losses" value={status?.consecutive_losses ?? 0} accent={status?.consecutive_losses >= 3 ? "#f85149" : undefined} />
    </Card>
  );
}
