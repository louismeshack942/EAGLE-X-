"use client";
import Link from "next/link";
import { useState, useEffect } from "react";

const MARKET_BTNS: Array<[string, string]> = [
  ["Volatility 100 (1s)", "R_100"],
  ["Volatility   75", "R_75"],
  ["Volatility   50", "R_50"],
  ["Volatility   25", "R_25"],
  ["Vol   10 (1s)", "1HZ10V"],
  ["Vol   25 (1s)", "1HZ25V"],
  ["Vol   30 (1s)", "1HZ30V"],
];
const TRADE_TYPES = ["Matches / Differs", "Over / Under", "Even / Odd", "Rise / Fall", "Digit prediction"];
function fmt(v: any, d =2): string {
  if (v == null || Number.isNaN(Number(v))) return "-";
  return v.toLocaleString(undefined, { minimumFractionDigits:d, maximumFractionDigits:d });
}
function smaVals(vals: number[], p: number): number[] {
  if (vals.length < p) return [];
  const out: number[] = [];
  for (let i = p - 1; i < vals.length; i++) {
    let s = 0;
    for (let j = i - p + 1; j <= i; j++) s += vals[j];
    out.push(s / p);
  }
  return out;
}
function emaVals(vals: number[], p: number): number[] {
  if (vals.length < p) return [];
const alpha =   2 / (p + 1);
const out: number[] = [vals.slice(0,p).reduce((a,b) => a + b,  0) / p];
for (let i = p; i < vals.length; i++) {
  out.push(alpha * vals[i] + (1 - alpha) * out[out.length - 1]);
}
return out;
}
export default function CockpitPage() {
 const [symbol, setSymbol] = useState<string>("R_100");
 const [tradeType, setTradeType] = useState<string>("Matches / Differs");
 const [helpOpen, setHelpOpen] = useState(false);
 const [ticks, setTicks] = useState<any>(null);
 const [digs, setDigs] = useState<any>(null);
 const [mm, setMm] = useState<any>(null);
 const [intel, setIntel] = useState<any>(null);
 const [pred, setPred] = useState<any>(null);
 const [tech, setTech] = useState<any>(null);
 const [contract, setContract] = useState<any>(null);
 const [loading, setLoading] = useState(true);
 const [updatedAt, setUpdatedAt] = useState<string>("-");
useEffect(() => {
 let alive = true;
 async function poll() {
 try {
 const [t,dg,mk,it,pd,tc,ct] = await Promise.all([
 fetch(`/ticks/${symbol}?limit=40&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/digits/${symbol}?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/market-master/${symbol}?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/intelligence/${symbol}?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/digits/${symbol}/predictor?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/technical/${symbol}?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 fetch(`/digits/${symbol}/contract?window=100&_cb=${Date.now()}`).then((x) => x.json()).catch(() => null),
 ]);
 if (alive) {
 if (t?.ticks?.length) setTicks(t);
 if (dg?.frequency) setDigs(dg);
 if (mk) setMm(mk);
 if (it) setIntel(it);
 if (pd?.candidate != null) setPred(pd);
 if (tc) setTech(tc);
 if (ct) setContract(ct);
 setUpdatedAt(new Date().toLocaleTimeString([], { hour12:false }));
 }
 } catch {}
 finally { if (alive) setLoading(false); }
 }
 poll();
 const iv = setInterval(poll, 15000);
 return () => { alive =false; clearInterval(iv); };
}, [symbol]);
const tickList: any[] = ticks?.ticks ?? [];
const quotes: number[] = [];
tickList.forEach((t: any) => {
  const q = Number(t?.quote);
  if (Number.isFinite(q)) quotes.push(q);
});
const digs2: number[] = [];
quotes.forEach((q: number) => {
  digs2.push(Number(String(q).slice(-1)));
});
const lastQuote: any = quotes.length ? quotes[quotes.length-1] : null;
const heatRows: Array<{ digit: number; count: number; percent: number }> = Array.from({ length:10 }, (_: unknown,d: number) => {
  const f = digs?.frequency?.[String(d)];
  return { digit:d, count:f?.count ?? 0, percent:f?.percent ??  0 };
});
const totalN = heatRows.reduce((a: number,r: { count: number }) => a + r.count,
   0) ||  1;
const pctOver = (b: number) => Math.round((100 * heatRows.slice(b+1).reduce((a: number,r: { count: number }) => a + r.count,
   0)) / totalN);
const pctUnder = (b: number) => Math.round((100 * heatRows.slice(0,b).reduce((a: number,r: { count: number }) => a + r.count,
   0)) / totalN);
const oddN = heatRows.filter((r: { digit: number }) => r.digit%2===1).reduce((a: number,r: { count: number }) => a + r.count,
   0);
const evenN = totalN - oddN;
const pctEven = Math.round(100 * evenN / totalN);
const pctOdd = Math.round(100 * oddN / totalN);
let riseN=0, fallN=0;
for (let i = 1; i < quotes.length; i++) {
 if (quotes[i] > quotes[i-1]) riseN++;
 else if (quotes[i] < quotes[i-1]) fallN++;
}
const risePct = quotes.length > 1 ? Math.round(100 * riseN / (quotes.length-1)) : 0;
const fallPct = quotes.length > 1 ? Math.round(100 * fallN / (quotes.length-1)) :  0;
const cand: any = contract?.candidate ?? digs?.most_frequent;
const matchesObs: number = Number(contract?.modes?.MATCHES?.observed ?? digs?.frequency?.[String(cand)]?.percent ?? 0);
const differsObs: number = Number(contract?.modes?.DIFFERS?.observed ?? Math.round(100 - matchesObs));
const sigRows: any[] = ((mm?.contracts ?? []) as any[]).slice(0,   3);
const candles: any[] = [];
for (let i =  0; i < quotes.length; i +=  2) {
 const g = quotes.slice(i,i+2);
 if (!g.length) continue;
 candles.push({ open:g[0], close:g[g.length-1], high:Math.max(...g), low:Math.min(...g) });
}
const cc = candles.slice(-8);
const minV = cc.length ? Math.min(...cc.map((c: any) => c.low)) : 0;
const maxV = cc.length ? Math.max(...cc.map((c: any) => c.high)) :  1;
const spanV = (maxV - minV) ||  1;
const yPos = (v: number) => 130 -   18 * ((v - minV) / spanV);
let smaLine: Array<[number, number]> = [];
let emaLine: Array<[number, number]> = [];
try {
 const sma = smaVals(quotes,   10);
 const ema = emaVals(quotes,   9);
 const plot = quotes.slice(-14);
 if (plot.length > 0) {
  const n = plot.length;
  smaLine = sma.slice(-n ).map((v: number,i: number): [number, number] => [28*i, yPos(v)]);
  emaLine = ema.slice(-n ).map((v: number,i: number): [number, number] => [28*i, yPos(v)]);
 }
} catch {}
const liveOk = !!(ticks?.is_live && quotes.length > 0);
const cycleMarket = () => {
 const idx = MARKET_BTNS.findIndex((m: [string,string]) => m[1] === symbol);
 setSymbol(MARKET_BTNS[(idx+1) % MARKET_BTNS.length][1]);
};
return (
 <main style={{ minHeight:"100vh", paddingBottom:72 }}>
 <header style={{ position:"sticky", top:0, zIndex:50, background:"rgba(10,14,26,0.88)", backdropFilter:"blur(10px)", borderBottom:"1px solid rgba(35,43,77,0.5)" }}>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:".75rem 1rem", display:"flex", alignItems:"center", justifyContent:"space-between", gap:10, flexWrap:"wrap" }}>
 <div style={{ display:"flex", alignItems:"center", gap:10 }}>
 <div style={{ width:26, height:26, borderRadius:7, background:"linear-gradient(135deg,var(--primary),var(--accent)))" }} />
 <div>
 <div style={{ fontWeight:800, lineHeight:1.1 }}>Pro Trader</div>
 <div style={{ fontSize:".56rem", color:"var(--muted-2)", letterSpacing:".16em" }}>LIVE ANALYSIS COCKPIT</div>
 </div>
 </div>
 <div style={{ display:"flex", gap:8, alignItems:"center", flexWrap:"wrap" }}>
 <span className={liveOk ? "chip chip-live" : "chip"} style={liveOk ? undefined : { background:"rgba(255,93,122,0.12)", borderColor:"rgba(255,93,122,0.4)", color:"var(--danger)" }}>
 {liveOk ? "LIVE FEED" : quotes.length ? "STALE / RECONNECTING" : "CONNECTING..."}
 </span>
 <span className="chip chip-violet">Digit prediction engine</span>
 <button className="btn btn-ghost" onClick={() => setHelpOpen(true)} style={{ padding:".38rem .8rem", fontSize:".76rem" }}>Help</button>
 <Link className="btn" href="/" style={{ padding:".38rem .8rem", fontSize:".76rem" }}>Exit</Link>
 </div>
 </div>
 </header>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:"18px 1rem 0", display:"grid", gap:16, gridTemplateColumns:"repeat(auto-fit,minmax(280px,1fr)))" }}>
 <div className="card-glow" style={{ padding:".9rem 1rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center" }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>VOLATILITY INDEX</h2>
 <button className="chip chip-violet" onClick={cycleMarket}>{symbol}</button>
 </div>
 <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginTop:10 }}>
 {MARKET_BTNS.map((btns:[string,string]) => (
 <button key={btns[1]} onClick={() => setSymbol(btns[1])} style={{ fontSize:".72rem", padding:".34rem .6rem", borderRadius:"9999px", border:"1px solid var(--border)", background:symbol === btns[1] ? "rgba(185,102,255,0.2)" : "rgba(35,43,77,0.4)", color:"var(--fg)", fontWeight:600, marginTop:6 }}>
 {btns[0]}
 </button>
 ))}
 </div>
 </div>
 <div className="card-glow" style={{ padding:".9rem 1rem" }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>TRADE TYPE</h2>
 <div style={{ display:"flex", flexWrap:"wrap", gap:6, marginTop:10 }}>
 {TRADE_TYPES.map((t:string) => (
 <button key={t} onClick={() => setTradeType(t)} style={{ fontSize:".72rem", padding:".34rem .6rem", borderRadius:"9999px", border:"1px solid var(--border)", background:tradeType === t ? "rgba(46,197,234,0.18)" : "rgba(35,43,77,0.4)", color:"var(--fg)", fontWeight:600 }}>
 {t}
 </button>
 ))}
 </div>
 <div style={{ marginTop:10, fontSize:".72rem", color:"var(--muted)" }}>
 {tradeType} — confidence-locked signal pipeline active. Next suggestion reviews every 15 seconds.
 {pred?.candidate != null && (
 <span style={{ color:"var(--primary)", fontWeight:600 }}>{" · Prediction "}{pred.candidate}{" ("}{pred.confidence}{"%)"}</span>
 )}
 <span style={{ color:"var(--muted-2)" }}>{" · Last analysis "}{updatedAt}</span>
 </div>
 </div>
 </div>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:"16px 1rem 0", display:"grid", gap:16, gridTemplateColumns:"minmax(0,1.4fr) minmax(280px,0.6fr)" }}>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10 }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>LIVE TICK STREAM</h2>
 <span className="chip chip-live">streaming</span>
 </div>
 <div style={{ display:"flex", gap:4, overflowX:"auto", paddingBottom:8, fontFamily:"ui-monospace,monospace" }}>
 {digs2.slice(-10).map((d:number,i:number) => (
 <div key={i} style={{ minWidth:34, height:40, display:"flex", alignItems:"center", justifyContent:"center", borderRadius:8, background:"rgba(185,102,255,0.12)", border:"1px solid rgba(185,102,255,0.35)", fontWeight:700, fontSize:"1.05rem", color:"var(--primary)" }}>
 {d}
 </div>
 ))}
 {!digs2.length && <div style={{ color:"var(--muted-2)", fontSize:".8rem", padding:"10px 0" }}>No ticks yet — waiting for the live feed…</div>}
 </div>
 <div style={{ display:"flex", justifyContent:"space-between", marginTop:10, fontSize:".7rem", color:"var(--muted-2)" }}>
 <span>Tape source: Deriv live feed · {symbol}</span>
 <span>window: last 10 digits</span>
 </div>
 </section>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em", marginBottom:10 }}>CANDLESTICK CHART</h2>
 <div style={{ display:"flex", gap:8, marginTop:8, fontSize:".68rem", color:"var(--muted-2)", flexWrap:"wrap" }}>
{cc.length ? (
  <svg viewBox="0 0 280 150" style={{ width:"100%", background:"rgba(10,14,26,0.5)", borderRadius:8, border:"1px solid var(--border)" }}>
   {cc.map((c:any,i:number) => (
     <rect key={i} x={3+28*i} y={yPos(c.high)} width={16} height={10} rx={2} fill={c.close >= c.open ? "rgba(40,209,124,0.65)" : "rgba(255,93,122,0.65)"} />
   ))}
  </svg>
) : (
  <div style={{ color:"var(--muted-2)", fontSize:".8rem", padding:"30px 0", textAlign:"center" }}>Waiting for live quotes…</div>
)} <span className="chip">SMA 10 {fmt(tech?.sma_10,2)}</span><span className="chip">EMA 9 {fmt(tech?.ema_10,2)}</span><span className="chip">BB {tech?.bollinger ? "active" : "-"}</span><span className="chip">RSI {tech?.rsi != null ? tech.rsi : "-"}</span>
 </div>
 </section>
 </div>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:"16px 1rem 0" }}>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10 }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>DIGIT FREQUENCY HEATMAP</h2>
 <span className="chip chip-violet">Strongest: {digs?.most_frequent ?? "-"} · Weakest: {digs?.least_frequent ?? "-"}</span>
 </div>
 <div style={{ display:"grid", gap:6, fontSize:".78rem" }}>
 {heatRows.slice().sort((a,b) => b.count-a.count).map((r:any,i:number) => (
 <div key={r.digit} style={{ display:"grid", gridTemplateColumns:"40px 1fr 46px", gap:8, alignItems:"center" }}>
 <span style={{ textAlign:"right", fontFamily:"ui-monospace,monospace", fontWeight:700 }}>{i+1}</span>
 <div style={{ background:"rgba(35,43,77,0.5)", borderRadius:"9999px", height:14, overflow:"hidden" }}>
 <div style={{ width:`${Math.max(3,r.percent)}%`, height:"100%", background:"linear-gradient(90deg,var(--accent),var(--primary))", borderRadius:"9999px", opacity:0.85 }} />
 </div>
 <span style={{ fontFamily:"ui-monospace,monospace", color:"var(--muted)" }}>{r.count}×</span>
 </div>
 ))}
 {!heatRows.some((r:any) => r.count >  0) && <div style={{ color:"var(--muted-2)", fontSize:".8rem" }}>No frequency data yet.</div>}
 </div>
 </section>
 </div>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:"16px 1rem 0", display:"grid", gap:16, gridTemplateColumns:"repeat(auto-fit,minmax(300px,1fr)))" }}>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em", marginBottom:10 }}>OVER / UNDER</h2>
 <div style={{ display:"grid", gap:8, fontSize:".76rem" }}>
 {([
 ["Over 5",pctOver(5),"var(--success)"],
 ["Under 5",pctUnder(5),"var(--accent)"],
 ["Over 7",pctOver(7),"var(--success)"],
 ["Under 3",pctUnder(3),"var(--accent)"],
 ["Even",pctEven,"var(--success)"],
 ["Odd",pctOdd,"var(--accent)"],
 ] as any[]).map((row:any[]) => (
 <div key={row[0]} style={{ display:"grid", gridTemplateColumns:"74px 1fr 40px", gap:8, alignItems:"center" }}>
 <span>{row[0]}</span>
 <div style={{ background:"rgba(35,43,77,0.5)", borderRadius:"9999px", height:10, overflow:"hidden" }}>
 <div style={{ width:`${Math.min(100,Math.max(2,row[1]))}%`, height:"100%", background:row[2], borderRadius:"9999px" }} />
 </div>
 <span style={{ fontFamily:"ui-monospace,monospace", color:"var(--muted)" }}>{row[1]}%</span>
 </div>
 ))}
 </div>
 </section>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em", marginBottom:10 }}>RISE / FALL · MATCHES / DIFFERS</h2>
 <div style={{ display:"grid", gap:8, fontSize:".76rem" }}>
 {([
 [`Matches ${cand}`,matchesObs,"var(--success)"],
 [`Differs ${cand}`,differsObs,"var(--warning)"],
 [`Rise from ${fmt(lastQuote,2)}`,risePct,"var(--success)"],
 [`Fall from ${fmt(lastQuote,2)}`,fallPct,"var(--accent)"],
 ] as any[]).map((row:any[]) => (
 <div key={row[0]} style={{ display:"flex", gap:8, alignItems:"center", justifyContent:"space-between" }}>
 <span>{row[0]}</span>
 <span className="chip" style={{ background:"rgba(40,209,124,0.12)", borderColor:"rgba(40,209,124,0.35)", color:row[2], fontWeight:700 }}>{row[1]}%</span>
 </div>
 ))}
 </div>
 <div style={{ marginTop:12, fontSize:".72rem", color:"var(--muted)" }}>
 Confidence gate: Wilson lower bound must stay above the 1.1 payout breakeven (90.9%). Only significant edges pass the CF pipeline. Analytics source: {intel?.decision ?? "-"} · conviction {intel?.conviction ?? "-"}.
 </div>
 </section>
 </div>
 <div style={{ maxWidth:1240, margin:"0 auto", padding:"16px 1rem 0", display:"grid", gap:16, gridTemplateColumns:"repeat(auto-fit,minmax(300px,1fr)))" }}>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10 }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>TRADING SIGNALS</h2>
 <span className="chip chip-violet">Matrix deep scan</span>
 </div>
 <div style={{ display:"grid", gap:8, fontSize:".8rem" }}>
 {sigRows.map((c:any,i:number) => (
 <div key={c.name ?? i} style={{ display:"flex", justifyContent:"space-between", alignItems:"center", background:c.verdict === "PLAY" ? "rgba(40,209,124,0.08)" : "rgba(35,43,77,0.4)", border:c.verdict === "PLAY" ? "1px solid rgba(40,209,124,0.25)" : "1px solid transparent", borderRadius:10, padding:".6rem .8rem" }}>
 <span>{c.name ?? c.type}{c.digit != null ? ` ${c.digit}` : ""}{c.verdict ? ` · ${c.verdict}` : ""}</span>
 <span className="chip" style={{ background:c.verdict === "PLAY" ? "rgba(40,209,124,0.16)" : "rgba(242,197,24,0.14)", color:c.verdict === "PLAY" ? "var(--success)" : "var(--warning)", fontWeight:700 }}>CONFIDENCE {c.confidence ?? 0}%</span>
 </div>
 ))}
 {!sigRows.length && <div style={{ color:"var(--muted-2)", fontSize:".72rem" }}>No contracts yet — wait for the analysis window.</div>}
 </div>
 <div style={{ marginTop:10, fontSize:".7rem", color:"var(--muted)" }}>
 Signals rate each read against breakeven odds. Below 90.9% the house edge wins — those plays are skipped,not forced.{mm?.recommendation ? ` · ${mm.recommendation}` : ''}
 </div>
 </section>
 <section className="card-glow" style={{ padding:"1rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:10 }}>
 <h2 style={{ fontSize:".78rem", fontWeight:700, color:"var(--muted)", letterSpacing:".08em" }}>SIGNAL HISTORY</h2>
 <span className="chip">{symbol}</span>
 </div>
 <div style={{ display:"grid", gap:4, fontSize:".7rem", fontFamily:"ui-monospace,monospace" }}>
 <div style={{ display:"grid", gridTemplateColumns:"1fr 110px 70px", gap:8, color:"var(--muted-2)", padding:".3rem .4rem" }}>
 <span>TIME</span><span>MARKET</span><span>DIGIT</span>
 </div>
 {tickList.slice(-6).reverse().map((t:any,i:number) => {
 const ts = new Date(t?.timestamp);
 const time = Number.isNaN(ts.getTime()) ? "-" : ts.toLocaleTimeString([],{ hour12:false });
 return (
 <div key={i} style={{ display:"grid", gridTemplateColumns:"1fr 110px 70px", gap:8, background:i%2===0 ? "rgba(35,43,77,0.35)" : "transparent", borderRadius:8, padding:".3rem .4rem" }}>
 <span>{time}</span><span>{t?.symbol ?? symbol}</span><span className="chip chip-live">{t?.digit ?? "-"}</span>
 </div>
 );
 })}
 {!tickList.length && <div style={{ color:"var(--muted-2)" }}>No live ticks yet.</div>}
 </div>
 <div style={{ marginTop:8, fontSize:".68rem", color:"var(--muted-2)", textAlign:"right" }}>
 {tickList.length} live ticks · tape history,most recent first
 </div>
 </section>
 </div>
 <footer style={{ maxWidth:1240, margin:"24px auto 0", padding:"0 1rem", borderTop:"1px solid rgba(35,43,77,0.5)" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"1rem 0", fontSize:".74rem", color:"var(--muted-2)", flexWrap:"wrap", gap:8 }}>
 <span>Pro Trader Analysis Tool · Cockpit v1</span>
 <span style={{ display:"flex", gap:12 }}><a href="/">Home</a><a href="/auth">Launch</a><span>Support</span></span>
 </div>
 </footer>
 {helpOpen && (
 <div onClick={() => setHelpOpen(false)} style={{ position:"fixed", inset:0, background:"rgba(4,6,12,0.78)", backdropFilter:"blur(4px)", display:"flex", alignItems:"center", justifyContent:"center", padding:"1rem", zIndex:100 }}>
 <div onClick={(e:any) => e.stopPropagation()} className="card-glow" style={{ maxWidth:640, width:"100%", maxHeight:"84vh", overflowY:"auto", padding:"1.2rem 1.2rem" }}>
 <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", marginBottom:12 }}>
 <h2 style={{ fontSize:"1.05rem", fontWeight:700 }}>Pro Trader — How to use the cockpit</h2>
 <button className="btn btn-ghost" onClick={() => setHelpOpen(false)} style={{ padding:".3rem .6rem", fontSize:".72rem" }}>Close</button>
 </div>
 <div style={{ display:"grid", gap:10, fontSize:".78rem", color:"var(--muted)", lineHeight:1.5 }}>
 <p><b style={{ color:"var(--fg)" }}>Volatility Index.</b> Pick a synthetic index. Each has its own tick cadence and volatility profile.</p>
 <p><b style={{ color:"var(--fg)" }}>Trade Type.</b> Choose which market you want to read: Matches / Differs,Over / Under,Even / Odd,Rise / Fall or the Digit prediction engine.</p>
 <p><b style={{ color:"var(--fg)" }}>Live Tick Stream.</b> The last 10 digits stream in real time from Deriv. Watch dominance and hot/cold streaks before you commit.</p>
 <p><b style={{ color:"var(--fg)" }}>Candlestick Chart.</b> Live candles built from the last quotes with SMA 10,EMA 9 and RSI overlay. Indicators come from the same live tape.</p>
 <p><b style={{ color:"var(--fg)" }}>Digit Frequency Heatmap.</b> Frequency per digit over the window. Strongest digit has appeared most often; Weakest digit least.</p>
 <p><b style={{ color:"var(--fg)" }}>Over / Under· Even / Odd.</b> Probability bars at any barrier 0–9. The engine estimates the chance the next digit lands over,under,even or odd.</p>
 <p><b style={{ color:"var(--fg)" }}>Rise / Fall.</b> Ticks vs the entry quote. Rise = next quote higher; Fall = lower. Used with RSI for momentum confirmation.</p>
 <p><b style={{ color:"var(--fg)" }}>Matches / Differs.</b> Strongest digit detection — picks the digit most likely to repeat (match) or toggle (differ).</p>
 <p><b style={{ color:"var(--fg)" }}>Digit prediction engine.</b> The full stack scorecard: frequency + volatility + RSI + sequence memory combine into a pick with confidenceanda rationale.</p>
 <p><b style={{ color:"var(--fg)" }}>Trading Signals · Signal History.</b> Every scored read lands here elsestable. History keeps a live tick tape with timestamps so you can audit every call.</p>
 <p style={{ fontSize:".72rem", color:"var(--muted-2)", marginTop:4 }}>Open access build — no paywall,no WhatsApp gate. The same engine,the same cockpit,for free.</p>
 </div>
 </div>
 </div>
 )}
 </main>
 );
}
