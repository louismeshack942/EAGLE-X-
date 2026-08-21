"use client";
import { useState } from "react";
import { apiPost } from "@/lib/api";
import { Card, Btn, Row } from "@/components/ui";

export default function Backtesting() {
  const [symbol, setSymbol] = useState("R_100");
  const [count, setCount] = useState("300");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    try {
      const r = await apiPost<any>("/backtest", { symbol, count: Number(count) });
      setResult(r);
      setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  return (
    <Card title="🧪 BACKTESTING">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
          {["R_10", "R_25", "R_50", "R_75", "R_100"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <input value={count} onChange={(e) => setCount(e.target.value)} placeholder="Ticks" style={{ width: 80, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
        <Btn small variant="primary" disabled={busy} onClick={run}>{busy ? "Running…" : "RUN"}</Btn>
      </div>
      {result && (
        <div style={{ fontSize: "0.75rem" }}>
          <Row label="Trades" value={result.total_trades} />
          <Row label="Win Rate" value={`${result.win_rate}%`} />
          <Row label="Profit Factor" value={result.profit_factor} />
          <Row label="Net Profit" value={`$${result.net_profit}`} accent={result.net_profit >= 0 ? "#3fb950" : "#f85149"} />
          <Row label="Max Drawdown" value={`$${result.max_drawdown}`} />
        </div>
      )}
    </Card>
  );
}
