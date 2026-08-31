"use client";
import Link from "next/link";

const MARKET_CARDS = [
  ["R_100", "Volatility 100 (1s)", "8,221.4", "-0.62%"],
  ["R_75", "Volatility 75", "987.12", "-0.83%"],
  ["BOOM1000", "BOOM 1000", "10,442.8", "+1.05%"],
  ["CRASH500", "CRASH 500", "8,221.4", "-0.62%"],
  ["R_50", "Volatility 50 (1s)", "642.39", "+0.94%"],
  ["STEPINDEX", "STEP", "4,510.2", "+0.18%"],
  ["R_25", "Volatility 25", "318.77", "-1.12%"],
  ["BOOM500", "BOOM 500", "6,019.0", "+0.74%"],
];

const FEATURES = [
  ["Auto-signal engine", "Every tick is scored by digit dominance, volatility, RSI and rise/fall momentum. Matches/differs signals are confidence-locked at 98% and recorded with timestamps so you can audit every call."],
  ["Over / Under", "Probability bars at any barrier 0–9."],
  ["Digit heatmap", "Frequency, hot/cold, dominance."],
  ["Live candles", "SMA · EMA · BB · RSI overlays."],
  ["Matches / Differs", "Strongest digit detection."],
  ["Tick stream", "Volatility · Boom · Crash · Step."],
];

export default function HomePage() {
  return (
 <main style={{ minHeight: "100vh" }}>
 <header style={{ position: "sticky", top: 0, zIndex: 50, background: "rgba(10,14,26,0.85)", borderBottom: "1px solid rgba(35,43,77,0.5)" }}>
 <div style={{ maxWidth: 1100, margin: "0 auto", padding: ".8rem 1rem", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
 <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
 <div style={{ width: 26, height: 26, borderRadius: 7, background: "linear-gradient(135deg, var(--primary), var(--accent)))" }} />
 <div>
 <div style={{ fontWeight: 800, letterSpacing: "-.01em" }}>Pro Trader</div>
 <div style={{ fontSize: ".58rem", color: "var(--muted-2)", letterSpacing: ".18em", textTransform: "uppercase" }}>Analysis Suite</div>
 </div>
 </div>
 <div style={{ display: "flex", gap: 14, fontSize: ".82rem", color: "var(--muted)" }}>
 <a href="#features">Features</a>
 <a href="#markets">Markets</a>
 <a href="#courses">Courses</a>
 <a href="#terms">Terms</a>
 </div>
 <div style={{ display: "flex", gap: 8 }}>
 <Link className="btn btn-ghost" href="/auth" style={{ padding: ".4rem .8rem", fontSize: ".78rem" }}>Launch app</Link>
 </div>
 </div>
 </header>

 <section style={{ textAlign: "center", padding: "88px 1rem 56px" }}>
 <span className="chip">Live · Synthetic indices analytics engine</span>
 <h1 className="section-title gradient-text" style={{ fontSize: "clamp(2.2rem, 6vw, 4rem)", margin: "14px 0 10px" }}>
 Read the market digit by digit.
 </h1>
 <p className="section-sub" style={{ margin: "0 auto", fontSize: "1rem", lineHeight: 1.6, maxWidth: 720 }}>
 Pro Trader streams every tick from Volatility, Boom and Crash marketsand turns it into actionable signals — heatmaps, candles, over/under odds,and confidence-locked matches/differs predictions, all in one cockpit.
 </p>
 <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap", marginTop: 26 }}>
 <Link className="btn btn-lg" href="/auth">Launch app</Link>
 <a className="btn btn-lg btn-outline" href="https://partner-tracking.deriv.com/click?a=9667&o=1&c=3&link_id=1" target="_blank" rel="noreferrer">Create Deriv account</a>
 <a className="btn btn-lg btn-ghost" href="https://t.me/dbottraders" target="_blank" rel="noreferrer">Join Telegram</a>
 </div>
 </section>

 <section style={{ padding: "0 1rem 48px" }}>
 <div style={{ maxWidth: 1100, margin: "0 auto", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr)))", gap: 12 }}>
 {MARKET_CARDS.map((m) => (
 <div key={m[0]} className="card-glow" style={{ padding: ".8rem 1rem" }}>
 <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: ".72rem", color: "var(--muted)", fontWeight: 600 }}>
 <span>{m[1]}</span><span className="chip chip-live" style={{ fontSize: ".55rem", padding: "0 .35rem" }}>LIVE</span>
 </div>
 <div style={{ fontSize: "1.1rem", fontWeight: 700, margin: "4px 0 2px", fontFamily: "ui-monospace, monospace" }}>{m[2]}</div>
 <div style={{ fontSize: ".78rem", fontWeight: 600, color: m[3].startsWith("+") ? "var(--success)" : "var(--danger)" }}>{m[3]}</div>
 </div>
 ))}
 </div>
 </section>

 <section id="features" style={{ padding: "0 1rem 48px" }}>
 <div style={{ maxWidth: 1100, margin: "0 auto" }}>
 <h2 className="section-title" style={{ textAlign: "center" }}>Toolkit</h2>
 <p className="section-sub" style={{ textAlign: "center", margin: "6px 0 28px" }}>Everything you need to call the next tick.</p>
 <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr)))", gap: 14 }}>
 {FEATURES.map((f) => (
 <div className="card-glow" key={f[0]}>
 <h3 style={{ fontSize: "1rem", fontWeight: 700, color: "var(--primary)", marginBottom: 6 }}>{f[0]}</h3>
 <p style={{ fontSize: ".84rem", color: "var(--muted)", lineHeight: 1.5 }}>{f[1]}</p>
 </div>
 ))}
 </div>
 </div>
 </section>

 <section id="terms" style={{ padding: "0 1rem 64px", textAlign: "center" }}>
 <div style={{ maxWidth: 640, margin: "0 auto" }}>
 <h2 className="section-title">Your edge starts now.</h2>
 <p className="section-sub" style={{ margin: "1rem auto 26px" }}>Join Pro Trader and turn raw ticks into confident, data-backed decisions.</p>
 <Link className="btn btn-lg" href="/auth">Continue to the tool</Link>
 </div>
 </section>

 <footer style={{ borderTop: "1px solid rgba(35,43,77,0.5)", padding: "1.1rem 1rem", fontSize: ".78rem", color: "var(--muted-2)" }}>
 <div style={{ maxWidth: 1100, margin: "0 auto", display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
 <span>© 2026 Pro Trader Analysis Tool</span>
 <span style={{ display: "flex", gap: 14 }}><a href="#terms">Terms</a><span>Support</span><Link href="/auth">Sign in</Link></span>
 </div>
 </footer>
 </main>
  );
}
