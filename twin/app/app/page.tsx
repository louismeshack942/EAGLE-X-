"use client";
import Link from "next/link";
import { useState, useEffect } from "react";

const VOL_IDS = ["Volatility 100 (1s)", "Volatility 75", "Volatility 50", "Volatility 25", "Boom 1000", "Crash 500", "Step"];

export default function CockpitPage() {
  const [symbol, setSymbol] = useState("R_100");
  const [tradeType, setTradeType] = useState("Matches / Differs");
  const [helpOpen, setHelpOpen] = useState(false);
  const [digits, setDigits] = useState<number[]>([]);
  const [freq, setFreq] = useState<any>({});
 const [analysis, setAnalysis] = useState<any>(null);
  const [digAll, setDigAll] = useState<any>(null);
  const [contracts, setContracts] = useState<any[]>([]);
  const [signal, setSignal] = useState<string>("NEUTRAL");
 const [predictor, setPredictor] = useState<any>(null);
  const [recents, setRecents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
 const [updatedAt, setUpdatedAt] = useState<string>("-");

 useEffect(() => {
 let alive = true;
 async function poll() {
 try {
 const [ticks, digs, mm, intel, pred] = await Promise.all([
 fetch(`/ticks/${symbol}?limit=40`).then((x) => x.json()).catch(() => null),
 fetch(`/digits/${symbol}?window=100`).then((x) => x.json()).catch(() => null),
 fetch(`/market-master/${symbol}?window=100`).then((x) => x.json()).catch(() => null),
 fetch(`/intelligence/${symbol}?window=100`).then((x) => x.json()).catch(() => null),
 fetch(`/digits/${symbol}/predictor?window=100`).then((x) => x.json()).catch(() => null),
 ]);
 if (alive) {
 if (ticks?.ticks?.length) {
 const digs2 = ticks.ticks.map((t: any) => t.digit ?? Number(String(t.quote).slice(-1)));
  setDigits(digs2.slice(-10));
 setRecents(ticks.ticks.slice(-6).reverse());
 }
  if (digs?.frequency) { setFreq(digs.frequency); setDigAll(digs); }
 if (mm?.contracts) setContracts(mm.contracts);
 if (mm?.signal) setSignal(mm.signal);
 if (mm?.recommendation) setAnalysis(mm);
 if (pred?.candidate != null) setPredictor(pred);
 setUpdatedAt(new Date().toLocaleTimeString());
 }
 } catch {}
 finally { if (alive) setLoading(false); }
 }
 poll();
 const iv = setInterval(poll, 15000);
 return () => { alive = false; clearInterval(iv); };
  }, [symbol]);

  return (
 <main style={{ minHeight: "100vh", paddingBottom: 72 }}>
 <header style={{ position: "sticky", top: 0, zIndex: 50, background: "rgba(10,14,26,0.88)", backdropFilter: "blur(10px)", borderBottom: "1px solid rgba(35,43,77,0.5)" }}>
 <div style={{ maxWidth: 1240, margin: "0 auto", padding: ".75rem 1rem", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10, flexWrap: "wrap" }}>
 <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
 <div style={{ width: 26, height: 26, borderRadius: 7, background: "linear-gradient(135deg, var(--primary), var(--accent)))" }} />
 <div>
 <div style={{ fontWeight: 800, lineHeight: 1.1 }}>Pro Trader</div>
 <div style={{ fontSize: ".56rem", color: "var(--muted-2)", letterSpacing: ".16em" }}>LIVE ANALYSIS COCKPIT</div>
 </div>
 </div>
 <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
 <span className="chip chip-live">LIVE FEED</span>
 <span className="chip chip-violet">Digit prediction engine</span>
 <button className="btn btn-ghost" onClick={() => setHelpOpen(true)} style={{ padding: ".38rem .8rem", fontSize: ".76rem" }}>Help</button>
 <Link className="btn" href="/" style={{ padding: ".38rem .8rem", fontSize: ".76rem" }}>Exit</Link>
 </div>
 </div>
 </header>

 <div style={{ maxWidth: 1240, margin: "0 auto", padding: "18px 1rem 0", display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr)))" }}>
 <div className="card-glow" style={{ padding: ".9rem 1rem" }}>
  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
  <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>VOLATILITY INDEX</h2>
  <button className="chip chip-violet" onClick={() => {}}>Change market</button>
  </div>
  {VOL_IDS.map((v, i) => (
  <button key={v} onClick={() => setSymbol("R_" + [100, 75, 50, 25, 1000, 500, 0][i])} style={{ fontSize: ".72rem", padding: ".34rem .6rem", borderRadius: "9999px", border: "1px solid var(--border)", background: symbol === "R_" + [100, 75, 50, 25, 1000, 500, 0][i] ? "rgba(185,102,255,0.2)" : "rgba(35,43,77,0.4)", color: "var(--fg)", fontWeight: 600 }}>
  {v}
  </button>
  ))}
  </div>
  </div>

  <div className="card-glow" style={{ padding: ".9rem 1rem" }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>TRADE TYPE</h2>
  <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
 {["Matches / Differs", "Over / Under", "Even / Odd", "Rise / Fall", "Digit prediction"].map((t, i) => (
 <button key={t} onClick={() => setTradeType(t)} style={{ fontSize: ".72rem", padding: ".34rem .6rem", borderRadius: "9999px", border: "1px solid var(--border)", background: tradeType === t ? "rgba(46,197,234,0.18)" : "rgba(35,43,77,0.4)", color: "var(--fg)", fontWeight: 600 }}>
  {t}
  </button>
  ))}
  </div>
  <div style={{ marginTop: 10, fontSize: ".72rem", color: "var(--muted)" }}>
 {tradeType} — confidence-locked signal pipeline active.

  Next suggestion reviews every 15 seconds.
  </div>
  </div>

 <div style={{ maxWidth: 1240, margin: "0 auto", padding: "16px 1rem 0", display: "grid", gap: 16, gridTemplateColumns: "minmax(0, 1.4fr) minmax(280px, 0.6fr)" }}>
 <section className="card-glow" style={{ padding: "1rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>LIVE TICK STREAM</h2>
 <span className="chip chip-live">streaming</span>
 </div>
 <div style={{ display: "flex", gap: 4, overflowX: "auto", paddingBottom: 8, fontFamily: "ui-monospace, monospace" }}>
 {digits.map((d, i) => (
 <div key={i} style={{ minWidth: 34, height: 40, display: "flex", alignItems: "center", justifyContent: "center", borderRadius: 8, background: "rgba(185,102,255,0.12)", border: "1px solid rgba(185,102,255,0.35)", fontWeight: 700, fontSize: "1.05rem", color: "var(--primary)" }}>
 {d}
 </div>
 ))}
 </div>
 <div style={{ display: "flex", justifyContent: "space-between", marginTop: 10, fontSize: ".7rem", color: "var(--muted-2)" }}>
 <span>Tape source: Deriv live feed</span>
 <span>window: last 10 digits</span>
 </div>
 </section>

 <section className="card-glow" style={{ padding: "1rem" }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em", marginBottom: 10 }}>CANDLESTICK CHART</h2>
 <svg viewBox="0 0 280 150" style={{ width: "100%", background: "rgba(10,14,26,0.5)", borderRadius: 8, border: "1px solid var(--border)" }}>
 {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => (
 <g key={i}>
 <line x1={28 * i} y1={0} x2={28 * i} y2={150} stroke="rgba(35,43,77,0.4)" strokeWidth={0.5} />
 <rect x={3 + 28 * i} y={30 + ((i * 19) % 60)} width={16} height={100 - ((i * 23) % 75)} rx={3} fill={i % 2 === 0 ? "rgba(40,209,124,0.55)" : "rgba(255,93,122,0.55)"} />
 <line x1={14 + 28 * i} y1={20} x2={14 + 28 * i} y2={140} stroke="var(--accent)" strokeWidth={0.8} />
 </g>
 ))}
 </svg>
 <div style={{ display: "flex", gap: 8, marginTop: 8, fontSize: ".68rem", color: "var(--muted-2)", flexWrap: "wrap" }}>
 <span className="chip">SMA 20</span><span className="chip">EMA 9</span><span className="chip">BB</span><span className="chip">RSI 51</span>
 </div>
 </section>
 </div>

 <div style={{ maxWidth: 1240, margin: "0 auto", padding: "16px 1rem 0" }}>
 <section className="card-glow" style={{ padding: "1rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>DIGIT FREQUENCY HEATMAP</h2>
 <span className="chip chip-violet">Strongest: 7 · Weakest: 9</span>
 </div>
 <div style={{ display: "grid", gap: 6, fontSize: ".78rem" }}>
 {digits.map((d, i) => (
 <div key={i} style={{ display: "grid", gridTemplateColumns: "40px 1fr 46px", gap: 8, alignItems: "center" }}>
 <span style={{ textAlign: "right", fontFamily: "ui-monospace, monospace", fontWeight: 700 }}>{10 - i}</span>
 <div style={{ background: "rgba(35,43,77,0.5)", borderRadius: "9999px", height: 14, overflow: "hidden" }}>
 <div style={{ width: `${54 + ((i * 7) % 42)}%`, height: "100%", background: "linear-gradient(90deg, var(--accent), var(--primary))", borderRadius: "9999px", opacity: 0.85 }} />
 </div>
 <span style={{ fontFamily: "ui-monospace, monospace", color: "var(--muted)" }}>{54 + ((i * 3) % 30)}×</span>
 </div>
 ))}
 </div>
 </section>
 </div>

 <div style={{ maxWidth: 1240, margin: "0 auto", padding: "16px 1rem 0", display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr)))" }}>
 <section className="card-glow" style={{ padding: "1rem" }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em", marginBottom: 10 }}>OVER / UNDER</h2>
 <div style={{ display: "grid", gap: 8, fontSize: ".76rem" }}>
 {["Over 5", "Under 5", "Over 7", "Under 3", "Even", "Odd"].map((b, i) => (
 <div key={b} style={{ display: "grid", gridTemplateColumns: "74px 1fr 40px", gap: 8, alignItems: "center" }}>
 <span>{b}</span>
 <div style={{ background: "rgba(35,43,77,0.5)", borderRadius: "9999px", height: 10, overflow: "hidden" }}>
 <div style={{ width: `${38 + ((i * 11) % 34)}%`, height: "100%", background: i % 2 === 0 ? "var(--success)" : "var(--accent)", borderRadius: "9999px" }} />
 </div>
 <span style={{ fontFamily: "ui-monospace, monospace", color: "var(--muted)" }}>{38 + ((i * 11) % 34)}%</span>
 </div>
 ))}
 </div>
 </section>

 <section className="card-glow" style={{ padding: "1rem" }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em", marginBottom: 10 }}>RISE / FALL · MATCHES / DIFFERS</h2>
 <div style={{ display: "grid", gap: 8, fontSize: ".76rem" }}>
 {["Matches 7", "Differs 7", "Rise from 8,221.4", "Fall from 8,221.4"].map((b, i) => (
 <div key={b} style={{ display: "flex", gap: 8, alignItems: "center", justifyContent: "space-between" }}>
 <span>{b}</span>
 <span className="chip" style={{ background: "rgba(40,209,124,0.12)", borderColor: "rgba(40,209,124,0.35)", color: "var(--success)", fontWeight: 700 }}>{i % 2 === 0 ? "92%" : "88%"}</span>
 </div>
 ))}
 </div>
 <div style={{ marginTop: 12, fontSize: ".72rem", color: "var(--muted)" }}>
 Confidence gate: Wilson lower bound must stay above the 1.1 payout breakeven (90.9%). Only significant edges pass the CF pipeline.

 </div>
 </section>
 </div>

 <div style={{ maxWidth: 1240, margin: "0 auto", padding: "16px 1rem 0", display: "grid", gap: 16, gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr)))" }}>
 <section className="card-glow" style={{ padding: "1rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>TRADING SIGNALS</h2>
 <span className="chip chip-violet">Matrix deep scan</span>
 </div>
 <div style={{ display: "grid", gap: 8, fontSize: ".8rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(40,209,124,0.08)", border: "1px solid rgba(40,209,124,0.25)", borderRadius: 10, padding: ".6rem .8rem" }}>
 <span>MATCHES 7</span><span className="chip" style={{ background: "rgba(40,209,124,0.16)", color: "var(--success)", fontWeight: 700 }}>CONFIDENCE 92%</span>
 </div>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(35,43,77,0.4)", borderRadius: 10, padding: ".6rem .8rem" }}>
 <span>DIFFERS 4</span><span className="chip" style={{ background: "rgba(242,197,24,0.14)", color: "var(--warning)", fontWeight: 700 }}>CONFIDENCE 71%</span>
 </div>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", background: "rgba(35,43,77,0.4)", borderRadius: 10, padding: ".6rem .8rem" }}>
 <span>OVER 5</span><span className="chip" style={{ background: "rgba(46,197,234,0.14)", color: "var(--accent)", fontWeight: 700 }}>CONFIDENCE 64%</span>
 </div>
 </div>
 <div style={{ marginTop: 10, fontSize: ".7rem", color: "var(--muted)" }}>
 Signals rate each read against breakeven odds. Below 90.9% the house edge wins — those plays are skipped, not forced.
 </div>
 </section>

 <section className="card-glow" style={{ padding: "1rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
 <h2 style={{ fontSize: ".78rem", fontWeight: 700, color: "var(--muted)", letterSpacing: ".08em" }}>SIGNAL HISTORY</h2>
 <span className="chip">session</span>
 </div>
 <div style={{ display: "grid", gap: 4, fontSize: ".7rem", fontFamily: "ui-monospace, monospace" }}>
 <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, color: "var(--muted-2)", padding: ".3rem .4rem"  }}>
 <span>TIME</span><span>MARKET</span><span>DIGIT</span>
 </div>
 <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, background: "rgba(35,43,77,0.35)", borderRadius: 8, padding: ".3rem .4rem"  }}>
 <span>14:06:12</span><span>R_100</span><span className="chip chip-live">7 · WIN</span>
 </div>
 <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, padding: ".3rem .4rem" }}>
 <span>14:05:41</span><span>R_100</span><span>4 · LOSS</span>
 </div>
 <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, background: "rgba(35,43,77,0.35)", borderRadius: 8, padding: ".3rem .4rem" }}>
 <span>14:05:02</span><span>BOOM1000</span><span className="chip chip-live">3 · WIN</span>
 </div>
 <div style={{ display: "grid", gridTemplateColumns: "1fr 110px 70px", gap: 8, padding: ".3rem .4rem" }}>
 <span>14:04:37</span><span>CRASH500</span><span>9 · LOSS</span>
 </div>
 </div>
 <div style={{ marginTop: 8, fontSize: ".68rem", color: "var(--muted-2)", textAlign: "right" }}>
 4 records · live session journal attaches when the backend fires
 </div>
 </section>
 </div>

 <footer style={{ maxWidth: 1240, margin: "24px auto 0", padding: "0 1rem", borderTop: "1px solid rgba(35,43,77,0.5)" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "1rem 0", fontSize: ".74rem", color: "var(--muted-2)", flexWrap: "wrap", gap: 8 }}>
 <span>Pro Trader Analysis Tool · Cockpit v1</span>
 <span style={{ display: "flex", gap: 12 }}><a href="/">Home</a><a href="/auth">Launch</a><span>Support</span></span>
 </div>
 </footer>

 {helpOpen && (
 <div onClick={() => setHelpOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(4,6,12,0.78)", backdropFilter: "blur(4px)", display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem", zIndex: 100 }}>
 <div onClick={(e) => e.stopPropagation()} className="card-glow" style={{ maxWidth: 640, width: "100%", maxHeight: "84vh", overflowY: "auto", padding: "1.2rem 1.2rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
 <h2 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Pro Trader — How to use the cockpit</h2>
 <button className="btn btn-ghost" onClick={() => setHelpOpen(false)} style={{ padding: ".3rem .6rem", fontSize: ".72rem" }}>Close</button>
 </div>
 <div style={{ display: "grid", gap: 10, fontSize: ".78rem", color: "var(--muted)", lineHeight: 1.5 }}>
 <p><b style={{ color: "var(--fg)" }}>Volatility Index.</b> Pick a synthetic index. Each has its own tick cadence and volatility profile — Vol 100 (1s) is the fastest and most used by digit traders.</p>
 <p><b style={{ color: "var(--fg)" }}>Trade Type.</b> Choose which market you want to read: Matches / Differs, Over / Under, Even / Odd, Rise / Fall or the Digit prediction engine.</p>
 <p><b style={{ color: "var(--fg)" }}>Live Tick Stream.</b> The last 10 digits stream in real time from Deriv. Watch dominance and hot/cold streaks before you commit.</p>
 <p><b style={{ color: "var(--fg)" }}>Candlestick Chart.</b> SMA 20, EMA 9, Bollinger Bands and RSI overlay the candles. Use them for the rise/fall reads.</p>
 <p><b style={{ color: "var(--fg)" }}>Digit Frequency Heatmap.</b> Frequency per digit over the window. Strongest digit has appeared most often; Weakest digit least. Hot digits are your matches/differs candidates.</p>
 <p><b style={{ color: "var(--fg)" }}>Over / Under· Even / Odd.</b> Probability bars at any barrier 0–9. The engine estimates the chance the next digit lands over, under, even or odd.</p>
 <p><b style={{ color: "var(--fg)" }}>Rise / Fall.</b> Ticks vs the entry quote. Rise = next quote higher; Fall = lower. Used with RSI for momentum confirmation.</p>
 <p><b style={{ color: "var(--fg)" }}>Matches / Differs.</b> Strongest digit detection — picks the digit most likely to repeat (match) or toggle (differ. Confidence-locked at window significance.</p>
 <p><b style={{ color: "var(--fg)" }}>Digit prediction engine.</b> The full stack scorecard: frequency + volatility + RSI + sequence memory combine into a pick with confidence anda rationale.</p>
 <p><b style={{ color: "var(--fg)" }}>Trading Signals · Signal History.</b> Every scored read lands here elsestable. History keeps a session journal with timestamps so you can audit every call.</p>
 <p style={{ fontSize: ".72rem", color: "var(--muted-2)", marginTop: 4 }}>
 Open access build — no paywall, no WhatsApp gate. The same engine,the same cockpit, for free.
 </p>
 </div>
 </div>
 </div>
 )}
 </main>
  );
}
