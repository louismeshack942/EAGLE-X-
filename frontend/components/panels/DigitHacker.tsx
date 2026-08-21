"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";
import { Card, Row } from "@/components/ui";
import { Pill, Btn } from "@/components/ui";

type Tab = "frequency" | "psychology" | "contract" | "predictor" | "gaps";

export default function DigitHacker({ symbol = "R_100", refreshMs = 4000 }: { symbol?: string; refreshMs?: number }) {
  const [tab, setTab] = useState<Tab>("frequency");
  const [freq, setFreq] = useState<any>(null);
  const [psy, setPsy] = useState<any>(null);
  const [contract, setContract] = useState<any>(null);
  const [pred, setPred] = useState<any>(null);
  const [gaps, setGaps] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [f, p, c, pr, g] = await Promise.all([
          apiGet<any>(`/digits/${symbol}`),
          apiGet<any>(`/digits/${symbol}/psychology`),
          apiGet<any>(`/digits/${symbol}/contract`),
          apiGet<any>(`/digits/${symbol}/predictor`),
          apiGet<any>(`/digits/${symbol}/gaps`),
        ]);
        if (mounted) { setFreq(f); setPsy(p); setContract(c); setPred(pr); setGaps(g); setError(null); }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [symbol, refreshMs]);

  const tabs: { id: Tab; label: string }[] = [
    { id: "frequency", label: "Frequency" },
    { id: "psychology", label: "Psychology" },
    { id: "contract", label: "Contract" },
    { id: "predictor", label: "Predictor" },
    { id: "gaps", label: "Gaps" },
  ];

  return (
    <Card title="🔬 DIGIT HACKER">
      <div style={{ display: "flex", gap: 4, marginBottom: 8, flexWrap: "wrap" }}>
        {tabs.map((t) => (
          <Btn small key={t.id} variant={tab === t.id ? "primary" : "secondary"} onClick={() => setTab(t.id)}>
            {t.label}
          </Btn>
        ))}
      </div>
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}

      {tab === "frequency" && freq && (
        <div>
          {Object.entries(freq?.frequency ?? {}).map(([d, v]: any) => (
            <Row key={d} label={`Digit ${d}`} value={`${(v as any).percent}%`} accent={d === String(freq?.most_frequent) ? "#3fb950" : d === String(freq?.least_frequent) ? "#f85149" : undefined} />
          ))}
          <Row label="Entropy" value={freq?.entropy} />
          <Row label="Balance" value={`${freq?.balance}%`} />
        </div>
      )}

      {tab === "psychology" && psy && (
        <div>
          <p style={{ color: "#8b949e", fontSize: "0.75rem", marginBottom: 6 }}>OVERFED / CONFIRMATION / STARVING based on deviation from 10% fair frequency.</p>
          <Row label="Overfed" value={`Digit ${psy?.overfed?.digit} (${psy?.overfed?.percent}%)`} accent="#3fb950" />
          <Row label="Confirmation" value={`Digit ${psy?.confirmation?.digit} (${psy?.confirmation?.percent}%)`} />
          <Row label="Starving" value={`Digit ${psy?.starving?.digit} (${psy?.starving?.percent}%)`} accent="#f85149" />
        </div>
      )}

      {tab === "contract" && contract && (
        <div>
          {Object.entries(contract?.modes ?? {}).map(([mode, details]: any) => (
            <Row key={mode} label={mode} value={`${(details as any).evidence}`} accent={(details as any).evidence?.includes("SUPPORT") ? "#3fb950" : (details as any).evidence?.includes("CONTRARY") ? "#f85149" : undefined} />
          ))}
        </div>
      )}

      {tab === "predictor" && pred && (
        <div>
          <Row label="Candidate" value={`Digit ${pred?.candidate}`} accent="#58a6ff" />
          <Row label="Confidence" value={`${pred?.confidence}%`} />
          <Row label="Evidence" value={pred?.evidence} />
        </div>
      )}

      {tab === "gaps" && gaps && (
        <div>
          {Object.entries(gaps?.gaps ?? {}).map(([d, g]: any) => (
            <Row key={d} label={`Digit ${d}`} value={`cur: ${(g as any).current}, max: ${(g as any).max}`} />
          ))}
        </div>
      )}
    </Card>
  );
}
