"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function DataQuality({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [q, setQ] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>(`/quality/${symbol}`);
        if (mounted) { setQ(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  const comp = q?.components ?? {};
  return (
    <Card title="📊 DATA QUALITY — LB">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Score" value={`${q?.score ?? "—"}/100`} />
      <Row label="Grade" value={q?.grade ?? "—"} accent={q?.grade === "HIGH" ? "#3fb950" : q?.grade === "MEDIUM" ? "#d29922" : "#f85149"} />
      <Row label="Completeness" value={`${comp.completeness ?? "—"}%`} />
      <Row label="Validity" value={`${comp.validity ?? "—"}%`} />
      <Row label="Consistency" value={`${comp.consistency ?? "—"}%`} />
    </Card>
  );
}
