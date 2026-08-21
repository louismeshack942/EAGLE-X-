"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function MarketMaster({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>(`/market-master/${symbol}`);
        if (mounted) { setData(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  const contracts = data?.contracts ?? [];
  return (
    <Card title="🏆 MARKET MASTER — RMF/LMF">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ marginBottom: 8, fontWeight: 700, color: "#58a6ff" }}>
        {data?.recommendation ?? "Loading…"}
      </div>
      {contracts.map((c: any) => (
        <Row
          key={c.name}
          label={c.name}
          value={`${c.confidence}%`}
          accent={c.confidence >= 60 ? "#3fb950" : c.confidence >= 40 ? "#d29922" : "#8b949e"}
        />
      ))}
    </Card>
  );
}
