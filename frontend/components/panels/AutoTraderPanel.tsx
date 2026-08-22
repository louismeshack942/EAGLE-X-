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
      {status?.benched && (
        <Row label="⚠ PEP'S RULE" value="CF BENCHED — 2 straight misses, regrouping (no chasing)" accent="#f85149" />
      )}
      {status?.tight_marking && !status?.benched && (
        <Row label="⚠ PEP'S RULE" value="TIGHT MARKING — single proven strikes only until he scores" accent="#d29922" />
      )}
      <Row label="Phase" value={status?.phase ?? "matchday"} accent={status?.phase === "matchday" ? "#3fb950" : "#d29922"} />
      <Row label="Balance" value={fmtUsd(status?.balance)} />
      <Row label="W / L today" value={`${status?.wins_today ?? 0} / ${status?.losses_today ?? 0}`} />
      <Row label="Win Rate" value={`${status?.win_rate ?? 0}%`} accent={(status?.win_rate ?? 0) >= 50 ? "#3fb950" : "#f85149"} />
      <Row label="CF Rating" value={`${status?.cf_rating ?? 75}/99`} accent={(status?.cf_rating ?? 75) >= 80 ? "#3fb950" : (status?.cf_rating ?? 75) >= 65 ? "#58a6ff" : "#d29922"} />
      {status?.gk && (
        <Row label="GK Posture" value={`${status.gk.posture} (x${status.gk.stake_multiplier})`} accent={status.gk.posture === "FULL_ATTACK" ? "#3fb950" : status.gk.posture === "DEFEND" ? "#f85149" : "#d29922"} />
      )}
      <Row label="Kelly Stake (cap 10%)" value={fmtUsd(status?.current_stake)} accent="#d29922" />
      <Row label="Daily P&L" value={fmtUsd(status?.daily_pnl)} accent={status?.daily_pnl >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Trades" value={status?.trades_today ?? 0} />
      {rec?.plays?.length > 1 ? (
        <Row
          label="FLUID PLAY"
          value={rec.plays.map((p: any) => `${p.contract} (EV ${p.ev != null ? `+${p.ev}` : `${p.confidence}%`})`).join(" + ")}
          accent="#3fb950"
        />
      ) : rec && (
        <Row label="Recommendation" value={`${rec.contract} (${rec.ev != null ? `EV +${rec.ev}` : `${rec.confidence}%`})`} accent="#58a6ff" />
      )}
      {rec?.team && (
        <Row
          label="Team Feed"
          value={`${rec.team.signal} · DQ ${rec.team.data_quality} · anomalies ${rec.team.anomaly_count ?? 0}`}
        />
      )}
      {rec?.decided_at && (
        <Row label="Team Call Made" value={new Date(rec.decided_at).toLocaleTimeString()} accent="#8b949e" />
      )}
      {rec?.board?.length > 0 && (
        <div style={{ marginTop: 8, borderTop: "1px solid #21262d", paddingTop: 6 }}>
          <div style={{ fontSize: "0.7rem", color: "#8b949e", letterSpacing: 1, marginBottom: 4 }}>
            TEAM BOARD — every contract voted on by the whole squad
          </div>
          {rec.board.map((b: any, i: number) => (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 6, fontSize: "0.72rem", padding: "2px 0", color: b.verdict === "PLAY" ? "#3fb950" : "#8b949e" }}>
              <span style={{ minWidth: 130 }}>{b.verdict === "PLAY" ? "✅" : "🪑"} {b.contract}</span>
              <span style={{ fontFamily: "monospace" }}>
                EV {b.ev != null ? (b.ev >= 0 ? `+${b.ev}` : b.ev) : "—"} · z {b.z != null ? b.z : "—"}
              </span>
              <span style={{ color: "#6e7681", textAlign: "right", flex: 1 }}>{b.verdict === "PLAY" ? "" : b.reason}</span>
            </div>
          ))}
        </div>
      )}
      {status?.decision_history?.length > 0 && (
        <div style={{ marginTop: 8, borderTop: "1px solid #21262d", paddingTop: 6 }}>
          <div style={{ fontSize: "0.7rem", color: "#8b949e", letterSpacing: 1, marginBottom: 4 }}>
            DECISION HISTORY — the team's call as the market moves
          </div>
          <div style={{ maxHeight: 110, overflow: "auto", fontSize: "0.7rem", fontFamily: "monospace" }}>
            {[...status.decision_history].reverse().map((d: any, i: number) => (
              <div key={i} style={{ color: "#c9d1d9", padding: "1px 0" }}>
                <span style={{ color: "#8b949e" }}>{d.ts}</span> {d.symbol} → {d.plays?.join(" + ")} <span style={{ color: "#58a6ff" }}>EV {d.ev != null && d.ev >= 0 ? `+${d.ev}` : d.ev}</span> <span style={{ color: "#6e7681" }}>z {d.z} · {d.signal}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <Row label="Confirmation Ticks" value={status?.confirmation_ticks ?? 0} />
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <Btn small variant="success" disabled={busy || status?.running} onClick={() => act("/auto-trader/start", { mode: "paper" })}>
          START PAPER
        </Btn>
        <Btn small variant="primary" disabled={busy || status?.running} onClick={() => {
          if (window.confirm("GO LIVE with real money? The GK's hard stops apply (20% stop-loss, 3-loss benching), but real stakes are real risk. Confirm to start.")) {
            act("/auto-trader/start", { mode: "live" });
          }
        }}>
          START LIVE
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
