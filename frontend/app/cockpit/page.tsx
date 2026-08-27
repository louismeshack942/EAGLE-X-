"use client";

import { useCallback, useEffect, useState } from "react";
import AnalysisPanel from "@/components/AnalysisPanel";
import AutomationPanel from "@/components/AutomationPanel";
import ExecutionPanel from "@/components/ExecutionPanel";
import LiveChart, { type ChartPoint } from "@/components/LiveChart";
import { apiGet, apiPost, ApiError, type MarketRow, type ServerStatus, type TickRow } from "@/lib/api";

type Conn = { symbol: string; state: string; latest?: TickRow };

const STATE_LABEL: Record<string, string> = {
  disconnected: "DISCONNECTED",
  connecting: "CONNECTING",
  connected: "CONNECTED",
  reconnecting: "RECONNECTING",
  auth_required: "AUTH REQUIRED",
  market_unavailable: "MARKET UNAVAILABLE",
};

// Normalize a tick payload, tolerating both `last_digit` and `digit` field names.
function toTick(raw: any): TickRow {
  return {
    epoch_ms: raw.epoch_ms ?? raw.epochMs ?? Date.now(),
    quote: raw.quote ?? 0,
    digit: typeof raw.digit === "number" ? raw.digit : raw.last_digit ?? -1,
    provider: raw.provider ?? "unknown",
  };
}

function toConn(raw: any): Conn {
  return {
    symbol: raw.symbol ?? "",
    state: raw.state ?? (raw.kind ? "connected" : "disconnected"),
    latest: raw.latest ?? undefined,
  };
}

function stateBadge(state: string, source: string) {
  const cls =
    state === "connected"
      ? source === "harness"
        ? "badge harness"
        : "badge live"
      : state === "connecting" || state === "reconnecting"
        ? "badge connecting"
        : "badge disconnected";
  const label = STATE_LABEL[state] ?? state;
  return <span className={cls}>{label}</span>;
}

export default function CockpitPage() {
  const [markets, setMarkets] = useState<MarketRow[]>([]);
  const [selected, setSelected] = useState("R_10");
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [ticks, setTicks] = useState<TickRow[]>([]);
  const [conn, setConn] = useState<Conn | null>(null);
  const [wsState, setWsState] = useState("disconnected");
  const [error, setError] = useState("");

  const refreshStatus = useCallback(async () => {
    try {
      const s = await apiGet<ServerStatus>("/api/status");
      setStatus(s);
      if (s.connection && s.connection.length > 0) setConn(s.connection[0]);
    } catch {
      /* transient */
    }
  }, []);

  useEffect(() => {
    apiGet<{ markets: MarketRow[] }>("/api/markets")
      .then((d) => setMarkets(d.markets))
      .catch(() => setError("Failed to load markets (server error)."));
    refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // WebSocket tick stream
  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/ticks`);
    ws.onopen = () => setWsState("open");
    ws.onclose = () => setWsState("closed");
    ws.onerror = () => setWsState("error");
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === "tick") {
          const t = toTick(msg.data);
          setTicks((prev) => [...prev.slice(-199), t]);
        } else if (msg.type === "status") {
          setConn(toConn(msg.data));
        }
      } catch {
        /* ignore malformed */
      }
    };
    return () => ws.close();
  }, []);

  const connect = async (mode: "live" | "harness") => {
    setError("");
    try {
      const r = await apiPost<Conn>("/api/connect", { symbol: selected, mode });
      setConn({ symbol: selected, state: r.state, latest: r.latest });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 503 && e.message.includes("AUTHORIZATION")) {
          setConn({ symbol: selected, state: "auth_required", latest: undefined });
        } else if (e.status === 404) {
          setConn({ symbol: selected, state: "market_unavailable", latest: undefined });
        } else {
          setError(`Server error: ${e.message}`);
        }
      } else {
        setError("Network error — connection lost.");
      }
    }
  };

  const chartPoints: ChartPoint[] = ticks.map((t) => ({
    time: Math.floor(t.epoch_ms / 1000),
    value: t.quote,
  }));

  const digitCounts = Array.from({ length: 10 }, (_, d) => ({
    d,
    count: ticks.filter((t) => t.digit === d).length,
  }));
  const n = ticks.length || 1;
  const dataSource = status?.data_source ?? "unknown";
  const connected = conn?.state === "connected";

  return (
    <div className="layout">
      <aside className="sidebar">
        <h3 style={{ margin: "0 0 .5rem" }}>🦅 EAGLE-X</h3>
        <a href="/cockpit/">Cockpit</a>
        <a href="/">Landing</a>
        <div style={{ flexGrow: 1 }} />
        <button
          className="btn secondary"
          onClick={async () => {
            try {
              await fetch("/auth/logout", { method: "POST" });
            } finally {
              location.href = "/";
            }
          }}
        >
          Logout
        </button>
      </aside>

      <div style={{ display: "flex", flexDirection: "column" }}>
        <header className="topbar">
          <div>Account</div>
          {conn ? stateBadge(conn.state, dataSource) : stateBadge("disconnected", dataSource)}
          {wsState === "open" ? (
            <span className="badge live">WS: OPEN</span>
          ) : (
            <span className="badge disconnected">WS: {wsState.toUpperCase()}</span>
          )}
          <div style={{ flexGrow: 1 }} />
          <div className="muted">DATA SOURCE: {dataSource.toUpperCase()}</div>
        </header>

        <main className="main">
          {error && (
            <div className="state error" role="alert">
              {error}
            </div>
          )}

          <section className="card">
            <p className="panel-title">Market connection</p>
            <div className="row">
              <select
                className="sel"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {markets.map((m) => (
                  <option key={m.symbol} value={m.symbol}>
                    {m.symbol} — {m.name}
                  </option>
                ))}
              </select>
              <button className="btn" onClick={() => connect("harness")}>
                Connect (Harness)
              </button>
              <button className="btn secondary" onClick={() => connect("live")}>
                Connect (Live)
              </button>
              <div className="muted">
                Live requires configured Deriv OAuth. Harness = development/simulation feed
                (never labeled real).
              </div>
            </div>
          </section>

          <div className="grid grid-2">
            <section className="card">
              <p className="panel-title">Price chart</p>
              {connected || ticks.length ? (
                <LiveChart points={chartPoints} empty={!ticks.length} />
              ) : (
                <div className="state">NO DATA — connect a market to start the tick chart</div>
              )}
            </section>

            <section className="card">
              <p className="panel-title">Last digit frequency (recent window)</p>
              {ticks.length === 0 ? (
                <div className="state">NO DATA</div>
              ) : (
                <div className="grid grid-4" style={{ gap: ".4rem" }}>
                  {digitCounts.map(({ d, count }) => (
                    <div key={d} className={`dig ${count === 0 ? "zero" : ""}`}>
                      <div className="n">{d}</div>
                      <div className="pct">{Math.round((count / n) * 100)}%</div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          <section className="card" style={{ marginBottom: ".75rem" }}>
            <p className="panel-title">Analysis & contracts (Phase 2 — statistical)</p>
            <AnalysisPanel symbol={selected} />
          </section>

          <section className="card">
            <p className="panel-title">Phase 4/5 — Signal pipeline & execution</p>
            <ExecutionPanel symbol={selected} />
          </section>

          <section className="card" style={{ marginBottom: ".75rem" }}>
            <p className="panel-title">Automated Trader (Phase 6)</p>
            <AutomationPanel symbol={selected} />
          </section>

          <section className="card">
            <p className="panel-title">Status log</p>
            {ticks.length === 0 ? (
              <div className="state">NO DATA</div>
            ) : (
              <div style={{ display: "flex", gap: ".5rem", flexWrap: "wrap" }}>
                {ticks.slice(-12).map((t, i) => (
                  <span key={i} className="badge" style={{ borderColor: "var(--border)" }}>
                    {t.digit}
                  </span>
                ))}
                {ticks.length > 0 && (
                  <span className="muted" style={{ marginLeft: "auto" }}>
                    provider: {ticks[ticks.length - 1].provider}
                  </span>
                )}
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}