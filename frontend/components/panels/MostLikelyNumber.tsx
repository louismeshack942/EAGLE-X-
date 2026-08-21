"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function MostLikelyNumber({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>(`/most-likely/${symbol}`);
        if (mounted) { setData(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  return (
    <Card title="🎯 MOST LIKELY NUMBER — DMF">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Digit" value={data?.digit ?? "—"} accent="#58a6ff" />
      <Row label="Contract" value={data?.contract ?? "—"} />
      <Row label="Confidence" value={data?.confidence ? `${data.confidence}%` : "—"} />
      <Row label="Evidence" value={data?.evidence ?? "—"} />
    </Card>
  );
}
