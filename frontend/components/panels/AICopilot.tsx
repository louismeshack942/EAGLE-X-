"use client";
import { useState } from "react";
import { apiPost } from "@/lib/api";
import { Card, Row, Btn } from "@/components/ui";

export default function AICopilot() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ask = async () => {
    if (!question.trim()) return;
    setBusy(true);
    try {
      const r = await apiPost<any>("/ai-copilot/ask", { question });
      setAnswer(r.answer);
    } catch (e: any) { setAnswer(`Error: ${e.message ?? e}`); }
    finally { setBusy(false); }
  };

  return (
    <Card title="🤖 AI COPILOT — AMF">
      <Row label="Ask" value="Anything about markets" />
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        placeholder="Should I trade MATCHES on 6?"
        style={{ width: "100%", minHeight: 60, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 6, padding: 8, fontFamily: "inherit", fontSize: "0.85rem" }}
      />
      <div style={{ marginTop: 6 }}>
        <Btn small variant="primary" disabled={busy} onClick={ask}>{busy ? "Thinking…" : "ASK"}</Btn>
      </div>
      {answer && (
        <div style={{ marginTop: 8, padding: 8, background: "#010409", borderRadius: 6, border: "1px solid #30363d", fontSize: "0.8rem", lineHeight: 1.5 }}>
          {answer}
        </div>
      )}
    </Card>
  );
}
