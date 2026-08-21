"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row, Pill, type PillStatus } from "@/components/ui";

const TIMER_STATUS: Record<string, PillStatus> = {
  GREEN: "strong",
  YELLOW: "weak",
  ORANGE: "weak",
  RED: "stopped",
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
    <Card pos="RB" emoji="⏱️" title="TICK TIMER" actions={<Pill label={timer?.status ?? "…"} status={TIMER_STATUS[timer?.status as string] ?? "neutral"} pulse={timer?.status === "GREEN"} />}>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Seconds to tick" value={timer?.seconds ?? "—"} />
      <Row label="Avg interval" value={`${timer?.avg_interval ?? "—"}s`} />
      <Row label="Since last tick" value={`${timer?.since_last ?? "—"}s`} />
    </Card>
  );
}
