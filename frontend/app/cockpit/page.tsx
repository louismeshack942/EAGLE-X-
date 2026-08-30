"use client";

import { useCallback, useEffect, useState } from "react";
import AnalysisPanel from "@/components/AnalysisPanel";
import AutomationPanel from "@/components/AutomationPanel";
import ExecutionPanel from "@/components/ExecutionPanel";
import LiveChart, { type ChartPoint } from "@/components/LiveChart";
import { apiGet, apiPost, ApiError, type MarketRow, type ServerStatus, type TickRow } from "@/lib/api";

type Conn = { symbol: string; state: string; latest?: TickRow };

const FAMILIES = [
  { id: "EVEN", label: "Even", desc: "Last digit even" },
  { id: "ODD", label: "Odd", desc: "Last digit odd" },
  { id: "OVER", label: "Over", desc: "Above barrier" },
  { id: "UNDER", label: "Under", desc: "Below barrier" },
  { id: "MATCHES", label: "Matches", desc: "Exact digit" },
  { id: "DIFFERS", label: "Differs", desc: "Not exact digit" },
];

const WINDOWS = [25, 50, 100,  250,  500,  1000];

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

function connChip(state: string, source: string) {
  let cls = "chip off";
  let txt = "DISCONNECTED";
  if (state === "connected") {
    cls = "chip ok";
    txt = "CONNECTED";
  } else if (state === "connecting" || state === "reconnecting") {
    cls = "chip busy";
    txt = "CONNECTING";
  }
  const src = source === "deriv_live" ? { c: "chip live", t: "LIVE" } : { c: "chip demo", t: "DEMO" };
  return (
    <span className={cls}>
      <span className="dot" />
      {txt}
    </span>
  );
}

export default function CockpitPage() {
  const [markets, setMarkets] = useState<MarketRow[]>([]);
  const [selected, setSelected] = useState("R_100");
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [ticks, setTicks] = useState<TickRow[]>([]);
  const [conn, setConn] = useState<Conn | null>(null);
  const [wsState, setWsState] = useState("disconnected");
  const [window, setWindow] = useState(100);
  const [activeFamily, setActiveFamily] = useState("EVEN");
  const [error, setError] = useState("");

  const refreshStatus = useCallback(async () => {
    try {
      const s = await apiGet<ServerStatus>("/api/status");
      setStatus(s);
      if (s.connection && s.connection.length > 0) setConn(s.connection[0]);
    } catch {}
  }, []);

  useEffect(() => {
    apiGet<{ markets: MarketRow[] }>("/api/markets")
      .then((d) => setMarkets(d.markets))
      .catch(() => setError("Failed to load markets"));
    refreshStatus();
    const iv = setInterval(refreshStatus, 5000);
    return () => clearInterval(iv);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // reset stale ticks when market changes
    setTicks([]);
    setConn(null);
    refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

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
          setTicks((prev) => [...prev.slice(-299), t]);
        } else if (msg.type === "status") {
          setConn(toConn(msg.data));
        }
      } catch {}
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
        setError("Network error — check connection.");
      }
    }
  };

  const chartPoints: ChartPoint[] = ticks.map((t) => ({
    time: Math.floor(t.epoch_ms / 1000),
    value: t.quote,
  }));

  const n = ticks.length || 1;
  const digitCounts = Array.from({ length: 10 }, (_, d) => {
    const cnt = ticks.filter((t) => t.digit === d).length;
    return { d, cnt, pct: Math.round((cnt / n) * 1000) / 10 };
  });
  const maxCnt = Math.max(...digitCounts.map((x) => x.cnt),  1);
  const pickDigit = digitCounts.reduce((a, b) => (b.cnt > a.cnt ? b : a), digitCounts[0]);
  const recentDigits = ticks.slice(-12).map((t) => t.digit);
  const parity = ticks.reduce((acc, t) => {
    if (t.digit < 0) return acc;
    acc[t.digit %  2 ===  0 ? "even" : "odd"]++;
    return acc;
  }, { even:  0, odd:  0 });
  const over = ticks.filter((t) => t.digit >  4).length;
  const under = ticks.filter((t) => t.digit <  4).length;
  const sameStreak = (() => {
    let best =  0, cur =  0, last = -1;
    for (const t of ticks) {
      const d = t.digit;
      if (d === last && d >=  0) { cur++; best = Math.max(best, cur); } else { cur =  1; }
      last = d;
    }
    return best;
  })();

  const dataSource = status?.data_source ?? "unknown";
  const connected = conn?.state === "connected";
  const signalActive = connected && ticks.length >= 50;

  return (
    <div>
      {/* ===== top navigation ===== */}
      <header className="topbar">
        <div className="brand">
          <span className="mark">▲</span>
          <span>EAGLE-X</span>
        </div>
        <span className="sep" />
        <select
          className="sel"
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Market"
        >
          {markets.map((m) => (
            <option key={m.symbol} value={m.symbol}>
              {m.symbol} — {m.name}
            </option>
          ))}
        </select>
        <button className="btn sm" onClick={() => connect("harness")} title="Start demo feed">
          Connect
        </button>
        <button className="btn sm ghost" onClick={() => connect("live")} title="Connect Deriv live data">
          Live
        </button>
        <span className="sp" style={{ flex: 1 }} />
        {connChip(connected ? "connected" : wsState === "open" ? "connecting" : "disconnected", dataSource)}
        <span className={dataSource === "deriv_live" ? "chip live" : "chip demo"}>
          {dataSource === "deriv_live" ? "LIVE DATA" : "DEMO DATA"}
        </span>
        <span className="chip neutral" title="Refresh">
          <button className="btn sm ghost" onClick={refreshStatus}>Refresh</button>
        </span>
        <button
          className="btn sm ghost"
          onClick={async () => {
            try { await fetch("/auth/logout", { method: "POST" }); } finally { location.href = "/"; }
          }}
        >
          Logout
        </button>
      </header>

      <div className="page">
        {error && <div className="state" style={{ color: "var(--bad)" }}>{error}</div>}

        {/* ===== signal hero ===== */}
        <section className="hero">
          <div className="h-main">
            <div className="h-state">
              {signalActive ? "SIGNAL ACTIVE" : connected ? "ANALYZING…" : "STANDBY"}
            </div>
            <div className="h-title">
              {signalActive ? `${pickDigit?.d ?? "—"} ${pickDigit?.cnt ? "SELECTED" : ""}` : "Synthetic Index Analysis"}
            </div>
            <div className="h-sub">
              {selected} · {activeFamily} · last tick {ticks.length ? ticks[ticks.length - 1].quote.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
            </div>
            <div className="meter">
              <div className="m"><b>{ticks.length}</b><span>Ticks</span></div>
              <div className="m"><b>{pickDigit?.d ?? "—"}</b><span>Pick</span></div>
              <div className="m"><b>{pickDigit?.pct ?? 0}%</b><span>Confidence</span></div>
              <div className="m"><b>{parity.even}</b><span>Even</span></div>
              <div className="m"><b>{parity.odd}</b><span>Odd</span></div>
              <div className="m"><b>{over}</b><span>Over 4</span></div>
              <div className="m"><b>{under}</b><span>Under 4</span></div>
              <div className="m"><b>{sameStreak}</b><span>Streak</span></div>
            </div>
          </div>
          <div className="h-right" style={{ display: "grid", placeItems: "center", gap: ".35rem" }}>
            <div className="gauge">
              <svg width="110" height="110" viewBox="0 0 110 110">
                <circle cx="55" cy="55" r="46" fill="none" stroke="#22304a" strokeWidth="10" />
                <circle
                  cx="55" cy="55" r="46" fill="none"
                  stroke="url(#gg)"
                  strokeWidth="10"
                  strokeLinecap="round"
                  strokeDasharray={`${Math.max(0, Math.min(100, pickDigit?.pct ?? 0) * 2.89)} 289`}
                />
                <defs>
                  <linearGradient id="gg" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0%" stopColor="#4f8ef7" />
                    <stop offset="100%" stopColor="#8b7cf8" />
                  </linearGradient>
                </defs>
              </svg>
              <div className="gv">{pickDigit?.pct ?? 0}%</div>
            </div>
          </div>
        </section>

        {/* ===== digit analytics ===== */}
        <section className="stage">
          <div className="stage-h">
            <span>Digit Analytics</span>
            <span className="chip neutral">window {window}</span>
            <span className="sp" />
            <select className="sel" value={window} onChange={(e) => setWindow(Number(e.target.value))}>
              {WINDOWS.map((w) => (
                <option key={w} value={w}>{w}</option>
              ))}
            </select>
          </div>
          <div className="digits">
            {digitCounts.map((x) => (
              <div key={x.d} className={`digit-tile ${x.d === pickDigit?.d ? "pick" : ""} ${x.cnt === 0 ? "zero" : ""}`}>
                <div>{x.d}</div>
                <div className="d-pct">{x.pct}%</div>
                <div className="d-bar"><i style={{ width: `${(x.cnt / maxCnt * 100)}%` }} /></div>
              </div>
            ))}
          </div>
          <div className="row" style={{ marginTop: ".9rem", gap: ".35rem", flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: ".74rem" }}>Recent:</span>
            {recentDigits.length ? recentDigits.map((d, i) => (
              <span key={i} className="chip neutral" style={{ padding: ".15rem .45rem", fontSize: ".72rem" }}>{d}</span>
            )) : <span className="muted">— connect for data</span>}
          </div>
        </section>

        {/* ===== market distribution ===== */}
        <section className="stage">
          <div className="stage-h">
            <span>Market Distribution</span>
            <span className="sp" />
            <span className="chip neutral">parity</span>
          </div>
          <div className="grid g3">
            <div className="kpi">
              <span className="k">Even</span>
              <b>{parity.even} <span className="muted" style={{ fontSize: ".78rem" }}>({Math.round((parity.even / n * 100) * 10) / 10}%)</span></b>
            </div>
            <div className="kpi">
              <span className="k">Odd</span>
              <b>{parity.odd} <span className="muted" style={{ fontSize: ".78rem" }}>({Math.round((parity.odd / n * 100) * 10) / 10}%)</span></b>
            </div>
            <div className="kpi">
              <span className="k">Over / Under 4</span>
              <b>{over} / {under}</b>
            </div>
            <div className="kpi">
              <span className="k">Same-digit streak</span>
              <b>{sameStreak}</b>
            </div>
            <div className="kpi">
              <span className="k">Mode digit</span>
              <b>{pickDigit?.d ?? "—"}</b>
            </div>
            <div className="kpi">
              <span className="k">Data</span>
              <b className="muted">{n} ticks</b>
            </div>
          </div>
        </section>

        {/* ===== chart ===== */}
        <section className="stage">
          <div className="stage-h">
            <span>Price Chart</span>
            <span className="sp" />
            <span className="chip neutral">Candles · EMA 20 · Bollinger 20 · RSI 14</span>
          </div>
          {ticks.length ? (
            <LiveChart points={chartPoints} />
          ) : (
            <div className="state">No data yet — click Connect to start the feed.</div>
          )}
        </section>

        {/* ===== contracts + workflow ===== */}
        <section className="stage">
          <div className="stage-h">
            <span>Contracts</span>
            <span className="sp" />
            <span className="chip neutral">pick a family to focus the signal</span>
          </div>
          <div className="con-grid">
            {FAMILIES.map((f) => (
              <button
                key={f.id}
                className={`con ${activeFamily === f.id ? "pick" : ""}`}
                onClick={() => setActiveFamily(f.id)}
              >
                <div className="cn">{f.label}</div>
                <div className="cd">{f.desc}</div>
              </button>
            ))}
          </div>
          <div className="flow" style={{ marginTop: ".9rem" }}>
            <span className={signalActive ? "step done" : "step on"}><span className="ix">1</span>Analyze</span>
            <span className="pin">→</span>
            <span className={signalActive ? "step on" : "step"}><span className="ix">2</span>Signal</span>
            <span className="pin">→</span>
            <span className="step"><span className="ix">3</span>Proposal</span>
            <span className="pin">→</span>
            <span className="step"><span className="ix">4</span>Trade</span>
          </div>
          {!signalActive && (
            <p className="empty-note" style={{ marginTop: ".6rem" }}>
              The 30-second signal window runs once enough live ticks are collected. Hold until confidence passes 70%% before entering a trade — matching the target tool's documented rule.

            </p>
          )}
        </section>

        {/* ===== analysis + execution ===== */}
        <section className="stage">
          <div className="stage-h">
            <span>Analysis</span>
            <span className="sp" />
          </div>
          <AnalysisPanel symbol={selected} />
        </section>

        <section className="stage">
          <div className="stage-h">
            <span>Signal Pipeline</span>
            <span className="sp" />
          </div>
          <ExecutionPanel symbol={selected} />
        </section>

        {/* ===== automation (collapsible, not dominant ===== */}
        <details className="aut">
          <summary>🤖 Trading Automation <span className="muted" style={{ fontWeight: 400 }}>— advanced settings</span></summary>
          <div className="aut-inner">
            <AutomationPanel symbol={selected} />
          </div>
        </details>
      </div>
    </div>
  );
}
