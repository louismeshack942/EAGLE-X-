"use client";
import { useCallback, useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill } from "@/components/ui";

type LabTab = "board" | "contracts" | "journal" | "tape";
const TABS: { id: LabTab; label: string }[] = [
  { id: "board", label: "EDGE BOARD" },
  { id: "contracts", label: "CONTRACT TRUTH" },
  { id: "journal", label: "JOURNAL TRUTH" },
  { id: "tape", label: "THE TAPE" },
];

const VERDICT_COLOR: Record<string, string> = {
  EDGE: "#3fb950",
  FAIR: "#8b949e",
  TRAP: "#f85149",
  SUSTAINABLE: "#3fb950",
  BREAKEVEN: "#d29922",
  "SLOW BLEED": "#f85149",
  UNKNOWN: "#8b949e",
};

const SYMBOLS = ["R_100", "R_50", "R_25", "R_10", "1HZ100V"];

export default function AnalysisLab({ refreshMs = 6000 }: { refreshMs?: number }) {
  const [tab, setTab] = useState<LabTab>("board");
  const [symbol, setSymbol] = useState("R_100");
  const [board, setBoard] = useState<any>(null);
  const [contracts, setContracts] = useState<any>(null);
  const [journal, setJournal] = useState<any>(null);
  const [tape, setTape] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (t: LabTab, sym: string) => {
    setBusy(true);
    try {
      if (t === "board") setBoard(await apiGet<any>("/lab/edge-board"));
      if (t === "contracts") setContracts(await apiGet<any>(`/lab/expectancy/${sym}`));
      if (t === "journal") setJournal(await apiGet<any>("/lab/reconcile"));
      if (t === "tape") setTape(await apiGet<any>("/lab/recordings"));
      setErr(null);
    } catch (e: any) {
      setErr(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load(tab, symbol);
    const t = setInterval(() => load(tab, symbol), refreshMs);
    return () => clearInterval(t);
  }, [tab, symbol, refreshMs, load]);

  const td: React.CSSProperties = { padding: "2px 6px", fontSize: "0.7rem", borderBottom: "1px solid #21262d" };
  const th: React.CSSProperties = { ...td, color: "#8b949e", textAlign: "left", fontWeight: 600 };

  const Badge = ({ v }: { v: string }) => (
    <span style={{ color: VERDICT_COLOR[v] ?? "#8b949e", fontWeight: 700 }}>{v}</span>
  );

  return (
    <Card pos="LAB" emoji="🔬" title="ANALYSIS LAB"
      actions={<Pill label={busy ? "SCANNING" : tab.toUpperCase()} status={busy ? "running" : "idle"} pulse={busy} />}
    >
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
        {TABS.map((t) => (
          <Btn key={t.id} small variant={t.id === tab ? "primary" : "secondary"} onClick={() => setTab(t.id)}>
            {t.label}
          </Btn>
        ))}
      </div>
      {err && <div style={{ color: "#f85149", fontSize: "0.7rem", marginBottom: 6 }}>{err}</div>}

      {tab === "board" && board && (
        <>
          <div style={{ fontSize: "0.7rem", color: board.board_has_edge ? "#3fb950" : "#d29922", marginBottom: 6 }}>
            {board.note}
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>Symbol</th><th style={th}>Best contract</th>
              <th style={th}>EV / $1</th><th style={th}>Margin</th><th style={th}>Verdict</th>
            </tr></thead>
            <tbody>
              {(board.symbols ?? []).map((r: any) => (
                <tr key={r.symbol}>
                  <td style={td}>{r.symbol}</td>
                  <td style={td}>{r.best_contract}</td>
                  <td style={{ ...td, color: r.ev > 0 ? "#3fb950" : "#f85149" }}>{r.ev > 0 ? "+" : ""}{r.ev}</td>
                  <td style={td}>{r.margin_pp > 0 ? "+" : ""}{r.margin_pp}pp</td>
                  <td style={td}><Badge v={r.verdict} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === "contracts" && (
        <>
          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
            {SYMBOLS.map((s) => (
              <Btn key={s} small variant={s === symbol ? "primary" : "secondary"} onClick={() => setSymbol(s)}>
                {s}
              </Btn>
            ))}
          </div>
          {contracts && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={th}>Contract</th><th style={th}>Payout</th>
                <th style={th}>Breakeven</th><th style={th}>Observed</th>
                <th style={th}>Margin</th><th style={th}>Verdict</th>
              </tr></thead>
              <tbody>
                {(contracts.contracts ?? [])
                  .slice()
                  .sort((a: any, b: any) => b.ev - a.ev)
                  .slice(0, 14)
                  .map((c: any) => (
                    <tr key={c.name}>
                      <td style={td}>{c.name}</td>
                      <td style={td}>{c.payout}</td>
                      <td style={td}>{c.breakeven_wr}%</td>
                      <td style={td}>{c.observed_wr}%</td>
                      <td style={{ ...td, color: c.margin_pp > 0 ? "#3fb950" : "#f85149" }}>
                        {c.margin_pp > 0 ? "+" : ""}{c.margin_pp}pp
                      </td>
                      <td style={td}><Badge v={c.verdict} /></td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {tab === "journal" && journal && (
        <>
          <div style={{ fontSize: "0.7rem", color: "#8b949e", marginBottom: 6 }}>
            {journal.entries} trades · total P&amp;L{" "}
            <span style={{ color: journal.total_pnl >= 0 ? "#3fb950" : "#f85149", fontWeight: 700 }}>
              {fmtUsd(journal.total_pnl)}
            </span>
          </div>
          <div style={{ fontSize: "0.7rem", color: "#d29922", marginBottom: 6 }}>{journal.note}</div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>Contract</th><th style={th}>W/L</th><th style={th}>Win %</th>
              <th style={th}>Breakeven</th><th style={th}>P&amp;L</th><th style={th}>Verdict</th>
            </tr></thead>
            <tbody>
              {(journal.contracts ?? []).map((r: any) => (
                <tr key={r.contract}>
                  <td style={td}>{r.contract}</td>
                  <td style={td}>{r.wins}/{r.trades - r.wins}</td>
                  <td style={td}>{r.win_rate}%</td>
                  <td style={td}>{r.breakeven_wr ?? "—"}%</td>
                  <td style={{ ...td, color: r.actual_pnl >= 0 ? "#3fb950" : "#f85149" }}>{fmtUsd(r.actual_pnl)}</td>
                  <td style={td}><Badge v={r.verdict} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {tab === "tape" && tape && (
        <>
          <div style={{ fontSize: "0.7rem", color: "#8b949e", marginBottom: 6 }}>
            {(tape.symbols ?? []).length} symbols on tape ·{" "}
            {((tape.total_bytes ?? 0) / 1024 / 1024).toFixed(2)} MB recorded
          </div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>Symbol</th><th style={th}>Session ticks</th>
              <th style={th}>Size</th><th style={th}>Source</th><th style={th}>Last tick</th>
            </tr></thead>
            <tbody>
              {(tape.symbols ?? []).map((s: any) => (
                <tr key={s.symbol}>
                  <td style={td}>{s.symbol}</td>
                  <td style={td}>{s.ticks_session}</td>
                  <td style={td}>{(s.bytes / 1024).toFixed(1)} KB</td>
                  <td style={td}>{(s.providers ?? []).join(", ")}</td>
                  <td style={td}>{s.last_ts ? new Date(s.last_ts).toLocaleTimeString() : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}
