"use client";
import { useState } from "react";

const VIDEOS = [
  { n: 1, title: "How to Open EAGLE-X and Log In", cat: "Beginner", dur: "2:00", views: "1.2k" },
  { n: 2, title: "Understanding the Dashboard Layout", cat: "Beginner", dur: "2:30", views: "980" },
  { n: 3, title: "How to Read the Header", cat: "Beginner", dur: "2:00", views: "850" },
  { n: 4, title: "How to Check the Intelligence Engine", cat: "Beginner", dur: "2:30", views: "790" },
  { n: 5, title: "How to Scan All Markets", cat: "Beginner", dur: "2:00", views: "710" },
  { n: 6, title: "How to Place a MATCHES Trade", cat: "Beginner", dur: "3:00", views: "1.4k" },
  { n: 7, title: "How to Place a DIFFERS Trade", cat: "Beginner", dur: "3:00", views: "1.1k" },
  { n: 8, title: "How to Place an ODD Trade", cat: "Beginner", dur: "2:30", views: "980" },
  { n: 9, title: "How to Place an EVEN Trade", cat: "Beginner", dur: "2:30", views: "940" },
  { n: 10, title: "How to Place OVER and UNDER Trades", cat: "Beginner", dur: "3:30", views: "1.6k" },
  { n: 11, title: "How to Use the Tick Timer", cat: "Intermediate", dur: "3:00", views: "1.3k" },
  { n: 12, title: "How to Read Market Master", cat: "Intermediate", dur: "4:00", views: "1.7k" },
  { n: 13, title: "Digit Hacker – Frequency Tab", cat: "Intermediate", dur: "3:30", views: "1.2k" },
  { n: 14, title: "Digit Hacker – Psychology Tab", cat: "Intermediate", dur: "3:00", views: "1.1k" },
  { n: 15, title: "Digit Hacker – Contract Tab", cat: "Intermediate", dur: "4:00", views: "1.4k" },
  { n: 16, title: "Digit Hacker – Predictor Tab", cat: "Intermediate", dur: "3:00", views: "1.0k" },
  { n: 17, title: "Digit Hacker – Gaps Tab", cat: "Intermediate", dur: "3:00", views: "890" },
  { n: 18, title: "How to Use Paper Mode", cat: "Intermediate", dur: "3:00", views: "1.2k" },
  { n: 19, title: "How to Start and Stop Auto Trader", cat: "Intermediate", dur: "3:00", views: "1.4k" },
  { n: 20, title: "How to Read the Activity Log", cat: "Intermediate", dur: "3:00", views: "880" },
  { n: 21, title: "Strategy Builder – Load a Template", cat: "Advanced", dur: "3:00", views: "760" },
  { n: 22, title: "Strategy Builder – Build a Custom Strategy", cat: "Advanced", dur: "8:00", views: "1.8k" },
  { n: 23, title: "Strategy Builder – Save, Export, and Import", cat: "Advanced", dur: "4:00", views: "900" },
  { n: 24, title: "How to Run a Backtest", cat: "Advanced", dur: "5:00", views: "1.5k" },
  { n: 25, title: "Backtest Optimization and Walk-Forward", cat: "Advanced", dur: "5:00", views: "1.1k" },
  { n: 26, title: "How to Deploy a Strategy to Auto Trader", cat: "Advanced", dur: "4:00", views: "1.3k" },
  { n: 27, title: "Copy Trading – Follow a Leader", cat: "Advanced", dur: "4:00", views: "1.2k" },
  { n: 28, title: "Copy Trading – Register as a Leader", cat: "Advanced", dur: "4:00", views: "1.0k" },
  { n: 29, title: "Portfolio Manager – Track Assets", cat: "Advanced", dur: "5:00", views: "980" },
  { n: 30, title: "Full Day Trading Session (Live)", cat: "Advanced", dur: "20:00", views: "3.2k" },
];

const CATEGORIES = ["All", "Beginner", "Intermediate", "Advanced"];

export default function Videos() {
  const [cat, setCat] = useState("All");
  const [q, setQ] = useState("");

  const filtered = VIDEOS.filter(v =>
    (cat === "All" || v.cat === cat) && v.title.toLowerCase().includes(q.toLowerCase())
  );

  return (
    <main style={{ padding: "2rem", maxWidth: 1200, margin: "0 auto" }}>
      <a href="/dashboard" style={{ fontSize: "0.8rem" }}>← Back to Dashboard</a>
      <h1 style={{ fontSize: "1.5rem", margin: "0.5rem 0" }}>🎬 Video Hub</h1>
      <p style={{ color: "#8b949e", fontSize: "0.9rem", marginBottom: "1rem" }}>
        30 realistic tutorial videos from Beginner to Advanced.
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: "1rem" }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search videos…"
          style={{ flex: 1, minWidth: 200, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "8px 12px" }}
        />
        {CATEGORIES.map(c => (
          <button key={c} onClick={() => setCat(c)}
            style={{ background: cat === c ? "#58a6ff" : "#161b22", color: cat === c ? "#0d1117" : "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "8px 12px", cursor: "pointer", fontWeight: 700 }}>
            {c}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
        {filtered.map(v => (
          <div key={v.n} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, overflow: "hidden" }}>
            <div style={{ position: "relative", height: 140, background: "linear-gradient(140deg, #0d1117 0%, #1a2230 100%)", display: "flex", alignItems: "center", justifyContent: "center", color: "#58a6ff", fontSize: 28 }}>
              ▶
              <span style={{ position: "absolute", bottom: 6, right: 8, background: "#010409cc", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem" }}>{v.dur}</span>
            </div>
            <div style={{ padding: 10 }}>
              <div style={{ fontWeight: 700, fontSize: "0.85rem", lineHeight: 1.3 }}>{v.title}</div>
              <div style={{ color: "#8b949e", fontSize: "0.7rem", marginTop: 4 }}>{v.cat} · {v.views} views</div>
            </div>
          </div>
        ))}
      </div>
    </main>
  );
}
