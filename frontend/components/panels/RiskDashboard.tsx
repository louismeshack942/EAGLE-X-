"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function RiskDashboard({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [var95, setVar95] = useState<any>(null);
  const [dd, setDd] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [v, d] = await Promise.all([
          apiGet<any>("/risk/var?confidence=0.95"),
          apiGet<any>("/risk/drawdown"),
        ]);
        if (mounted) { setVar95(v); setDd(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  return (
    <Card emoji="🛡️" title="RISK DASHBOARD">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="VaR (historical)" value={fmtUsd(var95?.var_historical)} />
      <Row label="VaR (parametric)" value={fmtUsd(var95?.var_parametric)} />
      <Row label="Conditional VaR" value={fmtUsd(var95?.cvar)} />
      <Row label="Max Drawdown" value={fmtUsd(dd?.max_drawdown)} accent="#f85149" />
      <Row label="Sharpe Ratio" value={dd?.sharpe_ratio ?? "—"} />
      <Row label="Samples" value={var95?.sample_size ?? 0} />
    </Card>
  );
}
