"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Card, Btn, Row } from "@/components/ui";

export default function CopyTrading({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [leaders, setLeaders] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try { const r = await apiGet<any>("/copy/leaders"); setLeaders(r.leaders ?? []); setError(null); }
    catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => { load(); const t = setInterval(load, refreshMs); return () => clearInterval(t); }, [refreshMs]);

  const follow = async (leaderId: string) => {
    try { await apiPost("/copy/follow", { user_id: "user-1", leader_id: leaderId }); await load(); }
    catch (e: any) { setError(String(e.message ?? e)); }
  };

  const register = async () => {
    if (!name.trim()) return;
    try { await apiPost("/copy/leaders", { name, copy_ratio: 0.1, bio: "" }); setName(""); await load(); }
    catch (e: any) { setError(String(e.message ?? e)); }
  };

  return (
    <Card emoji="👥" title="COPY TRADING">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Register as leader"
          style={{ flex: 1, background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}
        />
        <Btn small variant="primary" onClick={register}>REGISTER</Btn>
      </div>
      <div style={{ maxHeight: 160, overflow: "auto", fontSize: "0.75rem" }}>
        {leaders.map((l: any) => (
          <div key={l.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "4px 0", borderBottom: "1px solid #21262d" }}>
            <span>{l.name} · {l.followers} followers</span>
            <Btn small variant="success" onClick={() => follow(l.id)}>FOLLOW</Btn>
          </div>
        ))}
        {leaders.length === 0 && <div style={{ color: "#8b949e" }}>No leaders yet.</div>}
      </div>
    </Card>
  );
}
