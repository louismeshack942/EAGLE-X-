"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Card, Row, Btn } from "@/components/ui";

export default function TradingRooms({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [rooms, setRooms] = useState<any[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const d = await apiGet<any>("/rooms");
      setRooms(d?.rooms ?? d ?? []);
      setError(null);
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);

  const create = async () => {
    if (!name.trim()) return;
    try {
      await apiPost("/rooms", { name: name.trim() });
      setName("");
      await load();
    } catch (e: any) { setError(String(e.message ?? e)); }
  };

  return (
    <Card emoji="🎙️" title="TRADING ROOMS">
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      {rooms.length === 0 && !error && (
        <div style={{ color: "#8b949e", fontSize: "0.8rem" }}>No rooms yet — create the first one.</div>
      )}
      {rooms.slice(0, 6).map((r: any, i: number) => (
        <Row key={r?.id ?? i} label={r?.name ?? "Room"} value={r?.is_private ? "🔒 Private" : "🌐 Public"} />
      ))}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Room name"
          className="ex-input"
          style={{ flex: 1 }}
        />
        <Btn small variant="primary" onClick={create}>CREATE</Btn>
      </div>
    </Card>
  );
}
