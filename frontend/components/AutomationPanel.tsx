"use client";

// Phase 6 — Automated Trader UI. Displays the automation mode with UNAMBIGUOUS labels
// (OFF / MONITOR / PAPER / LIVE), automation state, scope, risk status, session counters,
// last signal/decision/execution, kill switch and the server-side live switch.
//
// The trader is a CLIENT of the Phase 4/5 pipeline: these controls only start/stop/pause
// the orchestrator. The UI can never enable real-money trading — LIVE requires the
// server-side execution_live_enabled master switch, authentication and every gate.

import { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, ApiError } from "@/lib/api";

const MODE_BADGE: Record<string, string> = {
  OFF: "badge disconnected",
  MONITOR: "badge harness",
  PAPER: "badge connecting",
  LIVE: "badge live",
};

const STATE_BADGE: Record<string, string> = {
  OFF: "badge disconnected",
  STARTING: "badge connecting",
  MONITORING: "badge harness",
  ANALYZING: "badge connecting",
  VALIDATING: "badge connecting",
  READY: "badge connecting",
  EXECUTING: "badge live",
  TRACKING: "badge connecting",
  PAUSED: "badge disconnected",
  ERROR: "badge disconnected",
  STOPPING: "badge disconnected",
};

export default function AutomationPanel({ symbol }: { symbol: string }) {
  const [status, setStatus] = useState<any>(null);
  const [config, setConfig] = useState<any>(null);
  const [decisions, setDecisions] = useState<any[]>([]);
  const [mode, setMode] = useState("MONITOR");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");

  const load = useCallback(async () => {
    try {
      const st = await apiGet<any>("/api/automation/status");
      setStatus(st);
      const cf = await apiGet<any>("/api/automation/config");
      setConfig(cf.config);
      setMode(st.mode || "OFF");
      setDecisions((await apiGet<any>("/api/automation/decisions?limit=8")).decisions || []);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load automation.");
    }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 4000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  const act = async (fn: () => Promise<any>) => {
    setLoading(true);
    setError("");
    setFlash("");
    try {
      const r = await fn();
      if (r.ok === false && r.problems) setError(r.problems.join("; "));
      else setFlash(r.state ? `State → ${r.state}` : "ok");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Request failed.");
    } finally {
      setLoading(false);
      load();
    }
  };

  const setM = (m: string) =>
    act(() => apiPost("/api/automation/set-mode", { mode: m }));
  const updateConfig = (patch: any) =>
    act(() => apiPost("/api/automation/config", { ...(config || {}), ...patch }));

  const st = status || {};
  const cfg = config || {};
  const modeBadge = MODE_BADGE[st.mode] || "badge disconnected";
  const live = st.live_enabled;
  const kill = st.kill_switch;

  return (
    <div>
      <div className="row" style={{ marginBottom: ".6rem", flexWrap: "wrap", gap: ".5rem" }}>
        <span className="panel-title">Automated Trader</span>
        <span className={modeBadge}>MODE: {st.mode || "OFF"}</span>
        <span className={STATE_BADGE[st.state] || "badge disconnected"}>
          STATE: {st.state || "OFF"}
        </span>
        <span className={live ? "badge live" : "badge disconnected"}>
          LIVE SWITCH (server): {live ? "ON" : "OFF"}
        </span>
        <span className={kill ? "badge disconnected" : "badge live"}>
          KILL SWITCH: {kill ? "ACTIVE" : "CLEAR"}
        </span>
      </div>

      <div className="row" style={{ marginBottom: ".6rem", flexWrap: "wrap", gap: ".5rem" }}>
        {["OFF", "MONITOR", "PAPER", "LIVE"].map((m) => (
          <button
            key={m}
            className={m === st.mode ? "btn" : "btn-outline"}
            style={m === "LIVE" && !live ? { borderColor: "#d4a5a5" } : undefined}
            onClick={() => setM(m)}
            disabled={loading}
          >
            {m}
          </button>
        ))}
        <button className="btn" onClick={() => act(() => apiPost("/api/automation/start", {}))} disabled={loading || !cfg.enabled}>
          Start
        </button>
        <button className="btn-outline" onClick={() => act(() => apiPost("/api/automation/pause", {}))} disabled={loading}>
          Pause
        </button>
        <button className="btn-outline" onClick={() => act(() => apiPost("/api/automation/resume", {}))} disabled={loading}>
          Resume
        </button>
        <button className="btn-outline" onClick={() => act(() => apiPost("/api/automation/stop", {}))} disabled={loading}>
          Stop
        </button>
      </div>

      {st.mode === "LIVE" && (
        <div className="badge live" style={{ marginBottom: ".6rem" }}>
          LIVE TRADING — real money. Every gate is enforced server-side; this UI cannot
          enable it.
        </div>
      )}
      {st.mode === "PAPER" && (
        <div className="badge connecting" style={{ marginBottom: ".6rem" }}>
          PAPER — full lifecycle, no real money.
        </div>
      )}
      {st.mode === "MONITOR" && (
        <div className="badge harness" style={{ marginBottom: ".6rem" }}>
          MONITOR — analyzes + risks, NEVER executes.
        </div>
      )}
      {st.mode === "OFF" && (
        <div className="badge disconnected" style={{ marginBottom: ".6rem" }}>
          OFF — automation disabled.
        </div>
      )}

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(210px,1fr))", marginBottom: ".6rem" }}>
        <span className="muted">Markets:</span>
        <span>{(cfg.allowed_markets || []).join(", ")}</span>
        <span className="muted">Contracts:</span>
        <span>{(cfg.allowed_families || []).join(", ")}</span>
        <span className="muted">Max stake:</span>
        <span>${cfg.max_stake ?? "-"}</span>
        <span className="muted">Max trades/session:</span>
        <span>{cfg.max_trades_per_session ?? "-"}</span>
        <span className="muted">Max open:</span>
        <span>{cfg.max_open ?? "-"}</span>

        <span className="muted">Trades today:</span>
        <span>{st.session_trades ?? 0}</span>
        <span className="muted">Session P/L:</span>
        <span>{(st.session_trades ?? 0) > 0 ? "-" : "$0.00"}</span>
        <span className="muted">Daily loss:</span>
        <span>${(st.daily_loss ?? 0).toFixed(2)}</span>
        <span className="muted">Consecutive losses:</span>
        <span>{st.consecutive_losses ?? 0}</span>
        <span className="muted">Open trades:</span>
        <span>{st.open_trades ?? 0}</span>
        <span className="muted">Authenticated:</span>
        <span>{st.authenticated ? "yes" : "no"}</span>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(210px,1fr))", marginBottom: ".6rem" }}>
        <span className="muted">Last signal:</span>
        <span className="mono" style={{ overflowWrap: "anywhere" }}>
          {st.last_signal_id ? `${st.last_signal_id} (${st.last_signal_state})` : "-"}
        </span>
        <span className="muted">Last risk state:</span>
        <span>{st.last_risk_state || "-"}</span>
        <span className="muted">Last decision:</span>
        <span>{st.last_decision || "-"}</span>
        <span className="muted">Last execution:</span>
        <span>{st.last_execution || "-"}</span>
      </div>

      {error && <div className="badge disconnected" style={{ margin: ".4rem 0" }}>{error}</div>}
      {flash && <div className="muted" style={{ margin: ".4rem 0" }}>{flash}</div>}

      <details>
        <summary className="muted">Recent automation decisions</summary>
        <ul style={{ fontSize: ".85rem", paddingLeft: "1rem" }}>
          {decisions.map((d, i) => (
            <li key={i} className="muted" style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {new Date(d.ts * 1000).toLocaleTimeString()} [{d.kind}] {d.message}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}