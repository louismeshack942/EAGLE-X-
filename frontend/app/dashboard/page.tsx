"use client";
import { useEffect, useState } from "react";
import { apiGet, fmtUsd } from "@/lib/api";
import { Pill } from "@/components/ui";
import RiskEngine from "@/components/panels/RiskEngine";
import IntelligenceEnginePanel from "@/components/panels/IntelligenceEngine";
import MostLikelyNumber from "@/components/panels/MostLikelyNumber";
import MarketMaster from "@/components/panels/MarketMaster";
import DigitHacker from "@/components/panels/DigitHacker";
import AutoTraderPanel from "@/components/panels/AutoTraderPanel";
import TradePlanner from "@/components/panels/TradePlanner";
import TradeJournal from "@/components/panels/TradeJournal";
import TickTimerPanel from "@/components/panels/TickTimer";
import DataQuality from "@/components/panels/DataQuality";
import AICopilot from "@/components/panels/AICopilot";
import StrategyBuilder from "@/components/panels/StrategyBuilder";
import Backtesting from "@/components/panels/Backtesting";
import SocialFeed from "@/components/panels/SocialFeed";
import CopyTrading from "@/components/panels/CopyTrading";
import Leaderboards from "@/components/panels/Leaderboards";
import PortfolioManager from "@/components/panels/PortfolioManager";
import RiskDashboard from "@/components/panels/RiskDashboard";
import PerformanceAnalytics, { DiversificationAnalyzer } from "@/components/panels/PerformanceAnalytics";

const SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100"];

export default function Dashboard() {
  const [symbol, setSymbol] = useState("R_100");
  const [status, setStatus] = useState<any>(null);
  const [lastTick, setLastTick] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const s = await apiGet<any>("/status");
        if (mounted) {
          setStatus(s);
          setError(null);
          setLastTick(s.last_tick ?? null);
        }
      } catch (e: any) { if (mounted) setError(String(e.message ?? e)); }
    };
    load();
    const t = setInterval(load, 2000);
    return () => { mounted = false; clearInterval(t); };
  }, []);

  const mode = status?.mode ?? "demo";
  const live = Boolean(status?.is_live);

  return (
    <div>
      <header className="header-bar">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="header-title">🦅 EAGLE-X</span>
          <Pill label={live ? "LIVE DATA" : "DEMO DATA"} color={live ? "#3fb950" : "#d29922"} />
          <Pill label={`Deriv ● ${live ? "LIVE" : "DEMO"}`} color={live ? "#3fb950" : "#8b949e"} />
          {lastTick && (
            <span style={{ color: "#8b949e", fontSize: "0.8rem" }}>
              {lastTick.symbol}: {lastTick.quote}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <span style={{ color: "#8b949e", fontSize: "0.8rem" }}>Market:</span>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ background: "#010409", color: "#c9d1d9", border: "1px solid #30363d", borderRadius: 4, padding: "4px 8px" }}>
            {SYMBOLS.map((s) => <option key={s}>{s}</option>)}
          </select>
          <a href="/learn" style={{ fontSize: "0.8rem" }}>Learn</a>
          <a href="/videos" style={{ fontSize: "0.8rem" }}>Videos</a>
        </div>
      </header>

      {error && <div style={{ margin: 12, padding: 12, background: "#161b22", border: "1px solid #f85149", borderRadius: 8, color: "#f85149", fontSize: "0.8rem" }}>
        Disconnected — {error}. The dashboard will retry automatically.
      </div>}

      <main style={{ padding: "1rem" }}>
        {/* STARTING XI */}
        <h2 style={{ fontSize: "0.85rem", color: "#8b949e", marginBottom: 8 }}>STARTING XI (4-3-3)</h2>
        <div className="main-grid">
          <RiskEngine />
          <IntelligenceEnginePanel symbol={symbol} />
          <DataQuality symbol={symbol} />
          <TickTimerPanel symbol={symbol} />
          <MostLikelyNumber symbol={symbol} />
          <MarketMaster symbol={symbol} />
          <AICopilot />
          <TradePlanner />
          <AutoTraderPanel />
        </div>

        {/* BENCH */}
        <h2 style={{ fontSize: "0.85rem", color: "#8b949e", margin: "1.5rem 0 8px" }}>SECOND XI (BENCH)</h2>
        <div className="main-grid">
          <TradeJournal />
          <DigitHacker symbol={symbol} />
          <StrategyBuilder />
          <Backtesting />
          <SocialFeed />
          <CopyTrading />
          <Leaderboards />
          <PortfolioManager />
          <RiskDashboard />
          <PerformanceAnalytics />
          <DiversificationAnalyzer />
        </div>
      </main>

      <footer style={{ padding: "1rem", borderTop: "1px solid #30363d", color: "#8b949e", fontSize: "0.75rem" }}>
        ⚠️ EAGLE-X is a statistical analysis tool — NOT a guaranteed-profit engine. All analytical scores are derived from actual data. Past performance does not guarantee future results. Never trade more than you can afford to lose.
      </footer>
    </div>
  );
}
