"use client";
import Link from "next/link";

export default function AuthPage() {
  return (
 <main style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "2rem 1rem" }}>
 <div style={{ width: "100%", maxWidth: 440 }}>
 <Link href="/" style={{ fontSize: ".78rem", color: "var(--muted)", marginBottom: 12, display: "inline-block" }}>← Back to home</Link>
 <div className="card-glow" style={{ padding: "1.6rem 1.4rem" }}>
 <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
 <div style={{ width: 30, height: 30, borderRadius: 9, background: "linear-gradient(135deg, var(--primary), var(--accent)))", boxShadow: "0 0 24px rgba(185,102,255,0.55)" }} />
 <div>
 <div style={{ fontWeight: 800, letterSpacing: "-.01em" }}>Pro Trader</div>
 <div style={{ fontSize: ".6rem", color: "var(--muted-2)", letterSpacing: ".18em" }}>Analysis Suite</div>
 </div>
 </div>
 <h1 className="section-title" style={{ fontSize: "1.35rem", margin: "10px 0 6px" }}>Live digit analysis begins here.</h1>
 <p style={{ color: "var(--muted)", fontSize: ".84rem", lineHeight: 1.5, marginBottom: 18 }}>
 One click. No payment screen. No account lock-in. Pick a market and start reading the tape.
 </p>
 <form
 onSubmit={(e) => { e.preventDefault(); window.location.href = "/app"; }}
 style={{ display: "grid", gap: 12 }}
 >
 <label style={{ fontSize: ".72rem", color: "var(--muted)", fontWeight: 600 }}>
 USERNAME
 <input
 className="bg-card"
 style={{ width: "100%", marginTop: 4, padding: ".7rem .9rem", color: "var(--fg)", outline: "none", border: "1px solid var(--border)" }}
 placeholder="trader01"
 defaultValue="trader01"
 aria-label="Username"
 />
 </label>
 <label style={{ fontSize: ".72rem", color: "var(--muted)", fontWeight: 600 }}>
 PASSWORD
 <input
 className="bg-card"
 type="password"
 style={{ width: "100%", marginTop: 4, padding: ".7rem .9rem", color: "var(--fg)", outline: "none", border: "1px solid var(--border)" }}
 defaultValue="• • • • • • • •"
 aria-label="Password"
 />
 </label>
 <div style={{ fontSize: ".72rem", color: "var(--muted)", display: "flex", gap: 6, alignItems: "center" }}>
 <input type="checkbox" id="toc" defaultChecked />
 <label htmlFor="toc">Open access — launch straight into the cockpit </label>
 </div>
 <button type="submit" className="btn btn-lg" style={{ width: "100%", marginTop: 4 }}>Launch Pro Trader</button>
 </form>
 <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
 <Link className="btn btn-outline" href="/app" style={{ width: "100%", fontSize: ".82rem" }}>I already have an account — open cockpit</Link>
 <a className="btn btn-ghost" href="https://partner-tracking.deriv.com/click?a=9667&o=1&c=3&link_id=1" target="_blank" rel="noreferrer" style={{ width: "100%", fontSize: ".82rem" }}>New to Deriv? Create a free account</a>
 <a className="btn btn-ghost" href="https://t.me/dbottraders" target="_blank" rel="noreferrer" style={{ width: "100%", fontSize: ".82rem" }}>Questions? Join Telegram @dbottraders</a>
 </div>
 </div>
 <p style={{ textAlign: "center", fontSize: ".7rem", color: "var(--muted-2)", marginTop: 16 }}>
 Pro · Trader · Cockpit · 2026 — no paywall, no WhatsApp gate, no payment required
 </p>
 </div>
 </main>
  );
}
