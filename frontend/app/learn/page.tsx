const COURSES = [
  {
    category: "Trading Basics",
    courses: [
      { title: "Introduction to Synthetic Indices", length: "2h", level: "Beginner" },
      { title: "Understanding Digit Contracts", length: "3h", level: "Beginner" },
      { title: "Reading Market Data", length: "2h", level: "Beginner" },
    ],
  },
  {
    category: "EAGLE-X Platform",
    courses: [
      { title: "Using Market Master", length: "1.5h", level: "Beginner" },
      { title: "Auto Trader Setup", length: "2h", level: "Intermediate" },
      { title: "Strategy Builder Deep-Dive", length: "4h", level: "Advanced" },
    ],
  },
  {
    category: "Advanced Strategies",
    courses: [
      { title: "Digit Frequency Trading", length: "3h", level: "Advanced" },
      { title: "Backtesting & Optimisation", length: "3h", level: "Advanced" },
      { title: "Portfolio Diversification", length: "2h", level: "Advanced" },
    ],
  },
  {
    category: "AI & Trading",
    courses: [
      { title: "ML for Market Regimes", length: "5h", level: "Advanced" },
      { title: "Reinforcement Trading Agents", length: "6h", level: "Expert" },
    ],
  },
  {
    category: "Risk Management",
    courses: [
      { title: "Stop-Loss Discipline", length: "2h", level: "Beginner" },
      { title: "Position Sizing & Stake Rules", length: "2h", level: "Intermediate" },
      { title: "Drawdown Management", length: "3h", level: "Advanced" },
    ],
  },
];

const GLOSSARY = [
  { term: "OVERFED", def: "A digit appearing more than fair (≥10% deviation above 10%)." },
  { term: "CONFIRMATION", def: "Second most frequent digit, backup candidate." },
  { term: "STARVING", def: "A digit appearing less than expected (<10%)." },
  { term: "DATA QUALITY", def: "0-100 score for completeness, timeliness, consistency, validity." },
  { term: "STOP-LOSS", def: "Hard limit on losses that halts trading." },
  { term: "PROFIT FACTOR", def: "Gross profit divided by gross loss." },
  { term: "SHARPE RATIO", def: "Risk-adjusted return metric." },
  { term: "VaR", def: "Value at Risk — max loss at a given confidence interval." },
];

export default function Learn() {
  return (
    <main style={{ padding: "2rem", maxWidth: 1000, margin: "0 auto" }}>
      <a href="/dashboard" style={{ fontSize: "0.8rem" }}>← Back to Dashboard</a>
      <h1 style={{ fontSize: "1.5rem", margin: "0.5rem 0" }}>🎓 Education Hub</h1>
      <p style={{ color: "#8b949e", fontSize: "0.9rem" }}>
        Courses, tutorials, certifications, and a 100+ term glossary.
      </p>

      {COURSES.map((cat) => (
        <section key={cat.category} style={{ marginTop: "2rem" }}>
          <h2 style={{ fontSize: "1rem", color: "#58a6ff", marginBottom: "0.5rem" }}>{cat.category}</h2>
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
            {cat.courses.map((c) => (
              <div key={c.title} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 12 }}>
                <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>{c.title}</div>
                <div style={{ color: "#8b949e", fontSize: "0.75rem", marginTop: 4 }}>
                  {c.level} · {c.length}
                </div>
                <div style={{ marginTop: 8, height: 4, background: "#30363d", borderRadius: 2 }}>
                  <div style={{ height: "100%", width: "0%", background: "#58a6ff" }} />
                </div>
                <div style={{ color: "#8b949e", fontSize: "0.7rem", marginTop: 4 }}>0% complete</div>
              </div>
            ))}
          </div>
        </section>
      ))}

      <section style={{ marginTop: "2.5rem" }}>
        <h2 style={{ fontSize: "1rem", color: "#58a6ff", marginBottom: "0.5rem" }}>Glossary</h2>
        <div style={{ display: "grid", gap: 8 }}>
          {GLOSSARY.map((g) => (
            <div key={g.term} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 12 }}>
              <span style={{ fontWeight: 700, color: "#58a6ff" }}>{g.term}</span> — {g.def}
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
