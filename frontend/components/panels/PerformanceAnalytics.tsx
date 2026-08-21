"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function PerformanceAnalytics({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [perf, setPerf] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try { const r = await apiGet<any>("/performance"); if (mounted) { setPerf(r); setError(null); } }
      catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  return (
    <Card title="📈 PERFORMANCE ANALYTICS">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Trades" value={perf?.total_trades ?? 0} />
      <Row label="Win Rate" value={`${perf?.win_rate ?? 0}%`} />
      <Row label="Profit Factor" value={perf?.profit_factor ?? 0} />
      <Row label="Net Profit" value={fmtUsd(perf?.net_profit)} accent={(perf?.net_profit ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Avg Win / Avg Loss" value={`${fmtUsd(perf?.average_win)} / ${fmtUsd(perf?.average_loss)}`} />
    </Card>
  );
}

export function DiversificationAnalyzer({ refreshMs = 10000 }: { refreshMs?: number }) {
  const [div, setDiv] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try { const r = await apiGet<any>("/portfolio/diversification"); if (mounted) { setDiv(r); setError(null); } }
      catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  return (
    <Card title="🧩 DIVERSIFICATION ANALYZER">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Score" value={`${div?.score ?? 0}/100`} />
      <Row label="Grade" value={div?.grade ?? "—"} />
      <Row label="Concentration Risk" value={div?.concentration_risk ? "YES" : "NO"} accent={div?.concentration_risk ? "#f85149" : "#3fb950"} />
      <Row label="Largest Holding" value={`${div?.largest_holding_pct ?? 0}%`} />
      <Row label="Assets" value={div?.assets ?? 0} />
    </Card>
  );
}
