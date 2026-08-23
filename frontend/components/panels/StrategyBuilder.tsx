"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Card, Btn, Row } from "@/components/ui";

const STRATEGY_TYPES = ["DIGIT_MATCH", "DIGIT_DIFF", "OVER_UNDER", "ODD_EVEN", "TREND_FOLLOW", "VOLATILITY_BREAKOUT"];

export default function StrategyBuilder() {
  const [sessions, setSessions] = useState<any[]>([]);
  const [name, setName] = useState("My Strategy");
  const [strategyType, setStrategyType] = useState("DIGIT_MATCH");
  const [symbol, setSymbol] = useState("R_100");
  const [stake, setStake] = useState("1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await apiGet<any>("/strategies");
      setSessions(r.sessions ?? []);
      setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => { load(); }, []);

  const create = async () => {
    setBusy(true);
    try {
      await apiPost("/strategies/create", {
        config: { name, strategy_type: strategyType, symbol, stake: Number(stake), duration_seconds: 5 },
      });
      await load();
    } catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  const act = async (sid: string, action: "start" | "stop") => {
    setBusy(true);
    try { await apiPost(`/strategies/${sid}/${action}`); await load(); }
    catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  return (
    <Card emoji="🛠️" title="STRATEGY BUILDER">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
        <select value={strategyType} onChange={(e) => setStrategyType(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
          {STRATEGY_TYPES.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
          {["R_10", "R_25", "R_50", "R_75", "R_100"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <input value={stake} onChange={(e) => setStake(e.target.value)} style={{ width: 60, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
        <Btn small variant="primary" disabled={busy} onClick={create}>CREATE</Btn>
      </div>
      <div style={{ maxHeight: 180, overflow: "auto", fontSize: "0.75rem" }}>
        {sessions.length === 0 && <div style={{ color: "#8b949e" }}>No strategies yet.</div>}
        {sessions.map((s: any) => (
          <div key={s.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", borderBottom: "1px solid #21262d" }}>
            <span>{s.config?.name} ({s.config?.strategy_type}) — {s.status}</span>
            <span style={{ display: "flex", gap: 4 }}>
              <Btn small variant="success" onClick={() => act(s.id, "start")}>RUN</Btn>
              <Btn small variant="danger" onClick={() => act(s.id, "stop")}>STOP</Btn>
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
