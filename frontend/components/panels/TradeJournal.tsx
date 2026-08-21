"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function TradeJournal({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [journal, setJournal] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>("/journal?limit=10");
        if (mounted) { setJournal(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  const dash = journal?.dashboard ?? {};
  const entries = journal?.entries ?? [];
  return (
    <Card pos="SCORE" emoji="📝" title="TRADE JOURNAL">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Trades Today" value={dash.trades_today ?? 0} />
      <Row label="Wins / Losses" value={`${dash.wins ?? 0}/${dash.losses ?? 0}`} />
      <Row label="Net P&L" value={fmtUsd(dash.net_pnl)} accent={(dash.net_pnl ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Win Rate" value={`${dash.win_rate ?? 0}%`} />
      <div style={{ marginTop: 6, maxHeight: 160, overflow: "auto", fontSize: "0.75rem" }}>
        {entries.length === 0 && <div style={{ color: "#8b949e" }}>No trades yet.</div>}
        {entries.map((e: any) => (
          <div key={e.id} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", borderBottom: "1px solid #21262d" }}>
            <span>{e.symbol} {e.contract}{e.digit !== null && e.digit !== undefined ? ` ${e.digit}` : ""}</span>
            <span style={{ color: e.result === "win" ? "#3fb950" : e.result === "loss" ? "#f85149" : "#8b949e" }}>
              {e.pnl >= 0 ? "+" : ""}{e.pnl?.toFixed(2) ?? "0.00"}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
