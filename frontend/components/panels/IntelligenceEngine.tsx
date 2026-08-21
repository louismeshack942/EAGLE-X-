"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row, Pill, type PillStatus } from "@/components/ui";

const STATUS: Record<string, PillStatus> = {
  STRONG_DATA_SUPPORT: "strong",
  WEAK_DATA_SUPPORT: "weak",
  NEUTRAL: "neutral",
  NO_CLEAR_STATISTICAL_EDGE: "stopped",
  INSUFFICIENT_DATA: "neutral",
  WEAK_DATA_CONTRARY: "stopped",
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
    <Card pos="CB" emoji="🧠" title="INTELLIGENCE ENGINE" actions={<Pill label={decision.replaceAll("_", " ")} status={STATUS[decision] ?? "neutral"} pulse={STATUS[decision] === "strong"} />}>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Data Quality" value={intel?.data_quality ?? "—"} />
      <Row label="Volatility" value={intel?.volatility?.regime ?? "—"} />
      <Row label="Movement" value={intel?.movement?.regime ?? "—"} />
      <Row label="Anomalies" value={intel?.anomaly_level ?? "—"} />
      <Row label="Digit Stability" value={intel?.digit_stability ?? "—"} />
    </Card>
  );
}
