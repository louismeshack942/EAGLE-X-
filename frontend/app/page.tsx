"use client";

import { apiGet, type ServerStatus } from "@/lib/api";
import { useEffect, useState } from "react";

export default function Landing() {
  const [status, setStatus] = useState<ServerStatus | null>(null);

  useEffect(() => {
    apiGet<ServerStatus>("/api/status").then(setStatus).catch(() => {});
  }, []);

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: "1.5rem" }}>
      <div style={{ maxWidth: 680, width: "100%", textAlign: "center" }}>
        <h1 style={{ fontSize: "2.2rem", margin: "0 0 .25rem" }}>🦅 EAGLE-X</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Trading Intelligence Platform — observable parity foundation (Phase 0 + 1)
        </p>

        <div className="card" style={{ textAlign: "left", marginTop: "1.5rem" }}>
          <div className="row spread">
            <span className="muted">Platform status</span>
            <span className="badge live">BACKEND ONLINE</span>
          </div>
          <div className="row spread" style={{ marginTop: ".5rem" }}>
            <span className="muted">Data source</span>
            <span className="badge harness">
              {status?.data_source?.toUpperCase() ?? "CHECKING…"}
            </span>
          </div>
          <div className="row spread" style={{ marginTop: ".5rem" }}>
            <span className="muted">Deriv OAuth configured</span>
            <span className="badge">{status?.oauth_configured ? "YES" : "NO"}</span>
          </div>
          <div className="row" style={{ marginTop: "1.25rem" }}>
            <a href="/cockpit/">
              <button className="btn">Open Cockpit</button>
            </a>
            {!status?.oauth_configured && (
              <span className="muted">Live data requires a configured Deriv OAuth app.</span>
            )}
          </div>
        </div>

        <div className="placeholder" style={{ marginTop: "1.5rem", textAlign: "left" }}>
          <b>Honest data policy.</b> This foundation never labels simulation as live. Market data
          is tagged by provider (harness vs deriv_live). Advanced analysis and trading are
          intentionally absent in Phase 1.
        </div>
      </div>
    </div>
  );
}