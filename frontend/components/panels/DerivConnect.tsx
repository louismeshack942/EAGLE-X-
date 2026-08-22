"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, apiDel, API_BASE } from "@/lib/api";
import { Card, Row, Btn, Pill } from "@/components/ui";

/**
 * Connect the owner's personal Deriv account — WITHOUT pasting a token
 * into chat. Two paths:
 *   1. OAuth: "CONNECT WITH DERIV" opens Deriv's own login in a new tab;
 *      Deriv redirects back through the backend which stores the token.
 *   2. Manual: paste a token into the form (over HTTPS); it goes straight
 *      to the backend, gets validated, and is never displayed.
 */
export default function DerivConnect({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [acct, setAcct] = useState<any>(null);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [token, setToken] = useState("");
  const [appId, setAppId] = useState("");
  const [showTokenForm, setShowTokenForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const s = await apiGet<any>("/status");
      setAcct(s.deriv_account ?? null);
      if (s.deriv_account?.connected) {
        const a = await apiGet<any>("/auth/accounts");
        setAccounts(a.accounts ?? []);
      } else {
        setAccounts([]);
      }
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  };

  const switchAccount = async (accountId: string) => {
    if (!accountId || accountId === acct?.account_id) return;
    setSwitching(true);
    setMsg(null);
    try {
      const r = await apiPost<any>("/auth/account/switch", { account_id: accountId });
      if (r.switched) {
        setMsg(`Switched to ${r.loginid} (${r.is_virtual ? "DEMO" : "REAL"})`);
      } else {
        setMsg(`Switch failed: ${r.error}`);
      }
      await load();
    } catch (e: any) {
      setMsg(`Switch failed: ${e.message ?? e}`);
    } finally {
      setSwitching(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [refreshMs]);

  const connectManual = async () => {
    if (!token.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPost<any>("/auth/token", {
        token: token.trim(),
        ...(appId.trim() ? { app_id: appId.trim() } : {}),
      });
      if (r.connected) {
        setMsg(`Connected: ${r.loginid} (${r.currency})`);
        setToken("");
        setAppId("");
        setShowTokenForm(false);
      } else {
        setMsg(`Failed: ${r.error}`);
      }
      await load();
    } catch (e: any) {
      setMsg(`Failed: ${e.message ?? e}`);
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    setBusy(true);
    try {
      await apiDel("/auth/token");
      setMsg("Disconnected");
      await load();
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  };

  const connected = Boolean(acct?.connected);

  return (
    <Card emoji="🔗" title="CONNECT DERIV"
      actions={<Pill label={connected ? "CONNECTED" : "NOT CONNECTED"} status={connected ? "running" : "neutral"} />}
    >
      {error && <div style={{ color: "#f85149", fontSize: "0.75rem" }}>{error}</div>}
      {connected ? (
        <>
          <Row label="Account" value={acct?.loginid ?? "—"} accent="#3fb950" />
          <Row label="Currency" value={acct?.currency ?? "—"} />
          <Row label="Balance" value={acct?.balance != null ? String(acct.balance) : "—"} />
          {accounts.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ color: "#8b949e", fontSize: "0.7rem", marginBottom: 4 }}>ACCOUNT (DEMO = VRTC, REAL = CR)</div>
              <select
                value={acct?.account_id ?? ""}
                disabled={switching}
                onChange={(e) => switchAccount(e.target.value)}
                style={{
                  width: "100%",
                  background: "#010409",
                  color: "#c9d1d9",
                  border: "1px solid #30363d",
                  borderRadius: 6,
                  padding: "6px 8px",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                }}
              >
                {accounts.map((a) => (
                  <option key={a.account_id} value={a.account_id}>
                    {(a.is_virtual ? "DEMO" : "REAL") + " — " + a.loginid + (a.balance != null ? ` (${a.balance} ${a.currency ?? ""})` : "")}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <Btn small variant="danger" disabled={busy} onClick={disconnect}>DISCONNECT</Btn>
          </div>
        </>
      ) : (
        <>
          <p style={{ color: "#8b949e", fontSize: "0.75rem", marginBottom: 8, lineHeight: 1.4 }}>
            Link your Deriv account to enable live trading. Your token is never
            shown or sent to chat — it goes straight to Deriv and your server.
            (The OAuth button needs a registered app id — until then it shows
            setup steps instead of a broken page.)
          </p>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Btn
              small
              variant="primary"
              onClick={() => window.open(`${API_BASE}/auth/deriv/login`, "_blank", "noopener,width=520,height=640")}
            >
              CONNECT WITH DERIV
            </Btn>
            <Btn small variant="secondary" onClick={() => setShowTokenForm((v) => !v)}>
              {showTokenForm ? "HIDE" : "PASTE TOKEN"}
            </Btn>
          </div>
          {showTokenForm && (
            <div style={{ marginTop: 8 }}>
              <input
                type="password"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="Deriv token (developers.deriv.com)"
                autoComplete="off"
                style={{
                  width: "100%",
                  background: "#010409",
                  color: "#c9d1d9",
                  border: "1px solid #30363d",
                  borderRadius: 6,
                  padding: "8px 10px",
                  fontFamily: "monospace",
                }}
              />
              <input
                type="text"
                value={appId}
                onChange={(e) => setAppId(e.target.value)}
                placeholder="App id (only for pat_ tokens)"
                autoComplete="off"
                style={{
                  width: "100%",
                  background: "#010409",
                  color: "#c9d1d9",
                  border: "1px solid #30363d",
                  borderRadius: 6,
                  padding: "8px 10px",
                  fontFamily: "monospace",
                  marginTop: 6,
                }}
              />
              <p style={{ color: "#8b949e", fontSize: "0.7rem", marginTop: 4, lineHeight: 1.3 }}>
                Modern pat_ tokens need your registered app id — register one
                free app on developers.deriv.com to get it. Old tokens work
                with the field empty.
              </p>
              <div style={{ marginTop: 6 }}>
                <Btn small variant="success" disabled={busy || !token.trim()} onClick={connectManual}>
                  {busy ? "VALIDATING…" : "CONNECT"}
                </Btn>
              </div>
            </div>
          )}
        </>
      )}
      {msg && <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#8b949e" }}>{msg}</div>}
    </Card>
  );
}
