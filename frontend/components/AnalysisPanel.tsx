"use client";

// Phase 2 + 3 analysis panel. Pure readonly: fetches statistical digit analysis
// (/api/analysis/*) and contract recommendations (/api/quick-analysis, /api/scan).
// All sources are surfaced honestly (DATA SOURCE + proposal source badges).

import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "@/lib/api";

type WindowAnalysis = {
  n: number;
  size?: number;
  data_quality: { state: string; source: string };
  digit_frequency?: any;
  parity?: any;
  gaps?: any;
  streaks?: any;
  over_under?: any;
  chi_square?: any;
};

type AnalysisSnapshot = {
  symbol: string;
  source: string;
  connection_state: string;
  windows: Record<string, WindowAnalysis>;
  multi_window: any;
};

type Recommendation = {
  state: string;
  reason: string;
  observed_win_rate?: number | null;
  sample_size: number;
  ev?: number | null;
  payout?: number | null;
  breakeven_win_rate?: number | null;
  proposal_source?: string;
  data_source?: string;
};

type QuickAnalysis = {
  family: string;
  barrier: number | null;
  prediction: string;
  recommendation: Recommendation;
  proposal_source: string;
  data_source: string;
  readonly_note: string;
};

const WINDOW_OPTIONS = [25, 50, 100, 250, 500, 1000];
const FAMILIES = ["MATCHES", "DIFFERS", "ODD", "EVEN", "OVER", "UNDER"];

const STATE_CLS: Record<string, string> = {
  QUALIFIED: "badge live",
  WATCH: "badge harness",
  "NO TRADE": "badge disconnected",
  "INSUFFICIENT DATA": "badge disconnected",
};
const QUALITY_CLS: Record<string, string> = {
  DATA_READY: "badge live",
  INSUFFICIENT_DATA: "badge harness",
  STALE: "badge disconnected",
  DISCONNECTED: "badge disconnected",
  INVALID: "badge disconnected",
};

function sourceBadge(source: string) {
  const cls =
    source === "deriv_live" || source === "LIVE" ? "badge live"
    : source === "harness" || source === "HARNESS" ? "badge harness"
    : "badge disconnected";
  return (
    <span className={cls}>
      SOURCE: {typeof source === "string" ? source.toUpperCase() : String(source)}
    </span>
  );
}

export default function AnalysisPanel({ symbol }: { symbol: string }) {
  const [tab, setTab] = useState<"stats" | "contracts" | "scan">("stats");
  const [window, setWindow] = useState(100);
  const [snap, setSnap] = useState<AnalysisSnapshot | null>(null);
  const [qa, setQa] = useState<QuickAnalysis[]>([]);
  const [scan, setScan] = useState<any>(null);
  const [family, setFamily] = useState("DIFFERS");
  const [barrier, setBarrier] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const s = await apiGet<AnalysisSnapshot>(
        `/api/analysis/${symbol}?window=${window}`
      );
      setSnap(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Failed to load analysis.");
    } finally {
      setLoading(false);
    }
  }, [symbol, window]);

  const runQuick = async (familyArg: string, barrierArg: number) => {
    setLoading(true);
    setError("");
    try {
      const r = await apiGet<QuickAnalysis>(
        `/api/quick-analysis?symbol=${symbol}&family=${familyArg}&barrier=${barrierArg}&window=${window}`
      );
      const existsIdx = qa.findIndex(
        (q) => q.family === familyArg && q.barrier === barrierArg
      );
      if (existsIdx >= 0) {
        setQa((prev) => {
          const next = [...prev];
          next[existsIdx] = r;
          return next;
        });
      } else {
        setQa((prev) => [...prev, r].slice(-8));
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Quick analysis failed.");
    } finally {
      setLoading(false);
    }
  };

  const runScan = async () => {
    setLoading(true);
    setError("");
    try {
      const r = await apiGet<any>(`/api/scan/${symbol}?window=${window}`);
      setScan(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Scan failed.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, window]);

  const wa = snap?.windows?.[String(window)];
  const df = wa?.digit_frequency;
  const qualityState = wa?.data_quality?.state ?? "INSUFFICIENT_DATA";

  return (
    <div>
      <div className="row" style={{ marginBottom: ".6rem", flexWrap: "wrap" }}>
        {(["stats", "contracts", "scan"] as const).map((t) => (
          <button
            key={t}
            className={`btn secondary ${tab === t ? "active" : ""}`}
            onClick={() => setTab(t)}
          >
            {t === "stats" ? "Stats" : t === "contracts" ? "Contracts" : "Scan"}
          </button>
        ))}
        <span style={{ flexGrow: 1 }} />
        <label className="muted">
          window:{" "}
          <select
            className="sel"
            value={window}
            onChange={(e) => setWindow(Number(e.target.value))}
          >
            {WINDOW_OPTIONS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </label>
        {snap && sourceBadge(snap.source)}
        <span className="badge" style={{ borderColor: "var(--border)" }}>
          {qualityState}
        </span>
      </div>

      {error && (
        <div className="state error" role="alert">
          {error}
        </div>
      )}

      {tab === "stats" && (
        <div>
          {loading ? (
            <div className="state">COMPUTING…</div>
          ) : !wa || wa.n === 0 ? (
            <div className="placeholder">
              NO DATA YET — connect the {symbol} market to populate the window.
            </div>
          ) : (
            <>
              <div className="grid grid-2">
                <section className="card">
                  <p className="panel-title">Digit frequency (n={wa.n})</p>
                  <div className="grid grid-4" style={{ gap: ".4rem" }}>
                    {(df?.counts ?? []).map((c: number, d: number) => (
                      <div key={d} className={`dig ${c === 0 ? "zero" : ""}`}>
                        <div className="n">{d}</div>
                        <div className="pct">
                          {(df?.percentages?.[d] ?? 0).toFixed(1)}%
                        </div>
                      </div>
                    ))}
                  </div>
                  {df?.most_frequent != null && (
                    <div className="muted" style={{ marginTop: ".5rem" }}>
                      Mode: {df.most_frequent} · ranks{" "}
                      {JSON.stringify(df.rank)}
                    </div>
                  )}
                </section>

                <section className="card">
                  <p className="panel-title">Parity</p>
                  {wa.parity ? (
                    <div>
                      <div>
                        ODD: <b>{wa.parity.odd_percent}%</b> (n={wa.parity.odd_count})
                      </div>
                      <div>
                        EVEN: <b>{wa.parity.even_percent}%</b> (n={wa.parity.even_count})
                      </div>
                      <div className="muted" style={{ marginTop: ".4rem" }}>
                        Baseline 50/50 · dev {wa.parity.odd_deviation_pp >= 0 ? "+" : ""}
                        {wa.parity.odd_deviation_pp}pp ODD
                      </div>
                    </div>
                  ) : (
                    <div className="muted">—</div>
                  )}
                  {wa.streaks && (
                    <div className="muted" style={{ marginTop: ".6rem" }}>
                      Max same-digit streak: {wa.streaks.same_digit?.max_same_digit_streak} ·
                      parity streak now: {wa.streaks.parity?.current_parity} ×
                      {wa.streaks.parity?.current_parity_streak}
                    </div>
                  )}
                </section>
              </div>

              <div className="grid grid-2">
                <section className="card">
                  <p className="panel-title">Over / Under (barrier 4 fair 50/40)</p>
                  {wa.over_under?.[4] ? (
                    <table className="tbl">
                      <thead>
                        <tr>
                          <th>fam</th>
                          <th>count</th>
                          <th>%</th>
                          <th>fair %</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(["over", "under", "equal"] as const).map((k) => {
                          const o = wa.over_under[4];
                          return (
                            <tr key={k}>
                              <td>{k.toUpperCase()}</td>
                              <td>{o[`${k}_count`]}</td>
                              <td>{o[`${k}_percent`]}%</td>
                              <td>
                                {k === "over"
                                  ? o.fair_over_percent
                                  : k === "under"
                                    ? o.fair_under_percent
                                    : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  ) : (
                    <div className="muted">—</div>
                  )}
                </section>

                <section className="card">
                  <p className="panel-title">Uniformity (chi-square)</p>
                  {wa.chi_square?.applicable ? (
                    <div>
                      <div>
                        χ² = {wa.chi_square.statistic} · p = {wa.chi_square.p_value}
                      </div>
                      <div className="muted" style={{ marginTop: ".4rem" }}>
                        {wa.chi_square.interpretation}
                      </div>
                    </div>
                  ) : (
                    <div className="muted">Sample too small ({wa.n}).</div>
                  )}

                  <div className="muted" style={{ marginTop: ".8rem" }}>
                    Multi-window: <b>{snap?.multi_window?.state}</b>
                  </div>
                  {snap?.multi_window?.summary && (
                    <div className="muted">{snap.multi_window.summary}</div>
                  )}
                </section>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "contracts" && (
        <div>
          <section className="card" style={{ marginBottom: ".75rem" }}>
            <p className="panel-title">Quick analysis (read-only)</p>
            <div className="row">
              <select
                className="sel"
                value={family}
                onChange={(e) => setFamily(e.target.value)}
              >
                {FAMILIES.map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
              <select
                className="sel"
                value={barrier}
                onChange={(e) => setBarrier(Number(e.target.value))}
              >
                {Array.from({ length: 10 }, (_, i) => (
                  <option key={i} value={i}>
                    barrier {i}
                  </option>
                ))}
              </select>
              <button className="btn" onClick={() => runQuick(family, barrier)}>
                {loading ? "Pricing…" : "Analyze"}
              </button>
              <span className="muted">
                Prices via Deriv proposals when configured; otherwise simulated (HARNESS).
              </span>
            </div>
          </section>

          {qa.length === 0 ? (
            <div className="placeholder">Run a quick analysis to see recommendations.</div>
          ) : (
            <div className="grid grid-2">
              {qa.map((q, i) => renderQuick(q, i))}
            </div>
          )}
        </div>
      )}

      {tab === "scan" && (
        <div>
          <section className="card">
            <p className="panel-title">Board scan — read-only, no trades</p>
            <div className="row">
              <button className="btn" onClick={runScan}>
                {loading ? "Scanning…" : "Scan board"}
              </button>
            </div>
          </section>

          {scan && (
            <div style={{ marginTop: ".75rem" }}>
              <div className="row" style={{ flexWrap: "wrap", gap: ".5rem" }}>
                <span className="badge live">{scan.qualified.length} QUALIFIED</span>
                <span className="badge harness">{scan.watch.length} WATCH</span>
                <span className="badge disconnected">{scan.no_trade.length} NO TRADE</span>
                <span className="muted">{scan.readonly_note}</span>
              </div>
              {scan.top_candidates?.length > 0 ? (
                <table className="tbl" style={{ marginTop: ".6rem", width: "100%" }}>
                  <thead>
                    <tr>
                      <th>family</th>
                      <th>barrier</th>
                      <th>pwin</th>
                      <th>breakeven</th>
                      <th>EV</th>
                      <th>state</th>
                    </tr>
                  </thead>
                  <tbody>
                    {scan.top_candidates.map((r: any, i: number) => (
                      <tr key={i}>
                        <td>{r.family}</td>
                        <td>{r.barrier}</td>
                        <td>{r.observed_win_rate?.toFixed(3) ?? "—"}</td>
                        <td>{r.breakeven_win_rate?.toFixed(3) ?? "—"}</td>
                        <td>{r.ev?.toFixed(4) ?? "—"}</td>
                        <td>
                          <span className={STATE_CLS[r.state] ?? "badge"}>
                            {r.state}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div className="placeholder" style={{ marginTop: ".6rem" }}>
                  No qualifying candidates — that is the correct, capital-preserving outcome on a
                  fair board.
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );

  function renderQuick(q: QuickAnalysis, i: number) {
    const r = q.recommendation;
    return (
      <section key={i} className="card">
        <p className="panel-title">
          {q.family} {q.barrier != null ? `@ ${q.barrier}` : ""} — {r.state}
        </p>
        <div className="row">
          {q.proposal_source && sourceBadge(q.proposal_source)}
          <span className={STATE_CLS[r.state] ?? "badge"}>{r.state}</span>
        </div>
        <div className="muted" style={{ marginTop: ".4rem" }}>
          {r.reason}
        </div>
        <div className="muted" style={{ marginTop: ".6rem" }}>
          prediction {q.prediction} · sample {r.sample_size} · pwin{" "}
          {r.observed_win_rate?.toFixed(3) ?? "—"} · payout {r.payout ?? "—"} · EV{" "}
          {r.ev?.toFixed(4) ?? "—"}
        </div>
      </section>
    );
  }
}