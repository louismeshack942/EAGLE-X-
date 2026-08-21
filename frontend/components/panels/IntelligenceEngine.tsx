"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row, Pill } from "@/components/ui";

const COLOR: Record<string, string> = {
  STRONG_DATA_SUPPORT: "#3fb950",
  WEAK_DATA_SUPPORT: "#d29922",
  NEUTRAL: "#8b949e",
  NO_CLEAR_STATISTICAL_EDGE: "#f85149",
  INSUFFICIENT_DATA: "#58a6ff",
  WEAK_DATA_CONTRARY: "#f85149",
};

export default function IntelligenceEngine({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [intel, setIntel] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const data = await apiGet<any>(`/intelligence/${symbol}`);
        if (mounted) { setIntel(data); setError(null); }
      } catch (e: any) {
        if (mounted) setError(String(e.message ?? e));
      }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  const decision = intel?.decision ?? "UNKNOWN";
  return (
    <Card title="🧠 INTELLIGENCE ENGINE — CB" actions={<Pill label={decision} color={COLOR[decision] ?? "#8b949e"} />}>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Data Quality" value={intel?.data_quality ?? "—"} />
      <Row label="Volatility" value={intel?.volatility?.regime ?? "—"} />
      <Row label="Movement" value={intel?.movement?.regime ?? "—"} />
      <Row label="Anomalies" value={intel?.anomaly_level ?? "—"} />
      <Row label="Digit Stability" value={intel?.digit_stability ?? "—"} />
    </Card>
  );
}
