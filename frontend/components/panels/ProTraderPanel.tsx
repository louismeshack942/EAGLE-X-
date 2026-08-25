"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill, Row } from "@/components/ui";

/** ProTrader Analysis — the analyst's desk. Gold-on-dark theme, a ranked
 * EV board of every contract on the primary symbol with z-scores and
 * verdicts, and one button that arms HYBRID mode (instant on overwhelming
 * evidence, approval queue on the marginal ones). */
export default function ProTraderPanel({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [mm, setMm] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);

  const load = async () => {
    try {
      const [m, s] = await Promise.all([
        apiGet<any>(`/market-master/${symbol}`),
        apiGet<any>("/auto-trader/status"),
      ]);
      setMm(m); setStatus(s);
    } catch { /* retries */ }
  };

  useEffect(() => {
    let m = true;
    const safe = async () => { if (m) await load(); };
    safe();
    const t = setInterval(safe, refreshMs);
    return () => { m = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, refreshMs]);

  const mode = status?.guard?.mode ?? "FULL_AUTO";
  const isActive = mode === "HYBRID";
  const board = (mm?.all_contracts ?? [])
    .filter((c: any) => c.ev > 0)
    .slice(0, 6);

  const arm = async () => {
    await apiPost("/guard/mode", { mode: "HYBRID" });
    await load();
  };

  return (
    <Card pos="PT" emoji="📊" title="PROTRADER"
      actions={<Pill label={isActive ? "ARMED" : "IDLE"} status={isActive ? "running" : "neutral"} pulse={isActive} />}>
      <div style={{
        background: "#1c150a", border: "1px solid #F5C51840", borderRadius: 6,
        padding: "8px 10px", marginBottom: 8,
      }}>
        <div style={{ fontSize: "0.72rem", color: "#F5C518", marginBottom: 6, fontWeight: 700 }}>
          EV BOARD · {symbol} · top +EV contracts
        </div>
        {board.length === 0 && (
          <div style={{ fontSize: "0.72rem", color: "#8b949e" }}>no positive-EV contracts — fair table</div>
        )}
        {board.map((c: any, i: number) => (
          <div key={i} style={{
            display: "flex", justifyContent: "space-between", fontSize: "0.72rem",
            padding: "2px 0", color: c.verdict === "PLAY" ? "#e6edf3" : "#8b949e",
          }}>
            <span>{c.name}</span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              EV {c.ev > 0 ? "+" : ""}{c.ev} · z {c.z > 0 ? "+" : ""}{c.z}
            </span>
          </div>
        ))}
      </div>
      <Row label="Mode" value={mode} accent={isActive ? "#F5C518" : "#8b949e"} />
      <Row label="Recommendation" value={mm?.recommendation ?? "—"} />
      <Row label="Signal" value={mm?.signal ?? "—"} />
      <div style={{ marginTop: 8 }}>
        <Btn variant={isActive ? "secondary" : "primary"} onClick={arm} title="Arm hybrid mode">
          {isActive ? "📊 HYBRID ACTIVE" : "▶ ARM HYBRID"}
        </Btn>
      </div>
    </Card>
  );
}
