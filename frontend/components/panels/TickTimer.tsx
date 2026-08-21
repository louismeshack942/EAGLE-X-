"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row, Pill } from "@/components/ui";

const STATUS_COLOR: Record<string, string> = {
  GREEN: "#3fb950",
  YELLOW: "#d29922",
  ORANGE: "#f47067",
  RED: "#f85149",
};

export default function TickTimerPanel({ symbol = "R_100", refreshMs = 1000 }: { symbol?: string; refreshMs?: number }) {
  const [timer, setTimer] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>(`/tick-timer/${symbol}`);
        if (mounted) { setTimer(d); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  return (
    <Card title="⏱️ TICK TIMER — RB" actions={<Pill label={timer?.status ?? "…"} color={STATUS_COLOR[timer?.status as string] ?? "#8b949e"} />}>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Seconds to tick" value={timer?.seconds ?? "—"} />
      <Row label="Avg interval" value={`${timer?.avg_interval ?? "—"}s`} />
      <Row label="Since last tick" value={`${timer?.since_last ?? "—"}s`} />
    </Card>
  );
}
