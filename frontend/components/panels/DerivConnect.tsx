"use client";
import { useEffect, useState } from "react";
import { apiGet, apiPost, apiDel, API_BASE } from "@/lib/api";
import { Card, Row, Btn, Pill } from "@/components/ui";

/**
 * Deriv account status. The token is configured server-side (env vars) and
 * the backend connects it automatically at every boot — there is nothing to
 * paste here. The panel shows the connected account, lets the owner switch
 * between accounts on the token, and offers Deriv's own OAuth login as an
 * alternative (no token pasting anywhere).
 */
export default function DerivConnect({ refreshMs = 5000 }: { refreshMs?: number }) {
  const [acct, setAcct] = useState<any>(null);
  const [accounts, setAccounts] = useState<any[]>([]);
  const [oauthAppId, setOauthAppId] = useState("");
  const [oauthCustom, setOauthCustom] = useState(false);
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
      const o = await apiGet<any>("/auth/oauth-app");
      setOauthCustom(o.custom);
      if (o.custom) setOauthAppId(String(o.app_id));
      setError(null);
    } catch (e: any) {
      setError(String(e.message ?? e));
    }
  };

  const saveOauthApp = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await apiPost<any>("/auth/oauth-app", { app_id: oauthAppId.trim() });
      if (r.saved) {
        setOauthCustom(r.custom);
        setMsg(r.custom
          ? `OAuth app id ${r.app_id} saved — CONNECT WITH DERIV now redirects to Deriv's real login.`
          : "OAuth app id cleared — button shows setup instructions again.");
      } else {
        setMsg(`Not saved: ${r.error}`);
      }
    } catch (e: any) {
      setMsg(`Not saved: ${e.message ?? e}`);
    } finally {
      setBusy(false);
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
              <div style={{ color: "#8b949e", fontSize: "0.7rem", marginBottom: 4 }}>SWITCH ACCOUNT</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {accounts.map((a) => {
                  const active = a.account_id === acct?.account_id;
                  return (
                    <button
                      key={a.account_id}
                      disabled={switching || active}
                      onClick={() => switchAccount(a.account_id)}
                      style={{
                        textAlign: "left",
                        background: active ? "#0d2818" : "#010409",
                        color: active ? "#3fb950" : "#c9d1d9",
                        border: `1px solid ${active ? "#3fb950" : "#30363d"}`,
                        borderRadius: 6,
                        padding: "6px 8px",
                        fontFamily: "monospace",
                        fontSize: "0.75rem",
                        cursor: active ? "default" : "pointer",
                      }}
                    >
                      <b style={{ color: a.is_virtual ? "#d29922" : "#58a6ff" }}>{a.is_virtual ? "DEMO" : "REAL"}</b>
                      {" — "}{a.loginid}{a.balance != null ? ` (${a.balance} ${a.currency ?? ""})` : ""}
                      {active ? "  ✓ ACTIVE" : ""}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          <div style={{ marginTop: 8 }}>
            <Btn small variant="danger" disabled={busy} onClick={disconnect}>DISCONNECT</Btn>
          </div>
        </>
      ) : (
        <>
          <p style={{ color: "#8b949e", fontSize: "0.75rem", marginBottom: 8, lineHeight: 1.4 }}>
            No token to paste — the server connects your Deriv account
            automatically at every boot. If this shows NOT CONNECTED, the
            server is still establishing the session (give it a few seconds)
            or the configured token needs attention on Render.
          </p>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            <Btn
              small
              variant="primary"
              onClick={() => window.open(`${API_BASE}/auth/deriv/login`, "_blank", "noopener,width=520,height=640")}
            >
              CONNECT WITH DERIV
            </Btn>
          </div>
          <div style={{ marginTop: 8 }}>
            <div style={{ color: "#8b949e", fontSize: "0.7rem", marginBottom: 4 }}>
              OAUTH APP ID {oauthCustom ? "(saved — button now redirects to Deriv login)" : "(activates the button — register an OAuth app on developers.deriv.com)"}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                type="text"
                value={oauthAppId}
                onChange={(e) => setOauthAppId(e.target.value)}
                placeholder="e.g. 77777"
                autoComplete="off"
                style={{
                  flex: 1,
                  background: "#010409",
                  color: "#c9d1d9",
                  border: "1px solid #30363d",
                  borderRadius: 6,
                  padding: "6px 8px",
                  fontFamily: "monospace",
                  fontSize: "0.75rem",
                }}
              />
              <Btn small disabled={busy} onClick={saveOauthApp}>SAVE</Btn>
            </div>
          </div>
        </>
      )}
      {msg && <div style={{ marginTop: 6, fontSize: "0.75rem", color: "#8b949e" }}>{msg}</div>}
    </Card>
  );
}
