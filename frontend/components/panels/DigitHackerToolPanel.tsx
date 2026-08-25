"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill, Row } from "@/components/ui";

/** DigitHacker — the digit forensics lab. Purple-on-dark theme, a live
 * digit frequency heat grid, overfed/starving callouts, and one button
 * that arms the PARITY mode (OVER/UNDER/ODD/EVEN/MATCHES — no DIFFERS). */
export default function DigitHackerToolPanel({ symbol = "R_100", refreshMs = 3000 }: { symbol?: string; refreshMs?: number }) {
  const [digits, setDigits] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);

  const load = async () => {
    try {
      const [d, s] = await Promise.all([
        apiGet<any>(`/digits/${symbol}`),
        apiGet<any>("/auto-trader/status"),
      ]);
      setDigits(d); setStatus(s);
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
  const isActive = mode === "PARITY";
  const freq = digits?.frequency ?? {};

  const arm = async () => {
    await apiPost("/guard/mode", { mode: "PARITY" });
    await apiPost("/guard/preset/PARITY");
    await load();
  };

  const heatColor = (z: number) => {
    if (z <= -2.5) return "#a371f7";   // starving -> good for DIFFERS
    if (z >= 2.5) return "#f778ba";    // overfed -> good for MATCHES
    return "#30363d";
  };

  return (
    <Card pos="DH" emoji="🔬" title="DIGITHACKER"
      actions={<Pill label={isActive ? "ARMED" : "IDLE"} status={isActive ? "running" : "neutral"} pulse={isActive} />}>
      <div style={{
        background: "#16101f", border: "1px solid #a371f740", borderRadius: 6,
        padding: "8px 10px", marginBottom: 8,
      }}>
        <div style={{ fontSize: "0.72rem", color: "#a371f7", marginBottom: 6, fontWeight: 700 }}>
          DIGIT FREQUENCY · {symbol}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(10, 1fr)", gap: 3 }}>
          {Array.from({ length: 10 }, (_, d) => {
            const f = freq[String(d)] ?? {};
            const z = f.z ?? 0;
            return (
              <div key={d} style={{
                background: heatColor(z), borderRadius: 4, padding: "4px 0",
                textAlign: "center", fontSize: "0.72rem", fontWeight: 800,
                color: z <= -2.5 || z >= 2.5 ? "#fff" : "#8b949e",
              }} title={`digit ${d}: ${f.percent ?? 0}% z=${z}`}>
                {d}
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: "0.68rem", color: "#8b949e", marginTop: 6 }}>
          purple = starving (DIFFERS) · pink = overfed (MATCHES)
        </div>
      </div>
      <Row label="Mode" value={mode} accent={isActive ? "#a371f7" : "#8b949e"} />
      <Row label="Strategy" value="OVER/UNDER/ODD/EVEN/MATCHES only" />
      <div style={{ marginTop: 8 }}>
        <Btn variant={isActive ? "secondary" : "primary"} onClick={arm} title="Arm parity tables">
          {isActive ? "🔬 PARITY ACTIVE" : "▶ ARM PARITY"}
        </Btn>
      </div>
    </Card>
  );
}
