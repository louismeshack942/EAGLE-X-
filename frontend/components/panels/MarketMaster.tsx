"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, ConfidenceBar, Skeleton } from "@/components/ui";

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
    <Card pos="RMF/LMF" emoji="🌍" title="MARKET MASTER">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ marginBottom: 10, fontWeight: 700, color: "#58a6ff", fontSize: "0.9rem" }}>
        {data?.recommendation ?? (!error ? "Loading…" : "—")}
      </div>
      {!data && !error && <Skeleton lines={6} />}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {contracts.map((c: any) => (
          <div key={c.name}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.75rem", color: "#8b949e", marginBottom: 3 }}>
              <span style={{ color: "#c9d1d9", fontWeight: 600 }}>{c.name}</span>
            </div>
            <ConfidenceBar value={c.confidence ?? 0} />
          </div>
        ))}
      </div>
    </Card>
  );
}
