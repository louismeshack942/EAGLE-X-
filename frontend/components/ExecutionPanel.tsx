"use client";

// Phase 5 — Execution UI. Everything here surfaces mode, signal state, risk state,
// execution status, open contracts, results, trade history, and kill-switch state.
// LIVE is DISABLED BY DEFAULT and governed server-side; the UI can only REQUEST a mode
// and can never enable real-money trading by itself.
//
// There is NO accidental one-tap live buy. Executing a signal requires an explicit,
// human-initiated two-step flow (Request -> Confirm) and even then the server revalidates
// everything (signal EXECUTION_READY + risk PASS + not expired + kill switch off).

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, ApiError } from "@/lib/api";

const MODES = ["HARNESS", "PAPER", "LIVE"];

type SignalCard = {
  signal: {
    signal_id: string;
    symbol: string;
    contract_family: string;
    barrier: number | null;
    prediction: string;
    stake: number;
    estimated_probability: number | null;
    expected_value: number | null;
    ask_price: number | null;
    potential_payout: number | null;
    proposal_source: string;
    multi_window_state: string;
    signal_state: string;
    execution_state: string;
    risk_state: string;
    risk_reason: string;
    expiry: number;
    reason: string;
    warnings: string[];
  };
  risk: { state: string; reason: string; vetos: string[] };
  executable: boolean;
  live_enabled: boolean;
};

const MODE_BADGE: Record<string, string> = {
  HARNESS: "badge harness",
  PAPER: "badge connecting",
  LIVE: "badge live",
};
const STATE_BADGE: Record<string, string> = {
  EXECUTION_READY: "badge live",
  EXECUTION_UNCERTAIN: "badge disconnected",
  OPEN: "badge live",
  WON: "badge live",
  LOST: "badge disconnected",
  VOID: "badge harness",
  EXPIRED: "badge disconnected",
  REJECTED: "badge disconnected",
  ERROR: "badge disconnected",
  VALIDATING: "badge connecting",
  NO_SIGNAL: "badge disconnected",
};

export default function ExecutionPanel({ symbol }: { symbol: string }) {
  const [mode, setMode] = useState("PAPER");
  const [family, setFamily] = useState("MATCHES");
  const [barrier, setBarrier] = useState(1);
  const [card, setCard] = useState<SignalCard | null>(null);
  const [info, setInfo] = useState<any>(null);
  const [open, setOpen] = useState<any[]>([]);
  const [ledger, setLedger] = useState<any[]>([]);
  const [perf, setPerf] = useState<any>(null);
  const [kill, setKill] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");

  const loadMeta = useCallback(async () => {
    try {
      const cf = await apiGet<any>("/api/exec/config");
      setInfo(cf);
      setKill((await apiGet<any>("/api/exec/killswitch")).kill_switch);
      setOpen((await apiGet<any>("/api/exec/open")).open);
      setPerf(await apiGet<any>("/api/exec/performance"));
      setLedger((await apiGet<any>("/api/exec/ledger")).trades);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load execution info.");
    }
  }, []);

  useEffect(() => {
    loadMeta();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const loadSignal = async () => {
    setLoading(true);
    setError("");
    setFlash("");
    setConfirming(false);
    try {
      const c = await apiGet<SignalCard>(
        `/api/signal/${symbol}?family=${family}&barrier=${barrier}&window=100&mode=${mode}`
      );
      setCard(c);
      setOpen((await apiGet<any>("/api/exec/open")).open);
      setPerf(await apiGet<any>("/api/exec/performance"));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Signal request failed.");
    } finally {
      setLoading(false);
    }
  };

  const confirmExecute = async () => {
    if (!card) return;
    setError("");
    setFlash("");
    try {
      const r = await apiPost<any>("/api/exec/execute", {
        signal_id: card.signal.signal_id,
        mode,
      });
      setFlash(`${r.status} — ${r.reason}`);
      setConfirming(false);
      await loadMeta();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Execution request failed.");
    }
  };

  const toggleKill = async (on: boolean) => {
    await apiPost<any>("/api/exec/killswitch", { on });
    setKill((await apiGet<any>("/api/exec/killswitch")).kill_switch);
  };

  const resolve = async (cid: string, win: boolean) => {
    try {
      await apiPost<any>("/api/exec/resolve", { contract_id: cid, win });
      await loadMeta();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Resolve failed.");
    }
  };

  const live = mode === "LIVE";
  const canRequest = info?.live_enabled !== undefined; // server decides

  return (
    <div>
      <div className="row" style={{ marginBottom: ".6rem", flexWrap: "wrap", gap: ".5rem" }}>
        <span className="panel-title">Execution</span>
        <span className={info?.live_enabled ? "badge live" : "badge disconnected"}>
          MASTER LIVE SWITCH: {info?.live_enabled ? "ON (SERVER-SIDE)" : "OFF (SERVER-SIDE)"}
        </span>
        <span className={kill ? "badge disconnected" : "badge live"}>
          KILL SWITCH: {kill ? "ACTIVE (no new trades)" : "CLEAR"}
        </span>
        <button
          className="btn secondary"
          onClick={() => toggleKill(!kill)}
        >
          {kill ? "Clear kill switch" : "Engage kill switch"}
        </button>
        <span style={{ flexGrow: 1 }} />
        <span className="muted">mode: </span>
        <select className="sel" value={mode} onChange={(e) => setMode(e.target.value.toUpperCase())}>
          {MODES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      </div>

      {live && (
        <div className="state error" role="alert">
          ⚠ LIVE MODE SELECTED. Real-money trading is DISABLED by default on the server and
          CANNOT be enabled from this interface. Even if enabled elsewhere, the server still
          enforces every gate before any purchase. Never rely on the UI to protect you.
        </div>
      )}

      {error && (
        <div className="state error" role="alert">
          {error}
        </div>
      )}
      {flash && (
        <div className="state" style={{ borderColor: "var(--accent)" }}>
          {flash}
        </div>
      )}

      {/* Build a signal card */}
      <section className="card" style={{ marginBottom: ".75rem" }}>
        <p className="panel-title">Signal decision card (read-only until you confirm)</p>
        <div className="row" style={{ flexWrap: "wrap", gap: ".5rem" }}>
          <select className="sel" value={family} onChange={(e) => setFamily(e.target.value)}>
            {["MATCHES", "DIFFERS", "ODD", "EVEN", "OVER", "UNDER"].map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
          <select className="sel" value={barrier} onChange={(e) => setBarrier(Number(e.target.value))}>
            {Array.from({ length: 10 }, (_, i) => (
              <option key={i} value={i}>
                barrier {i}
              </option>
            ))}
          </select>
          <button className="btn" onClick={loadSignal} disabled={loading}>
            {loading ? "Building…" : "Build signal"}
          </button>
          <span className="muted">window 100 · stake {info?.live_stake_max ?? 1.0} · server-validated</span>
        </div>
      </section>

      {card && (
        <section className="card" style={{ marginBottom: ".75rem" }}>
          <div className="row" style={{ flexWrap: "wrap", gap: ".5rem" }}>
            <span className={STATE_BADGE[card.signal.signal_state] ?? "badge"}>
              SIGNAL: {card.signal.signal_state}
            </span>
            <span className={card.signal.risk_state === "PASS" ? "badge live" : "badge disconnected"}>
              RISK: {card.signal.risk_state}
            </span>
            <span className={MODE_BADGE[mode] ?? "badge"}>{mode}</span>
            {card.signal.proposal_source && (
              <span className="badge harness">PROPOSAL: {card.signal.proposal_source}</span>
            )}
          </div>

          <div className="tbl" style={{ marginTop: ".5rem" }}>
            <table style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>market</th>
                  <th>contract</th>
                  <th>prediction</th>
                  <th>duration</th>
                  <th>stake</th>
                  <th>ask</th>
                  <th>payout</th>
                  <th>P(win)</th>
                  <th>breakeven</th>
                  <th>EV</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>{card.signal.symbol}</td>
                  <td>{card.signal.contract_family}</td>
                  <td>{card.signal.prediction}</td>
                  <td>5</td>
                  <td>{card.signal.stake}</td>
                  <td>{card.signal.ask_price ?? "—"}</td>
                  <td>{card.signal.potential_payout ?? "—"}</td>
                  <td>{card.signal.estimated_probability?.toFixed(4) ?? "—"}</td>
                  <td>—</td>
                  <td>{card.signal.expected_value?.toFixed(4) ?? "—"}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div className="muted" style={{ marginTop: ".5rem" }}>
            multi-window: <b>{card.signal.multi_window_state}</b> · reason: {card.signal.reason}
          </div>
          {card.signal.warnings?.length > 0 && (
            <div className="muted" style={{ marginTop: ".3rem" }}>
              warnings: {card.signal.warnings.join("; ")}
            </div>
          )}

          {card.executable ? (
            <div className="row" style={{ marginTop: ".6rem", gap: ".5rem" }}>
              {!confirming ? (
                <button className="btn" onClick={() => setConfirming(true)}>
                  Request execution
                </button>
              ) : (
                <>
                  <button className="btn" onClick={confirmExecute}>
                    Confirm {mode} execution
                  </button>
                  <button className="btn secondary" onClick={() => setConfirming(false)}>
                    Cancel
                  </button>
                </>
              )}
              <span className="muted">
                Request → confirm; server revalidates everything before purchase.
              </span>
            </div>
          ) : (
            <div className="muted" style={{ marginTop: ".6rem" }}>
              {card.risk.state === "VETO"
                ? `Risk veto: ${card.risk.reason}`
                : "Not executable (risk not passed / expired / rejected)."}
            </div>
          )}
          {canRequest && !card.executable && live && (
            <div className="state error">LIVE is not executable here — this is correct and safe.</div>
          )}
        </section>
      )}

      {/* Open contracts */}
      <section className="card" style={{ marginBottom: ".75rem" }}>
        <p className="panel-title">Open contracts</p>
        {open.length === 0 ? (
          <div className="muted">No open contracts.</div>
        ) : (
          <table className="tbl" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>contract</th>
                <th>mode</th>
                <th>market</th>
                <th>prediction</th>
                <th>stake</th>
                <th>status</th>
                <th>actions</th>
              </tr>
            </thead>
            <tbody>
              {open.map((o) => (
                <tr key={o.contract_id}>
                  <td>{o.contract_id}</td>
                  <td>
                    <span className={MODE_BADGE[o.execution_mode] ?? "badge"}>{o.execution_mode}</span>
                  </td>
                  <td>{o.symbol}</td>
                  <td>{o.prediction}</td>
                  <td>{o.stake}</td>
                  <td>{o.status}</td>
                  <td>
                    <button className="btn secondary" onClick={() => resolve(o.contract_id, true)}>
                      WIN
                    </button>{" "}
                    <button className="btn secondary" onClick={() => resolve(o.contract_id, false)}>
                      LOSS
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Performance */}
      {perf && (
        <section className="card" style={{ marginBottom: ".75rem" }}>
          <p className="panel-title">Performance ledger</p>
          <div className="row" style={{ flexWrap: "wrap", gap: ".6rem" }}>
            <span>trades <b>{perf.trades}</b></span>
            <span>wins <b>{perf.wins}</b></span>
            <span>losses <b>{perf.losses}</b></span>
            <span>win rate <b>{(perf.win_rate * 100).toFixed(1)}%</b></span>
            <span>net <b>{perf.net_profit}</b></span>
            <span>profit factor <b>{perf.profit_factor ?? "—"}</b></span>
            <span>max DD <b>{perf.max_drawdown}</b></span>
            <span>losing streak <b>{perf.losing_streak}</b></span>
          </div>
        </section>
      )}

      {/* Trade history */}
      {ledger.length > 0 && (
        <section className="card">
          <p className="panel-title">Trade history</p>
          <table className="tbl" style={{ width: "100%" }}>
            <thead>
              <tr>
                <th>trade</th>
                <th>mode</th>
                <th>market</th>
                <th>contract</th>
                <th>prediction</th>
                <th>stake</th>
                <th>payout</th>
                <th>pnl</th>
                <th>status</th>
              </tr>
            </thead>
            <tbody>
              {ledger.slice(0, 20).map((t) => (
                <tr key={t.trade_id}>
                  <td>{t.trade_id.slice(0, 12)}</td>
                  <td>
                    <span className={MODE_BADGE[t.mode] ?? "badge"}>{t.mode}</span>
                  </td>
                  <td>{t.symbol}</td>
                  <td>{t.contract_type}</td>
                  <td>{t.prediction}</td>
                  <td>{t.stake}</td>
                  <td>{t.payout ?? "—"}</td>
                  <td>{t.profit_loss ?? "—"}</td>
                  <td>{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}