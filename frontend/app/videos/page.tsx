"use client";
import { useState } from "react";
import { VIDEOS, type Vid } from "@/lib/videos";

const CATEGORIES = ["All", "Beginner", "Intermediate", "Advanced"];

export default function Videos() {
  const [cat, setCat] = useState("All");
  const [q, setQ] = useState("");
  const [active, setActive] = useState<Vid | null>(null);

  const filtered = VIDEOS.filter(
    (v) =>
      (cat === "All" || v.cat === cat) &&
      v.title.toLowerCase().includes(q.toLowerCase())
  );

  return (
    <main style={{ padding: "2rem", maxWidth: 1200, margin: "0 auto" }}>
      <a href="/dashboard" style={{ fontSize: "0.8rem" }}>← Back to Dashboard</a>
      <h1 style={{ fontSize: "1.5rem", margin: "0.5rem 0" }}>🎬 Video Hub</h1>
      <p style={{ color: "#8b949e", fontSize: "0.9rem", marginBottom: "1rem" }}>
        {VIDEOS.length} narrated tutorials — Beginner to Advanced. Tap a card to play.
      </p>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: "1rem" }}>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search videos…"
          style={{ flex: 1, minWidth: 200, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "8px 12px" }}
        />
        {CATEGORIES.map((c) => (
          <button
            key={c}
            onClick={() => setCat(c)}
            style={{ background: cat === c ? "#58a6ff" : "#161b22", color: cat === c ? "#0d1117" : "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "8px 12px", cursor: "pointer", fontWeight: 700 }}
          >
            {c}
          </button>
        ))}
      </div>

      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
        {filtered.map((v) => (
          <button key={v.n} onClick={() => setActive(v)} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, overflow: "hidden", cursor: "pointer", textAlign: "left", padding: 0 }}>
            <div style={{ position: "relative", height: 140 }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={v.thumb} alt={v.title} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              <span style={{ position: "absolute", bottom: 6, right: 8, background: "#010409cc", padding: "2px 6px", borderRadius: 4, fontSize: "0.7rem", color: "#c9d1d9" }}>{v.dur}</span>
            </div>
            <div style={{ padding: 10 }}>
              <div style={{ fontWeight: 700, fontSize: "0.85rem", lineHeight: 1.3, color: "#c9d1d9" }}>{v.n}. {v.title}</div>
              <div style={{ color: "#8b949e", fontSize: "0.7rem", marginTop: 4 }}>{v.cat}</div>
            </div>
          </button>
        ))}
      </div>

      {active && (
        <div onClick={() => setActive(null)} style={{ position: "fixed", inset: 0, background: "#010409dd", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 16 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, width: "min(960px, 100%)", padding: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <strong style={{ fontSize: "0.9rem", color: "#c9d1d9" }}>{active.n}. {active.title}</strong>
              <button onClick={() => setActive(null)} style={{ background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "4px 10px", cursor: "pointer" }}>✕ Close</button>
            </div>
            <video src={active.src} poster={active.thumb} controls autoPlay playsInline style={{ width: "100%", borderRadius: 6, background: "#000" }} />
          </div>
        </div>
      )}
    </main>
  );
}
