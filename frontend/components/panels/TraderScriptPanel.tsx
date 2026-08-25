"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill, Row } from "@/components/ui";

/** TraderScript — pure speed. The panel looks like a terminal: green on
 * black, monospace feel, run counter, and one button that arms the HEV
 * speed-bot mode. Fast decisions, fast executions, no ceremony. */
export default function TraderScriptPanel({ refreshMs = 3000 }: { refreshMs?: number }) {
  const [status, setStatus] = useState<any>(null);
  const [journal, setJournal] = useState<any>(null);

  const load = async () => {
    try {
      const [s, j] = await Promise.all([
        apiGet<any>("/auto-trader/status"),
        apiGet<any>("/journal?limit=5"),
      ]);
      setStatus(s); setJournal(j);
    } catch { /* retries */ }
  };

  useEffect(() => {
    let m = true;
    const safe = async () => { if (m) await load(); };
    safe();
    const t = setInterval(safe, refreshMs);
    return () => { m = false; clearInterval(t); };
  }, [refreshMs]);

  const mode = status?.guard?.mode ?? "FULL_AUTO";
  const isActive = mode === "HEV";

  const arm = async () => {
    await apiPost("/guard/mode", { mode: "HEV" });
    await apiPost("/guard/preset/HEV");
    await load();
  };

  return (
    <Card pos="TS" emoji="⚡" title="TRADERSCRIPT"
      actions={<Pill label={isActive ? "ARMED" : "IDLE"} status={isActive ? "running" : "neutral"} pulse={isActive} />}>
      <div style={{
        background: "#0d1117", border: "1px solid #3fb95040", borderRadius: 6,
        padding: "8px 10px", fontFamily: "monospace", fontSize: "0.75rem", color: "#3fb950",
        marginBottom: 8,
      }}>
        <div>$ traderscript --mode speed --runs 10/tick</div>
        <div style={{ color: "#8b949e" }}>// fires every significant contract, zero cooldown</div>
        <div style={{ color: "#8b949e" }}>// Guard limits: $500 floor / 30 trades per hour</div>
      </div>
      <Row label="Mode" value={mode} accent={isActive ? "#3fb950" : "#8b949e"} />
      <Row label="Trades today" value={status?.trades_today ?? 0} />
      <Row label="Win rate" value={`${status?.win_rate ?? 0}%`} accent={(status?.win_rate ?? 0) >= 50 ? "#3fb950" : "#f85149"} />
      <Row label="Session P&L" value={fmtUsd(status?.daily_pnl ?? 0)} accent={(status?.daily_pnl ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
      <div style={{ marginTop: 8 }}>
        <Btn variant={isActive ? "secondary" : "success"} onClick={arm} title="Arm the speed bot">
          {isActive ? "⚡ SPEED BOT ACTIVE" : "▶ ARM SPEED BOT"}
        </Btn>
      </div>
      <div style={{ marginTop: 8, fontSize: "0.72rem", color: "#8b949e" }}>
        Last runs: {(journal?.entries ?? []).slice(0, 3).map((e: any, i: number) => (
          <div key={i}>{e.market} {e.contract} {e.result === "win" ? "✅" : "❌"} {fmtUsd(e.pnl)}</div>
        ))}
      </div>
    </Card>
  );
}
