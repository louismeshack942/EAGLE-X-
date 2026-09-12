"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Btn, Row, Pill } from "@/components/ui";

type CockpitData = any;

const FAMILIES = ["EVEN", "ODD", "MATCHES", "DIFFERS", "OVER", "UNDER"];
const WINDOWS = [100, 250, 1000];

function VerdictBadge({ verdict }: { verdict: string }) {
  const map: Record<string, { color: string; bg: string; label: string }> = {
    EDGE: { color: "#0f5132", bg: "#d2f4e0", label: "EDGE - playable" },
    TRAP: { color: "#7a4f01", bg: "#fff3cd", label: "TRAP - looks real, bleeds money" },
    FAIR: { color: "#3f3f46", bg: "#eaecef", label: "FAIR - no proven edge" },
  };
  const v = map[verdict] ?? map.FAIR;
  return <span style={{ color: v.color, background: v.bg, fontWeight: 800, padding: "4px 10px", borderRadius: 8, display: "inline-block", fontSize: "0.82rem" }}>{v.label}</span>;
}

function GateChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div style={{ padding: "5px 8px", borderRadius: 6, fontSize: "0.68rem", fontWeight: 700, textAlign: "center", color: ok ? "#0f5132" : "#f85149", background: ok ? "#d2f4e0" : "#3d1216", border: `1px solid ${ok ? "#2ea043" : "#f85149"}` }}>
      {ok ? `OK ${label}` : `NO ${label}`}
    </div>
  );
}

function PredictCard({ title, data }: { title: string; data: any }) {
  const top = data?.top ?? data;
  const verdict = data?.verdict ?? "pending";
  const target = top?.observed_pct != null ? top.observed_pct : data?.observed_pct;
  const breakeven = top?.breakeven_pct ?? data?.breakeven_pct;
  const edge = top?.edge_pp ?? data?.edge_pp;
  const ev = top?.ev ?? data?.ev;
  const digit = top?.digit ?? data?.digit;
  const playable = data?.playable ?? false;
  const sample = top?.sample_n ?? data?.sample_n;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ fontWeight: 800, fontSize: "0.82rem", color: "#e6edf3" }}>{title}</span>
        <Pill label={playable ? "PLAYABLE" : verdict ?? "PENDING"} status={playable ? "strong" : verdict === "EDGE" ? "strong" : verdict === "TRAP" ? "weak" : "neutral"} />
      </div>
      {target != null ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: "0.75rem", color: "#c9d1d9" }}>
          {digit != null && <Row label="Digit" value={digit} />}
          <Row label="Observed" value={`${target}%`} />
          <Row label="Breakeven" value={breakeven != null ? `${breakeven}%` : "-"} />
          <Row label="Edge" value={edge != null ? `${edge > 0 ? "+" : ""}${edge}pp` : "-"} accent={(edge ?? 0) > 0 ? "#3fb950" : "#f85149"} />
          <Row label="EV" value={ev != null ? `${(ev * 100).toFixed(2)}%` : "-"} accent={(ev ?? 0) > 0 ? "#3fb950" : "#f85149"} />
          {sample != null && <Row label="Sample" value={sample} />}
        </div>
      ) : (
        <div style={{ fontSize: "0.7rem", color: "#8b949e", fontStyle: "italic" }}>
          No {title} read yet - hit the button above.
        </div>
      )}
    </div>
  );
}

function EntryCard({ data }: { data: any }) {
  const avail = data?.entry?.available ?? false;
  const e = data?.entry;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <span style={{ fontWeight: 800, fontSize: "0.82rem", color: "#e6edf3" }}>ENTRY POINT</span>
        <Pill label={avail ? "READY" : "STAND DOWN"} status={avail ? "live" : "idle"} pulse={avail} />
      </div>
      {avail ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: "0.75rem", color: "#c9d1d9" }}>
          <Row label="Direction" value={e?.direction} accent="#58a6ff" />
          <Row label="Contract" value={e?.contract_type} />
          <Row label="Candidate" value={e?.candidate} />
          <Row label="Stake" value={`$ ${e?.stake}`} />
          <Row label="Duration" value={e?.duration_ticks != null ? `${e.duration_ticks} ticks` : "-"} />
          {e?.last_quote != null && <Row label="Last price" value={e.last_quote} accent="#3fb950" />}
          <Row label="Edge" value={e?.edge_pp != null ? `${e.edge_pp}pp` : "-"} accent={(e?.edge_pp ?? 0) > 0 ? "#3fb950" : "#f85149"} />
          <div style={{ fontSize: "0.7rem", color: "#8b949e", marginTop: 4 }}>{e?.explain}</div>
        </div>
      ) : (
        <div style={{ fontSize: "0.72rem", color: "#8b949e" }}>
          No verified edge right now - the gate says stand down. Wait for a re-probe.
        </div>
      )}
    </div>
  );
}

export default function CockpitPanel({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [data, setData] = useState<CockpitData>(null);
  const [predict, setPredict] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [fam, setFam] = useState("EVEN");
  const [window, setWindow] = useState(250);
  const [duration, setDuration] = useState("5t");
  const [stake, setStake] = useState(1);
  const [tab, setTab] = useState<"review" | "backtest" | "predict">("review");
  const [backtest, setBacktest] = useState<any>(null);

  const loadCockpit = async () => {
    try {
      const d = await apiGet<any>(`/cockpit/${symbol}?window=${window}&family=${fam}&duration=${duration}&stake=${stake}`);
      if (!d) setError("cockpit engine returned nothing");
      else { setData(d); setError(null); }
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  const loadBacktest = async () => {
    try {
      const b = await apiGet<any>(`/cockpit/backtest/${symbol}?window=${window}&family=${fam}&duration=${duration}&stake=${stake}`);
      setBacktest(b);
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  const loadPredict = async () => {
    try {
      const p = await apiGet<any>(`/cockpit/predict/${symbol}?window=${window}&duration=${duration}&stake=${stake}`);
      setPredict(p);
      setTab("predict");
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => {
    loadCockpit();
    const t = setInterval(loadCockpit, refreshMs);
    return () => clearInterval(t);
  }, [symbol, window, fam, duration, stake, refreshMs]);

  const top = data?.top;
  const verdict = data?.verdict ?? "FAIR";
  const gates = data?.gates ?? {};
  const ladder = data?.ladder ?? {};
  const explain = data?.explain;
  const scheme = data?.scheme_entry;

  return (
    <Card pos="COCKPIT" emoji="🚁" title="EDGE COCKPIT"
      statusLabel={verdict}
      status={verdict === "EDGE" ? "strong" : verdict === "TRAP" ? "weak" : "neutral"}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10, alignItems: "center" }}>
        <select value={fam} onChange={(e) => setFam(e.target.value)} className="ex-select" aria-label="Contract family" style={{ fontSize: "0.75rem" }}>
          {FAMILIES.map((f) => <option key={f}>{f}</option>)}
        </select>
        <select value={window} onChange={(e) => setWindow(Number(e.target.value))} className="ex-select" aria-label="Window" style={{ fontSize: "0.75rem" }}>
          {WINDOWS.map((w) => <option key={w} value={w}>{w} ticks</option>)}
        </select>
        <input value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="5t" aria-label="Duration" style={{ width: "60px", fontSize: "0.75rem" }} className="ex-input" />
        <input type="number" min={1} value={stake} onChange={(e) => setStake(Number(e.target.value))} aria-label="Stake" style={{ width: "70px", fontSize: "0.75rem" }} className="ex-input" />
        <Btn small onClick={() => setTab("review")}>Review</Btn>
        <Btn small variant="secondary" onClick={() => { loadBacktest(); setTab("backtest"); }}>Backtest</Btn>
        <Btn small variant="success" onClick={loadPredict}>Predict</Btn>
      </div>

      {error && <div style={{ color: "#f85149", fontSize: "0.72rem", marginBottom: 8 }}>{error}</div>}

      {tab === "review" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <VerdictBadge verdict={verdict} />
          {explain && <div style={{ fontSize: "0.8rem", color: "#c9d1d9", lineHeight: 1.5 }}>{explain}</div>}
          {top && (
            <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 8 }}>
              <Row label="Top pick" value={top.name ? `${top.name} ${top.digit != null ? `(digit ${top.digit})` : ""}` : "-"} />
              <Row label="Observed" value={top.observed_pct != null ? `${top.observed_pct}%` : "-"} accent={(top.edge_pp ?? 0) >= 3 ? "#3fb950" : "#8b949e"} />
              <Row label="Breakeven" value={top.breakeven_pct != null ? `${top.breakeven_pct}%` : "-"} />
              <Row label="Edge" value={top.edge_pp != null ? `${top.edge_pp > 0 ? "+" : ""}${top.edge_pp}pp` : "-"} accent={(top.edge_pp ?? 0) > 0 ? "#3fb950" : "#f85149"} />
              <Row label="EV" value={top.ev != null ? `${(top.ev * 100).toFixed(2)}%` : "-"} accent={(top.ev ?? 0) > 0 ? "#3fb950" : "#f85149"} />
              <Row label="Z" value={top.z != null ? top.z : "-"} />
            </div>
          )}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 6 }}>
            {["data_quality", "windows_ok", "significant", "breakeven_ok", "ev_ok", "edge_ok", "super"].map((k) => {
              if (!(k in gates)) return null;
              const labels: Record<string, string> = { data_quality: "DATA", windows_ok: "WINDOWS", significant: "SIGNIFICANT", breakeven_ok: "BREAKEVEN", ev_ok: "EV", edge_ok: "EDGE", super: "SUPER" };
              return <GateChip key={k} ok={!!gates[k]} label={labels[k]} />;
            })}
          </div>
          {scheme && (
            <div style={{ background: "#101b2e", border: "1px solid #1f6feb", borderRadius: 8, padding: 8 }}>
              <Row label="Entry scheme" value={`${scheme.direction} on ${scheme.candidate} at $${scheme.stake}`} accent="#58a6ff" />
            </div>
          )}
        </div>
      )}

      {tab === "backtest" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: "0.78rem", color: "#c9d1d9" }}>
          {backtest ? (
            <>
              <Row label="Replays" value={backtest.trades_count ?? backtest.trades ?? 0} />
              <Row label="Win rate" value={backtest.win_rate != null ? `${backtest.win_rate}%` : "-"} />
              <Row label="Net P&amp;L" value={backtest.net_profit != null ? `${backtest.net_profit.toFixed(2)} USD` : "-"} accent={(backtest.net_profit ?? 0) > 0 ? "#3fb950" : "#f85149"} />
              <Row label="Net EV / trade" value={backtest.net_ev != null ? `${backtest.net_ev} USD` : "-"} />
              <Row label="Longest loss streak" value={backtest.longest_loss_streak ?? "-"} />
              {backtest.note && <div style={{ fontSize: "0.68rem", color: "#8b949e", fontStyle: "italic" }}>{backtest.note}</div>}
            </>
          ) : (
            <div style={{ color: "#8b949e" }}>Run a replay to see how this contract would have performed on recorded tape.</div>
          )}
        </div>
      )}

      {tab === "predict" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <div style={{ fontSize: "0.78rem", color: "#c9d1d9", fontWeight: 600 }}>PREDICT + ENTER
            <span style={{ color: "#8b949e", fontWeight: 400 }}> - OVER and UNDER market reads, plus your entry point. Separate checks, one request.</span>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <Btn small variant="primary" onClick={loadPredict}>1. OVER</Btn>
            <Btn small variant="primary" onClick={loadPredict}>2. UNDER</Btn>
            <Btn small variant="success" onClick={loadPredict}>3. ENTRY</Btn>
            <Btn small variant="secondary" onClick={() => setPredict(null)}>Clear</Btn>
          </div>
          {predict ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
              <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 10 }}>
                <PredictCard title="OVER" data={predict.over} />
              </div>
              <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 10 }}>
                <PredictCard title="UNDER" data={predict.under} />
              </div>
              <div style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 10 }}>
                <EntryCard data={predict} />
              </div>
            </div>
          ) : (
            <div style={{ fontSize: "0.75rem", color: "#8b949e", fontStyle: "italic" }}>
              The three signals are computed together on request. Nothing has been sent yet.
            </div>
          )}
        </div>
      )}

      {tab === "review" && ladder && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", color: "#58a6ff", fontSize: "0.75rem" }}>Per-family edge ladder</summary>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 8, paddingTop: 8 }}>
            {FAMILIES.map((f) => {
              const rows = ladder[f] ?? [];
              return (
                <div key={f} style={{ fontSize: "0.7rem", color: "#8b949e" }}>
                  <div style={{ fontWeight: 700, color: "#e6edf3", marginBottom: 4 }}>{f}</div>
                  {rows.length === 0 ? <div>no {f} tape</div> : rows.map((r: any, i: number) => (
                    <div key={`${f}-${i}`} style={{ display: "flex", justifyContent: "space-between", gap: 6 }}>
                      <span>{r.observed_pct ?? "-"}%</span>
                      <span>vs {r.breakeven_pct ?? "-"}%</span>
                      <span style={{ color: r.significant ? "#3fb950" : "#8b949e" }}>{r.significant ? "sig" : "no"}</span>
                      <span style={{ color: (r.ev ?? 0) > 0 ? "#3fb950" : "#f85149" }}>{((r.ev ?? 0) * 100).toFixed(2)}%</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        </details>
      )}
    </Card>
  );
}