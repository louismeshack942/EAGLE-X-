"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill, Row } from "@/components/ui";

/** The Session Room — scorecard, smoothness, lessons, season standings,
 * and the shadow scoreboard (what the benched CF would have done). */
export default function SessionRoom({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [score, setScore] = useState<any>(null);
  const [smooth, setSmooth] = useState<any>(null);
  const [exp, setExp] = useState<any>(null);
  const [sugg, setSugg] = useState<any>(null);
  const [season, setSeason] = useState<any>(null);
  const [mc, setMc] = useState<any>(null);
  const [status, setStatus] = useState<any>(null);

  const load = async () => {
    try {
      const [sc, sm, ex, sg, se, st] = await Promise.all([
        apiGet<any>("/session/scorecard"),
        apiGet<any>("/forensics/smoothness"),
        apiGet<any>("/forensics/expectancy"),
        apiGet<any>("/forensics/suggestions"),
        apiGet<any>("/season/report"),
        apiGet<any>("/auto-trader/status"),
      ]);
      setScore(sc); setSmooth(sm); setExp(ex); setSugg(sg); setSeason(se); setStatus(st);
    } catch { /* retries on interval */ }
  };

  const runMc = async () => {
    try { setMc(await apiGet<any>("/forensics/monte-carlo?p_win=0.9&payout=1.1&sims=300")); } catch { /* ignore */ }
  };

  useEffect(() => {
    let mounted = true;
    const safe = async () => { if (mounted) await load(); };
    safe();
    const t = setInterval(safe, refreshMs);
    return () => { mounted = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshMs]);

  const grade = score?.grade ?? "N/A";
  const gradeColor = { A: "#3fb950", B: "#58a6ff", C: "#F5C518", D: "#f0883e", F: "#f85149", "N/A": "#8b949e" }[grade as string] ?? "#8b949e";
  const shadow = status?.shadow;

  return (
    <Card pos="SR" emoji="📋" title="SESSION ROOM"
      actions={<Pill label={`Grade ${grade}`} status={grade === "A" || grade === "B" ? "running" : "neutral"} />}>
      <div style={{ fontSize: "1.6rem", fontWeight: 800, color: gradeColor }}>{grade}</div>
      <div style={{ fontSize: "0.78rem", color: "#8b949e", marginBottom: 6 }}>{score?.note ?? "no session yet"}</div>

      <Row label="Session P&L" value={fmtUsd(score?.session_pnl ?? 0)} accent={(score?.session_pnl ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
      <Row label="Result / Discipline / Smoothness"
        value={`${score?.components?.result ?? "-"} / ${score?.components?.discipline ?? "-"} / ${score?.components?.smoothness ?? "-"}`} />
      <Row label="Ride quality" value={smooth?.label ? `${smooth.label} (${smooth.score}/100)` : "grading…"} accent="#58a6ff" />
      <Row label="Expectancy / trade" value={fmtUsd(exp?.expectancy_per_trade ?? 0)} />
      <Row label="Payoff ratio" value={exp?.payoff_ratio ?? "—"} />
      <Row label="Avg R-multiple" value={exp?.avg_r_multiple ?? "—"} />

      {shadow && (shadow.wins + shadow.losses) > 0 && (
        <Row label="Shadow (benched calls)"
          value={`${shadow.wins}W/${shadow.losses}L ${fmtUsd(shadow.pnl)}`}
          accent={shadow.pnl >= 0 ? "#3fb950" : "#8b949e"} />
      )}

      {season?.position && (
        <Row label={`Season · ${season.week}`} value={season.position}
          accent={season.position === "TITLE RACE" ? "#3fb950" : season.position === "RELEGATION BATTLE" ? "#f85149" : "#F5C518"} />
      )}

      <div style={{ borderTop: "1px solid #21262d", paddingTop: 6, marginTop: 6 }}>
        <div style={{ fontSize: "0.8rem", color: "#F5C518", marginBottom: 4 }}>🎯 Top suggestions</div>
        {(sugg?.suggestions ?? []).map((s: any, i: number) => (
          <div key={i} style={{ fontSize: "0.78rem", color: "#e6edf3", marginBottom: 3 }}>
            <span style={{ color: s.priority === "HIGH" ? "#f85149" : s.priority === "MEDIUM" ? "#F5C518" : "#8b949e" }}>
              [{s.priority}]
            </span>{" "}{s.text}
          </div>
        ))}
      </div>

      <div style={{ borderTop: "1px solid #21262d", paddingTop: 6, marginTop: 6, display: "flex", gap: 6, alignItems: "center" }}>
        <Btn small variant="secondary" onClick={runMc}>🎲 Run Monte Carlo</Btn>
        {mc && (
          <span style={{ fontSize: "0.75rem", color: "#8b949e" }}>
            {mc.verdict}
          </span>
        )}
      </div>
    </Card>
  );
}
