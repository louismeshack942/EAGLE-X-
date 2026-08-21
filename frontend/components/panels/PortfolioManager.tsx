"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Card, Btn, Row } from "@/components/ui";

export default function PortfolioManager({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [data, setData] = useState<any>(null);
  const [symbol, setSymbol] = useState("BTC");
  const [qty, setQty] = useState("1");
  const [entry, setEntry] = useState("100");
  const [current, setCurrent] = useState("100");
  const [cls, setCls] = useState("crypto");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try { const r = await apiGet<any>("/portfolio"); setData(r); setError(null); }
    catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => { load(); const t = setInterval(load, refreshMs); return () => clearInterval(t); }, [refreshMs]);

  const add = async () => {
    try {
      await apiPost("/portfolio/assets", { symbol, asset_class: cls, quantity: Number(qty), entry_price: Number(entry), current_price: Number(current) });
      await load();
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  const summary = data?.summary ?? {};
  const assets = data?.assets ?? [];
  return (
    <Card title="💼 PORTFOLIO MANAGER">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Total Value" value={fmtUsd(summary.total_value)} />
      <Row label="Total P&L" value={fmtUsd(summary.total_pl)} accent={(summary.total_pl ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
      <div style={{ display: "flex", gap: 4, margin: "6px 0", flexWrap: "wrap" }}>
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ width: 70, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 6px" }} />
        <select value={cls} onChange={(e) => setCls(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 6px" }}>
          {["crypto", "stocks", "forex", "commodities", "derivatives", "manual"].map((c) => <option key={c}>{c}</option>)}
        </select>
        <input value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 50, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 6px" }} />
        <input value={entry} onChange={(e) => setEntry(e.target.value)} style={{ width: 60, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 6px" }} />
        <input value={current} onChange={(e) => setCurrent(e.target.value)} style={{ width: 60, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "2px 6px" }} />
        <Btn small variant="primary" onClick={add}>ADD</Btn>
      </div>
      <div style={{ maxHeight: 100, overflow: "auto", fontSize: "0.7rem" }}>
        {assets.map((a: any) => (
          <div key={a.id} style={{ display: "flex", justifyContent: "space-between" }}>
            <span>{a.symbol} ({a.asset_class})</span>
            <span style={{ color: a.pl >= 0 ? "#3fb950" : "#f85149" }}>{fmtUsd(a.pl)}</span>
          </div>
        ))}
        {assets.length === 0 && <div style={{ color: "#8b949e" }}>No assets.</div>}
      </div>
    </Card>
  );
}
