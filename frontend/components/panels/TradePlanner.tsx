"use client";
import { useState } from "react";
import { apiPost, fmtUsd } from "@/lib/api";
import { Card, Row, Btn } from "@/components/ui";

const CONTRACTS = ["CALL", "PUT", "DIGITMATCH", "DIGITDIFF", "DIGITODD", "DIGITEVEN", "DIGITOVER", "DIGITUNDER"];
const DIGIT_CONTRACTS = ["DIGITMATCH", "DIGITDIFF", "DIGITOVER", "DIGITUNDER"];

export default function TradePlanner() {
  const [symbol, setSymbol] = useState("R_100");
  const [contract, setContract] = useState("CALL");
  const [digit, setDigit] = useState("5");
  const [stake, setStake] = useState("1");
  const [duration, setDuration] = useState("60");
  const [token, setToken] = useState("");
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsDigit = DIGIT_CONTRACTS.includes(contract);

  const place = async () => {
    setBusy(true);
    try {
      const r = await apiPost<any>("/trade", {
        symbol,
        direction: contract,
        amount: Number(stake),
        duration: Number(duration),
        digit: needsDigit ? Number(digit) : undefined,
        api_token: token || undefined,
      });
      setResult(r);
      setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
    finally { setBusy(false); }
  };

  return (
    <Card pos="SS" emoji="⚡" title="TRADE PLANNER">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <Row label="Symbol" value={symbol} />
      <Row label="Contract" value={contract} />
      {needsDigit && <Row label="Digit (barrier)" value={digit} accent="#58a6ff" />}
      <Row label="Stake" value={fmtUsd(Number(stake))} />
      <Row label="Duration (s)" value={duration} />
      <div style={{ display: "flex", gap: 8, marginTop: 6, flexWrap: "wrap" }}>
        <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
          {["R_10", "R_25", "R_50", "R_75", "R_100"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={contract} onChange={(e) => setContract(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
          {CONTRACTS.map((c) => <option key={c}>{c}</option>)}
        </select>
        {needsDigit && (
          <select value={digit} onChange={(e) => setDigit(e.target.value)} style={{ background: "#010409", color: "#58a6ff", border: "1px solid #58a6ff", borderRadius: 4, padding: "4px 8px" }}>
            {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        )}
        <input value={stake} onChange={(e) => setStake(e.target.value)} placeholder="Stake" style={{ width: 70, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
        <input value={duration} onChange={(e) => setDuration(e.target.value)} placeholder="Dur (t)" style={{ width: 70, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
      </div>
      <input value={token} onChange={(e) => setToken(e.target.value)} placeholder="Deriv API token (optional override)" style={{ marginTop: 6, width: "100%", background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }} />
      <div style={{ marginTop: 8 }}>
        <Btn small variant="primary" disabled={busy} onClick={place}>PLACE TRADE</Btn>
      </div>
      {result && (
        <pre style={{ marginTop: 6, fontSize: "0.7rem", color: "#8b949e" }}>{JSON.stringify(result, null, 2)}</pre>
      )}
    </Card>
  );
}
