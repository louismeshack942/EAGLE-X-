"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Card, Row, Btn, Pill } from "@/components/ui";

export default function AutoTraderPanel({ refreshMs = 3000 }: { refreshMs?: number }) {
  const [status, setStatus] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const d = await apiGet<any>("/auto-trader/status");
      setStatus(d); setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    try {
      await apiPost(path, body ?? {});
      await load();
    } catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  const rec = status?.current_recommendation;
  return (
    <Card pos="CF" emoji="🏹" title="AUTO TRADER"
      actions={<Pill label={status?.running ? "RUNNING" : "STOPPED"} status={status?.running ? "running" : "stopped"} pulse={Boolean(status?.running)} />}
    >
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Status" value={status?.status ?? "stopped"} />
      <Row label="Phase" value={status?.phase ?? "matchday"} accent={status?.phase === "matchday" ? "#3fb950" : "#d29922"} />
      <Row label="Balance" value={fmtUsd(status?.balance)} />
      <Row label="W / L today" value={`${status?.wins_today ?? 0} / ${status?.losses_today ?? 0}`} />
      <Row label="Current Stake (10%)" value={fmtUsd(status?.current_stake)} accent="#d29922" />
      <Row label="Daily P&L" value={fmtUsd(status?.daily_pnl)} accent={status?.daily_pnl >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Trades" value={status?.trades_today ?? 0} />
      {rec?.plays?.length > 1 ? (
        <Row
          label="FLUID PLAY"
          value={rec.plays.map((p: any) => `${p.contract} (${p.confidence}%)`).join(" + ")}
          accent="#3fb950"
        />
      ) : rec && (
        <Row label="Recommendation" value={`${rec.contract} (${rec.confidence}%)`} accent="#58a6ff" />
      )}
      {rec?.team && (
        <Row
          label="Team Feed"
          value={`${rec.team.signal} · DQ ${rec.team.data_quality} · anomalies ${rec.team.anomaly_count ?? 0}`}
        />
      )}
      <Row label="Confirmation Ticks" value={status?.confirmation_ticks ?? 0} />
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <Btn small variant="success" disabled={busy || status?.running} onClick={() => act("/auto-trader/start", { mode: "paper" })}>
          START PAPER
        </Btn>
        <Btn small variant="danger" disabled={busy || !status?.running} onClick={() => act("/auto-trader/stop")}>
          STOP
        </Btn>
      </div>
      <div style={{ marginTop: 8, maxHeight: 120, overflow: "auto", fontSize: "0.7rem", fontFamily: "monospace", color: "#8b949e" }}>
        {(status?.log ?? []).slice(-20).reverse().map((line: string, i: number) => (
          <div key={i}>{line}</div>
        ))}
      </div>
    </Card>
  );
}
