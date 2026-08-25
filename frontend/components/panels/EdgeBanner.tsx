"use client";
import { useEffect, useState } from "react";
import { apiGet } from "@/lib/api";

const WORST_TRAPS: Record<string, string> = { TRAP: "#f85149" };

export default function EdgeBanner({ refreshMs = 8000 }: { refreshMs?: number }) {
  const [board, setBoard] = useState<any>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const d = await apiGet<any>("/lab/edge-board");
        if (mounted) setBoard(d);
      } catch { /* banner is advisory; never block the page */ }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => { mounted = false; clearInterval(t); };
  }, [refreshMs]);

  if (!board) return null;
  const hasEdge = !!board.board_has_edge;
  const traps = (board.symbols ?? []).filter((s: any) => s.verdict === "TRAP").length;

  if (hasEdge) {
    const best = (board.symbols ?? [])[0];
    return (
      <div className="banner" style={{
        background: "#0d2818", border: "1px solid #3fb950", color: "#3fb950",
        borderRadius: 6, padding: "6px 12px", margin: "0 1rem 8px", fontSize: "0.78rem",
      }}>
        <span>🟢</span>
        <span><strong>REAL EDGE DETECTED</strong> — stakes justified on EDGE rows only.
          Best on board: {best?.symbol} {best?.best_contract} (EV {best?.ev > 0 ? "+" : ""}{best?.ev}/$1).
          Size with Kelly. Everything else is still the house.</span>
      </div>
    );
  }

  return (
    <div className="banner" style={{
      background: "#3d1214", border: "1px solid #f85149", color: "#ff7b72",
      borderRadius: 6, padding: "6px 12px", margin: "0 1rem 8px", fontSize: "0.78rem",
    }}>
      <span>🛑</span>
      <span><strong>NO EDGE — DO NOT TRADE.</strong> No contract on any symbol beats its breakeven
        win rate right now{traps > 0 ? ` (${traps} TRAP${traps === 1 ? "" : "s"}: significant patterns that still lose)` : ""}.
        The honest position is NO TRADE. Anything staked here pays the house.</span>
    </div>
  );
}
