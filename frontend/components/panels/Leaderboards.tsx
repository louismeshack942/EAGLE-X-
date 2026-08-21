"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row } from "@/components/ui";

export default function Leaderboards({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [leaders, setLeaders] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try { const r = await apiGet<any>("/leaderboards"); if (mounted) { setLeaders(r.leaders ?? []); setError(null); } }
      catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  return (
    <Card title="🏅 LEADERBOARDS">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ maxHeight: 160, overflow: "auto", fontSize: "0.75rem" }}>
        {leaders.map((l: any) => (
          <Row key={l.id} label={`#${l.rank} ${l.name}`} value={`P&L ${l.total_pnl ?? 0}`} />
        ))}
        {leaders.length === 0 && <div style={{ color: "#8b949e" }}>Leaderboard empty.</div>}
      </div>
    </Card>
  );
}
