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
import DerivConnect from "@/components/panels/DerivConnect";
import StrategyBuilder from "@/components/panels/StrategyBuilder";
import Backtesting from "@/components/panels/Backtesting";
import SocialFeed from "@/components/panels/SocialFeed";
import CopyTrading from "@/components/panels/CopyTrading";
import Leaderboards from "@/components/panels/Leaderboards";
import TradingRooms from "@/components/panels/TradingRooms";
import PortfolioManager from "@/components/panels/PortfolioManager";
import RiskDashboard from "@/components/panels/RiskDashboard";
import PerformanceAnalytics, { DiversificationAnalyzer } from "@/components/panels/PerformanceAnalytics";
import ClubPanel from "@/components/panels/ClubPanel";
import VirtualBankPanel from "@/components/panels/VirtualBankPanel";
import SessionRoom from "@/components/panels/SessionRoom";
import TraderScriptPanel from "@/components/panels/TraderScriptPanel";
import DigitHackerToolPanel from "@/components/panels/DigitHackerToolPanel";
import ProTraderPanel from "@/components/panels/ProTraderPanel";

const SYMBOLS = ["R_10", "R_25", "R_50", "R_75", "R_100"];

function SectionHeader({ emoji, title, sub }: { emoji: string; title: string; sub?: string }) {
  return (
    <h2 className="section-header">
      <span className="sh-emoji">{emoji}</span>
      {title}
      {sub && <span className="sh-sub">{sub}</span>}
    </h2>
  );
}

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

  const live = Boolean(status?.is_live);
  const balance = status?.balance;

  return (
    <div>
      <header className="header-bar">
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="header-title">🦅 EAGLE-X</span>
          <Pill label={live ? "LIVE DATA" : "DEMO DATA"} status={live ? "live" : "demo"} pulse={live} />
          <Pill label={`Deriv ${live ? "● LIVE" : "● DEMO"}`} status={live ? "live" : "neutral"} />
          {lastTick && (
            <span className="header-price">last: {lastTick.quote}</span>
          )}
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {typeof balance === "number" && (
            <span style={{ color: "#F5C518", fontWeight: 800, fontSize: "0.9rem", fontVariantNumeric: "tabular-nums" }}>
              {fmtUsd(balance)}
            </span>
          )}
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            className="ex-select"
            aria-label="Market"
          >
            {SYMBOLS.map((s) => <option key={s}>{s}</option>)}
          </select>
          <a href="/learn" style={{ fontSize: "0.8rem" }}>Learn</a>
          <a href="/videos" style={{ fontSize: "0.8rem" }}>Videos</a>
        </div>
      </header>

      {error && (
        <div className="banner banner-error">
          <span>🔌</span>
          <span><strong>Disconnected</strong> — {error}. The dashboard will retry automatically.</span>
        </div>
      )}

      {!error && status?.last_error && (
        <div className="banner banner-warn">
          <span>⚠️</span>
          <span>{status.last_error}</span>
        </div>
      )}

      <main style={{ padding: "0 1rem 1rem" }}>
        <SectionHeader emoji="🏆" title="Starting XI" sub="4-3-3" />
        <div className="main-grid">
          <VirtualBankPanel />
          <RiskEngine />
          <IntelligenceEnginePanel symbol={symbol} />
          <DataQuality symbol={symbol} />
          <TickTimerPanel symbol={symbol} />
          <MostLikelyNumber symbol={symbol} />
          <MarketMaster symbol={symbol} />
          <AICopilot />
          <DerivConnect />
          <TradePlanner />
          <AutoTraderPanel />
        </div>

        <SectionHeader emoji="📣" title="The Club" sub="Manager · Board · News · Fans · Alerts" />
        <div className="main-grid">
          <ClubPanel />
        </div>

        <SectionHeader emoji="⚔️" title="The Competition" sub="Speed · Digits · Analysis" />
        <div className="main-grid">
          <TraderScriptPanel />
          <DigitHackerToolPanel symbol={symbol} />
          <ProTraderPanel symbol={symbol} />
        </div>

        <SectionHeader emoji="🔄" title="Second XI" sub="Bench" />
        <div className="main-grid">
          <SessionRoom />
          <TradeJournal />
          <DigitHacker symbol={symbol} />
          <StrategyBuilder />
          <Backtesting />
          <SocialFeed />
          <CopyTrading />
          <Leaderboards />
          <TradingRooms />
          <PortfolioManager />
          <RiskDashboard />
          <PerformanceAnalytics />
          <DiversificationAnalyzer />
        </div>
      </main>

      <footer className="disclaimer">
        ⚠️ <strong>EAGLE-X is a statistical analysis tool — NOT a guaranteed-profit engine.</strong><br />
        All analytical scores are derived from actual data. Past performance does not guarantee future results.
        Never trade more than you can afford to lose.
      </footer>
    </div>
  );
}
