"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, fmtUsd } from "@/lib/api";
import { Btn, Card, Pill, Row } from "@/components/ui";

/** The Treasurer + the Guard + the Scout, one panel.
 *
 * - Virtual bank: current (spendable) vs vault (60% of every profit, locked).
 * - Kill switch: one big red button that stops EVERYTHING instantly.
 * - Guard: mode (FULL_AUTO / COACH / FULL_MANUAL), $ limits, approvals queue.
 * - Table heat: the scout's verdict on every symbol.
 */
export default function VirtualBankPanel({ refreshMs = 4000 }: { refreshMs?: number }) {
  const [bank, setBank] = useState<any>(null);
  const [guard, setGuard] = useState<any>(null);
  const [tables, setTables] = useState<any>(null);
  const [amount, setAmount] = useState("10");
  const [stake, setStake] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    try {
      const [b, g, t] = await Promise.all([
        apiGet<any>("/bank"),
        apiGet<any>("/guard"),
        apiGet<any>("/scout/tables"),
      ]);
      setBank(b); setGuard(g); setTables(t);
    } catch { /* panel retries on interval */ }
  };

  useEffect(() => {
    let mounted = true;
    const safe = async () => { if (mounted) await load(); };
    safe();
    const t = setInterval(safe, refreshMs);
    return () => { mounted = false; clearInterval(t); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshMs]);

  const act = async (fn: () => Promise<any>, note: string) => {
    try { await fn(); setMsg(note); await load(); }
    catch (e: any) { setMsg(String(e.message ?? e)); }
  };

  const killed = Boolean(guard?.killed);
  const mode = guard?.mode ?? "FULL_AUTO";
  const pending = guard?.pending_approvals ?? [];

  return (
    <Card
      pos="TR"
      emoji="🏦"
      title="VIRTUAL BANK"
      actions={<Pill label={killed ? "KILLED" : mode} status={killed ? "stopped" : "running"} pulse={!killed} />}
    >
      {/* Kill switch — the biggest, reddest button on the dashboard */}
      <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
        {!killed ? (
          <Btn variant="danger" title="Stop everything instantly"
            onClick={() => act(() => apiPost("/guard/kill?reason=manager+pulled+the+kill+switch"), "KILL SWITCH DOWN — all trading stopped")}>
            🛑 KILL SWITCH
          </Btn>
        ) : (
          <Btn variant="success" title="Release the kill switch"
            onClick={() => act(() => apiPost("/guard/release"), "Kill switch released")}>
            ✅ RELEASE
          </Btn>
        )}
        <Btn small variant="secondary" title="CF trades freely"
          onClick={() => act(() => apiPost("/guard/mode", { mode: "FULL_AUTO" }), "Mode: FULL_AUTO")}>AUTO</Btn>
        <Btn small variant="secondary" title="CF proposes, you confirm"
          onClick={() => act(() => apiPost("/guard/mode", { mode: "COACH" }), "Mode: COACH")}>COACH</Btn>
        <Btn small variant="secondary" title="CF advises only"
          onClick={() => act(() => apiPost("/guard/mode", { mode: "FULL_MANUAL" }), "Mode: FULL_MANUAL")}>MANUAL</Btn>
        <Btn small variant="secondary" title="Speed of the bots, brakes of the Guard"
          onClick={() => act(() => apiPost("/guard/mode", { mode: "HYBRID" }), "Mode: HYBRID")}>HYBRID</Btn>
        <Btn small variant="primary" title="Plug-in hybrid: fewer, bigger, cleaner strikes"
          onClick={() => act(async () => { await apiPost("/guard/mode", { mode: "PHEV" }); await apiPost("/guard/preset/PHEV"); }, "Mode: PHEV — engine only runs when the market is charging")}>PHEV</Btn>
      </div>

      {killed && (
        <div style={{ color: "#f85149", fontSize: "0.8rem", marginBottom: 6 }}>
          🛑 {guard?.kill_reason} — nobody plays until you release.
        </div>
      )}
      {msg && <div style={{ color: "#8b949e", fontSize: "0.75rem", marginBottom: 6 }}>{msg}</div>}

      {/* Manual stake — the manager sets the bullet size himself */}
      <div style={{ display: "flex", gap: 6, marginBottom: 8, alignItems: "center", borderTop: "1px solid #21262d", paddingTop: 8 }}>
        <span style={{ fontSize: "0.78rem", color: "#8b949e" }}>
          Stake: <strong style={{ color: "#e6edf3" }}>
            {guard?.stake_override > 0 ? `${fmtUsd(guard.stake_override)} (manual)` : "auto (10%)"}
          </strong>
        </span>
        <input
          className="ex-select" style={{ width: 90 }} placeholder="$ stake" value={stake}
          onChange={(e) => setStake(e.target.value)} aria-label="manual stake"
        />
        <Btn small variant="primary" title="Fire every play at exactly this amount"
          onClick={() => act(() => apiPost("/guard/stake", { amount: Number(stake) || 0 }), `Manual stake set: $${stake}`)}>
          Set
        </Btn>
        {guard?.stake_override > 0 && (
          <Btn small variant="secondary" title="Return to the 10% rule"
            onClick={() => act(() => apiPost("/guard/stake", { amount: 0 }), "Back to auto (10%)")}>
            Auto
          </Btn>
        )}
      </div>

      <Row label="Current (spendable)" value={fmtUsd(bank?.current_balance ?? 0)} accent="#58a6ff" />
      <Row label="Vault (protected)" value={fmtUsd(bank?.vault_balance ?? 0)} accent="#3fb950" />
      <Row label="Total account" value={fmtUsd(bank?.total_balance ?? 0)} accent="#F5C518" />
      <Row label="Rule" value={bank?.split_label ?? "60% of every profit is locked in the vault"} />
      <Row label="Protected" value={`${bank?.protected_pct ?? 0}%`} />
      <Row label="Lifetime profit / loss" value={`${fmtUsd(bank?.total_profit ?? 0)} / ${fmtUsd(bank?.total_loss ?? 0)}`} />

      <div style={{ display: "flex", gap: 6, margin: "8px 0", alignItems: "center" }}>
        <input
          className="ex-select" style={{ width: 80 }} value={amount}
          onChange={(e) => setAmount(e.target.value)} aria-label="amount"
        />
        <Btn small variant="secondary" title="Move vault → current"
          onClick={() => act(() => apiPost("/bank/withdraw", { amount: Number(amount) || 0 }), "Withdrawn to current")}>
          ← Withdraw
        </Btn>
        <Btn small variant="secondary" title="Move current → vault (protect it)"
          onClick={() => act(() => apiPost("/bank/deposit", { amount: Number(amount) || 0 }), "Protected in the vault")}>
          Deposit →
        </Btn>
      </div>

      {pending.length > 0 && (
        <div style={{ borderTop: "1px solid #21262d", paddingTop: 6, marginTop: 4 }}>
          <div style={{ fontSize: "0.8rem", color: "#F5C518", marginBottom: 4 }}>
            ⏳ Waiting for your call ({pending.length})
          </div>
          {pending.map((a: any) => (
            <div key={a.id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.78rem", marginBottom: 4 }}>
              <span style={{ flex: 1 }}>{a.play?.symbol} {a.play?.plays?.[0]?.name}</span>
              <Btn small variant="success" onClick={() => act(() => apiPost(`/guard/approvals/${a.id}`, { approve: true }), "Approved — CF fires")}>✓</Btn>
              <Btn small variant="danger" onClick={() => act(() => apiPost(`/guard/approvals/${a.id}`, { approve: false }), "Rejected")}>✗</Btn>
            </div>
          ))}
        </div>
      )}

      <div style={{ borderTop: "1px solid #21262d", paddingTop: 6, marginTop: 6 }}>
        <div style={{ fontSize: "0.8rem", color: "#8b949e", marginBottom: 4 }}>🔭 Table Scout</div>
        <div style={{ fontSize: "0.78rem", color: "#e6edf3" }}>{tables?.summary ?? "scanning…"}</div>
        {(tables?.tables ?? []).slice(0, 5).map((t: any) => (
          <Row key={t.symbol} label={t.symbol}
            value={t.tradeable ? "🔥 HOT" : "fair"}
            accent={t.tradeable ? "#3fb950" : "#8b949e"} />
        ))}
      </div>
    </Card>
  );
}
