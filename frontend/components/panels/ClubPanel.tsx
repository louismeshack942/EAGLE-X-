"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Card, Row, Pill, Btn } from "@/components/ui";

type ClubTab = "manager" | "news" | "fans" | "board" | "alerts";
const TABS: ClubTab[] = ["manager", "news", "fans", "board", "alerts"];

export default function ClubPanel({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [tab, setTab] = useState<ClubTab>("manager");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async (t: ClubTab = tab) => {
    setBusy(true);
    try {
      const d = await apiGet<any>(`/club/${t}`);
      setData(d); setErr(null);
    } catch (e: any) { setErr(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    load(tab);
    const t = setInterval(() => load(tab), refreshMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, refreshMs]);

  const moraleColor: Record<string, string> = {
    HIGH: "#3fb950", READY: "#58a6ff", PATIENT: "#8b949e", CAUTIOUS: "#d29922",
  };
  const alertColor: Record<string, string> = { high: "#f85149", medium: "#d29922" };

  return (
    <Card pos="GM" emoji="📣" title="CLUB"
      actions={<Pill label={tab.toUpperCase()} status="idle" pulse={busy} />}
    >
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
        {TABS.map((t) => (
          <Btn key={t} small variant={t === tab ? "primary" : "secondary"} onClick={() => setTab(t)}>
            {t.toUpperCase()}
          </Btn>
        ))}
      </div>
      {err && <div style={{ color: "#f85149", fontSize: "0.7rem" }}>{err}</div>}

      {tab === "manager" && data && (
        <>
          <Row label="Morale" value={data.morale} accent={moraleColor[data.morale] ?? "#c9d1d9"} />
          <Row label="Formation" value={data.formation} />
          <Row label="Data Quality" value={`${data.data_quality}/100`} accent={data.data_quality >= 70 ? "#3fb950" : "#f85149"} />
          <Row label="Anomalies" value={data.anomalies} accent={(data.anomalies ?? 0) >= 3 ? "#f85149" : "#8b949e"} />
          <div style={{ marginTop: 6, fontSize: "0.78rem", color: "#c9d1d9", fontStyle: "italic" }}>
            “{data.briefing}”
          </div>
          <div style={{ marginTop: 6 }}>
            {(data.directives ?? []).map((d: string, i: number) => (
              <div key={i} style={{ fontSize: "0.72rem", color: "#58a6ff", marginTop: 2 }}>▸ {d}</div>
            ))}
          </div>
        </>
      )}

      {tab === "news" && data && (
        <div style={{ maxHeight: 180, overflow: "auto" }}>
          {(data.headlines ?? []).map((h: any, i: number) => (
            <div key={i} style={{ fontSize: "0.73rem", marginBottom: 6, color: "#c9d1d9" }}>
              <span style={{ color: alertColor[h.importance] ?? "#8b949e", fontWeight: 700 }}>
                [{h.category}]
              </span>{" "}
              {h.headline} <span style={{ color: "#8b949e" }}>· {h.symbol}</span>
            </div>
          ))}
          {!(data.headlines ?? []).length && <div style={{ fontSize: "0.72rem", color: "#8b949e" }}>Quiet news day — markets calm.</div>}
          <div style={{ fontSize: "0.65rem", color: "#8b949e", marginTop: 4 }}>— {data.paper_name}</div>
        </div>
      )}

      {tab === "fans" && data && (
        <>
          <Row label="Crowd" value={data.crowd} accent="#3fb950" />
          <Row label="Sentiment" value={data.sentiment} />
          <Row label="Attendance" value={`${data.attendance}/${data.capacity}`} />
          <div style={{ marginTop: 8, fontSize: "0.85rem", color: "#c9d1d9", fontStyle: "italic", lineHeight: 1.3 }}>
            {data.chant}
          </div>
        </>
      )}

      {tab === "board" && data && (
        <>
          <Row label="Club Value" value={fmtUsd(data.club_value)} accent="#d29922" />
          <Row label="Daily P&L" value={fmtUsd(data.daily_pnl)} accent={(data.daily_pnl ?? 0) >= 0 ? "#3fb950" : "#f85149"} />
          <Row label="Board View" value={data.standings} accent={data.standings === "GOOD" ? "#3fb950" : "#d29922"} />
          <div style={{ marginTop: 6, fontSize: "0.72rem", color: "#c9d1d9" }}>{data.statement}</div>
          <div style={{ marginTop: 6 }}>
            {(data.sponsors ?? []).map((s: any, i: number) => (
              <div key={i} style={{ fontSize: "0.7rem", color: "#8b949e" }}>
                <span style={{ color: "#58a6ff" }}>{s.tier}</span> · {s.name} — {s.clause}
              </div>
            ))}
          </div>
        </>
      )}

      {tab === "alerts" && data && (
        <div style={{ maxHeight: 170, overflow: "auto" }}>
          {(data.alerts ?? []).map((a: any, i: number) => (
            <div key={i} style={{ fontSize: "0.72rem", marginBottom: 4, color: "#c9d1d9" }}>
              <span style={{ color: alertColor[a.severity] ?? "#8b949e", fontWeight: 700 }}>
                {a.type}
              </span>{" "}
              {a.message}
            </div>
          ))}
          {!(data.alerts ?? []).length && <div style={{ fontSize: "0.72rem", color: "#8b949e" }}>No active alerts — all clear.</div>}
        </div>
      )}
    </Card>
  );
}
