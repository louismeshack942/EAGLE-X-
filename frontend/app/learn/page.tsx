"use client";
import { useMemo, useState } from "react";

type QuizQ = { q: string; a: string[]; correct: number };
type Lesson = { title: string; level: string; mins: number; content: { body: string[]; quiz: QuizQ[] } };
type Course = { category: string; lessons: Lesson[] };

const COURSES: Course[] = [
  {
    category: "Trading Basics",
    lessons: [
      {
        title: "Introduction to Synthetic Indices",
        level: "Beginner", mins: 10,
        content: {
          body: [
            "Synthetic indices are markets that simulate real market movement using random number generation instead of real-world events. Deriv's Volatility indices (R_10, R_25, R_50, R_75, R_100) are the most common.",
            "Each tick updates the price. The LAST DIGIT of each price is what digit contracts (MATCHES, DIFFERS, ODD, EVEN, OVER, UNDER) resolve on. EAGLE-X analyses the distribution of those last digits.",
            "Because nothing 'drives' synthetic prices, no news affects them — but short-window digit frequencies still drift. EAGLE-X measures that drift and turns it into evidence."
          ],
          quiz: [
            { q: "What does a digit contract resolve on?", a: ["The last digit of the price", "The closing price direction", "The volume"], correct: 0 },
            { q: "Do real-world news events move synthetic indices?", a: ["Yes", "No"], correct: 1 }
          ]
        }
      },
      {
        title: "Understanding Digit Contracts",
        level: "Beginner", mins: 12,
        content: {
          body: [
            "MATCHES wins when the final last-digit equals your chosen digit (fair chance 10%). DIFFERS wins when it differs (fair chance 90%).",
            "ODD and EVEN win based on the final digit's parity. Each has a 50% fair chance. OVER X wins when the digit is above X; UNDER X wins below X.",
            "EAGLE-X's Digit Hacker measures the observed percentage vs the fair percentage, and flags deviations you can investigate."
          ],
          quiz: [
            { q: "Fair chance of a MATCHES contract?", a: ["10%", "50%", "90%"], correct: 0 },
            { q: "Which contract wins if the last digit equals your barrier?", a: ["MATCHES", "DIFFERS", "OVER"], correct: 0 },
            { q: "Fair chance of ODD?", a: ["50%", "10%", "25%"], correct: 0 }
          ]
        }
      },
      {
        title: "Reading Market Data",
        level: "Beginner", mins: 8,
        content: {
          body: [
            "Every panel on EAGLE-X shows rows of label / value pairs. The header badge (LIVE DATA / DEMO DATA) tells you where the numbers came from.",
            "The Tick Timer (RB) counts down to the next tick — green above 1.5s, yellow to 0.5s, red below that. Only trade in green/yellow windows."
          ],
          quiz: [
            { q: "What colour means the timer is fine to trade?", a: ["GREEN", "RED"], correct: 0 }
          ]
        }
      }
    ]
  },
  {
    category: "EAGLE-X Platform",
    lessons: [
      {
        title: "Using Market Master",
        level: "Beginner", mins: 12,
        content: {
          body: [
            "Market Master ranks all six contract types for a market by score. The top recommendation is highlighted in blue, and each contract shows a confidence percentage.",
            "A recommendation is only valid when data quality is solid and anomalies are low. Market Master itself checks those gates."
          ],
          quiz: [
            { q: "What does Market Master output?", a: ["A ranked list of contracts", "A random digit", "A profit guarantee"], correct: 0 }
          ]
        }
      },
      {
        title: "Auto Trader Setup",
        level: "Intermediate", mins: 15,
        content: {
          body: [
            "The Auto Trader (CF) scans markets continuously and places trades when evidence is strong. START PAPER runs it safely; STOP halts it.",
            "Risk rules gate every cycle: stake capped, stop-loss, profit target, cooldowns, and confirmation ticks. The Activity Log shows every decision."
          ],
          quiz: [
            { q: "Which button runs the bot safely?", a: ["START PAPER", "START LIVE", "STOP"], correct: 0 }
          ]
        }
      },
      {
        title: "Strategy Builder Deep-Dive",
        level: "Advanced", mins: 20,
        content: {
          body: [
            "The Strategy Builder lets you create, save, and run named strategies. Templates like 'MATCHES on OVERFED digit' give working starting points.",
            "Tune evidence deviation, data quality, and confidence thresholds; pick stake and duration; then deploy to the Auto Trader once validated."
          ],
          quiz: [
            { q: "Where do you deploy strategies?", a: ["Auto Trader", "Social Feed", "Video Hub"], correct: 0 }
          ]
        }
      }
    ]
  },
  {
    category: "Advanced Strategies",
    lessons: [
      {
        title: "Digit Frequency Trading",
        level: "Advanced", mins: 15,
        content: {
          body: [
            "Overfed digits (well above 10%) motivate MATCHES trades; starving digits (well below 10%) motivate DIFFERS. The Digit Hacker psychology tab surfaces both.",
            "Deviation size scales confidence, but randomness means deviation comes back over time. Track it, trade it, stop it."
          ],
          quiz: [
            { q: "An overfed digit suggests which contract?", a: ["MATCHES", "DIFFERS"], correct: 0 }
          ]
        }
      },
      {
        title: "Backtesting & Optimisation",
        level: "Advanced", mins: 15,
        content: {
          body: [
            "The Backtesting panel replays strategies on real tick history and reports win rate, profit factor, and drawdown.",
            "When you change parameters, validate on OUT-of-sample ticks. Overfitting is the biggest trap in strategy design."
          ],
          quiz: [
            { q: "What is overfitting?", a: ["Tuning to the same window you test on", "Using too few trades"], correct: 0 }
          ]
        }
      },
      {
        title: "Portfolio Diversification",
        level: "Advanced", mins: 10,
        content: {
          body: [
            "Portfolio Manager tracks assets by category, name, quantity, and price. Correlated markets concentrate risk; diversification spreads it.",
            "Check your concentration in Deriv indices versus crypto, stocks, or forex in the portfolio panel."
          ],
          quiz: [
            { q: "What does diversification reduce?", a: ["Concentrated risk", "Payouts"], correct: 0 }
          ]
        }
      }
    ]
  },
  {
    category: "Risk Management",
    lessons: [
      {
        title: "Stop-Loss Discipline",
        level: "Beginner", mins: 8,
        content: {
          body: [
            "The Risk Engine (GK) hard-caps your losses at 20% of balance per session. When it triggers, the Auto Trader halts — always.",
            "Never override a hard stop. Discipline is the only guaranteed edge."
          ],
          quiz: [
            { q: "What halts trading?", a: ["Stop-loss", "Profit target"], correct: 0 }
          ]
        }
      },
      {
        title: "Position Sizing & Stake Rules",
        level: "Intermediate", mins: 10,
        content: {
          body: [
            "The Auto Trader stakes at most 10% of balance per trade. Sizing down during drawdown preserves capital for the recovery phase."
          ],
          quiz: [
            { q: "Max stake per trade?", a: ["10% of balance", "Any amount"], correct: 0 }
          ]
        }
      },
      {
        title: "Drawdown Management",
        level: "Advanced", mins: 12,
        content: {
          body: [
            "Drawdown is the peak-to-trough decline of your equity. Three consecutive losses pause the Auto Trader until evidence recovers.",
            "The goal is not to avoid losses — it is to control their size and frequency so recovery is always possible."
          ],
          quiz: [
            { q: "After 3 consecutive losses the bot…", a: ["Pauses", "Increases stake", "Disables"], correct: 0 }
          ]
        }
      }
    ]
  },
  {
    category: "AI & Trading",
    lessons: [
      {
        title: "Using the AI Copilot",
        level: "Intermediate", mins: 10,
        content: {
          body: [
            "The AI Copilot (AMF) answers natural-language questions about markets, data quality, anomalies, and signals in plain English.",
            "Ask things like 'Should I trade MATCHES on 6?' or 'Explain the anomalies' — it summarises the analytics rather than inventing answers."
          ],
          quiz: [
            { q: "Is the Copilot guessing?", a: ["No — it explains the analytics", "Yes"], correct: 0 }
          ]
        }
      }
    ]
  }
];

const GLOSSARY = [
  { term: "OVERFED", def: "Digit appearing well above its fair 10% frequency." },
  { term: "STARVING", def: "Digit appearing well below its fair 10% frequency." },
  { term: "DATA QUALITY", def: "0-100 score for completeness, timeliness, consistency, validity." },
  { term: "STOP-LOSS", def: "Hard cap on losses that halts trading." },
  { term: "ANOMALY", def: "Unusual market behaviour (price spike, digit gap)." },
  { term: "SIGNAL", def: "Data strength verdict: STRONG, WEAK, or NEUTRAL." },
  { term: "DRAWDOWN", def: "Peak-to-trough decline in equity." },
  { term: "PAPER MODE", def: "Simulated trading without real money." },
  { term: "LIVE MODE", def: "Real trading via Deriv API using your token." },
  { term: "TICK TIMER", def: "Countdown to the next incoming tick." }
];

export default function Learn() {
  const [active, setActive] = useState<Lesson | null>(null);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const total = useMemo(() => COURSES.reduce((n, c) => n + c.lessons.length, 0), []);

  return (
    <main style={{ padding: "2rem", maxWidth: 1000, margin: "0 auto" }}>
      <a href="/dashboard" style={{ fontSize: "0.8rem" }}>← Back to Dashboard</a>
      <h1 style={{ fontSize: "1.5rem", margin: "0.5rem 0" }}>🎓 Education Hub</h1>
      <p style={{ color: "#8b949e", fontSize: "0.9rem" }}>
        {total} real lessons, quizzes, and a glossary. Tap a lesson to open it.
      </p>

      {COURSES.map((course) => (
        <section key={course.category} style={{ marginTop: "2rem" }}>
          <h2 style={{ fontSize: "1rem", color: "#58a6ff", marginBottom: "0.5rem" }}>{course.category}</h2>
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))" }}>
            {course.lessons.map((l) => (
              <button key={l.title} onClick={() => { setActive(l); setAnswers({}); }} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 12, textAlign: "left", cursor: "pointer" }}>
                <div style={{ fontWeight: 700, fontSize: "0.9rem", color: "#c9d1d9" }}>{l.title}</div>
                <div style={{ color: "#8b949e", fontSize: "0.75rem", marginTop: 4 }}>
                  {l.level} · {l.mins} min · {l.content.quiz.length} quiz questions
                </div>
                <div style={{ marginTop: 8, color: "#58a6ff", fontSize: "0.75rem" }}>Open lesson →</div>
              </button>
            ))}
          </div>
        </section>
      ))}

      <section style={{ marginTop: "2.5rem" }}>
        <h2 style={{ fontSize: "1rem", color: "#58a6ff", marginBottom: "0.5rem" }}>Glossary</h2>
        <div style={{ display: "grid", gap: 8 }}>
          {GLOSSARY.map((g) => (
            <div key={g.term} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, padding: 12 }}>
              <span style={{ fontWeight: 700, color: "#58a6ff" }}>{g.term}</span>
              <span style={{ color: "#c9d1d9" }}> — {g.def}</span>
            </div>
          ))}
        </div>
      </section>

      {active && (
        <div onClick={() => setActive(null)} style={{ position: "fixed", inset: 0, background: "#010409dd", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 50, padding: 16 }}>
          <div onClick={(e) => e.stopPropagation()} style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8, width: "min(760px, 100%)", maxHeight: "90vh", overflowY: "auto", padding: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <strong style={{ color: "#c9d1d9", fontSize: "1rem" }}>{active.title}</strong>
              <button onClick={() => setActive(null)} style={{ background: "#21262d", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: "4px 10px", cursor: "pointer" }}>✕ Close</button>
            </div>
            {active.content.body.map((p, i) => (
              <p key={i} style={{ color: "#c9d1d9", fontSize: "0.9rem", lineHeight: 1.6 }}>{p}</p>
            ))}
            <h3 style={{ color: "#58a6ff", marginTop: 16, fontSize: "0.9rem" }}>Interactive Quiz</h3>
            {active.content.quiz.map((q, qi) => (
              <div key={qi} style={{ marginTop: 12 }}>
                <div style={{ color: "#c9d1d9", fontWeight: 700, fontSize: "0.85rem" }}>{qi + 1}. {q.q}</div>
                {q.a.map((opt, oi) => {
                  const chosen = answers[qi];
                  const isCorrect = oi === q.correct;
                  const isChosen = chosen === oi;
                  const showResult = chosen !== undefined;
                  const bg = showResult ? (isCorrect ? "#3fb95033" : isChosen ? "#f85149" : "#161b22") : "#161b22";
                  const border = showResult ? (isCorrect ? "#3fb950" : isChosen ? "#f85149" : "#30363d") : "#30363d";
                  return (
                    <button key={oi}
                      onClick={() => setAnswers((a) => ({ ...a, [qi]: oi }))}
                      style={{ display: "block", width: "100%", textAlign: "left", marginTop: 6, background: bg, border: `1px solid ${border}`, borderRadius: 6, padding: "8px 12px", color: "#c9d1d9", cursor: "pointer", fontSize: "0.85rem" }}>
                      {opt}
                    </button>
                  );
                })}
                {answers[qi] !== undefined && (
                  <div style={{ color: answers[qi] === q.correct ? "#3fb950" : "#f85149", fontSize: "0.75rem", marginTop: 4 }}>
                    {answers[qi] === q.correct ? "✓ Correct" : "✗ Not quite — check the lesson above"}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </main>
  );
}
