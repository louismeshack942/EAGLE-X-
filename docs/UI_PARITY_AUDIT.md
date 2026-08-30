# UI Parity Audit — EAGLE-X vs ProTrader Analysis Tool

Target: https://protraderanalysistool.com/ (observable public evidence only; authenticated tool pages UNKNOWN, NOT invented.
Current: this repo /cockpit.

## Observable target evidence (public)
- Title/tagline:"ProTrader Analysis Tool — Synthetic Index Signals";"Live digit, even/odd, over/under and rise/fall analysis for synthetic indices; One-time $200 access with login, trading bot and full training."
- Feature scope: live digit analysis; EVEN/ODD; OVER/UNDER; RISE/FALL; MATCHES/DIFFERS; synthetic indices (Deriv volatility markets. Candlestick + bar chart types (TikTok feature list..
- Signal rule (TikTok:"Wait until it hits 70% and then enter your trade!" — confidence-threshold workflow; 30s-style countdown signal workflow cited by the task.

## Section-by-section audit

| Section | Target (observable) | Current EAGLE-X | Deviation | Correction |
|---|---|---|---|---|
| Top navigation | Brand + product nav + account (.login, toolbar per marketing. | Sidebar dev-layout (EAGLE-X, Cockpit, Landing, Logout. | Dev-chrome; no product nav | Trader topbar: brand, market select, connection status, data-source chip, account/logout |
| Market selection | In-tool synthetic-index picker | Card "Market connection" | Not in top chrome | Moved into top navigation bar (R_10..R_100. |
| Connection status | LIVE / DEMO indicator | Three chips (state, WS: OPEN, DATA SOURCE:. | Raw internals exposed | Single CONNECTED/CONNECTING/DISCONNECTED chip + LIVE/DEMO data-source chip |
| Analysis/refresh | Tool-level analysis actions | Buried in AnalysisPanel tabs | Not in main flow | Global Analyze stage + auto-refresh on market/window change |
| Main signal area | 70%-confidence threshold signal workflow | Signals only in ExecutionPanel tables | No hero signal | Hero Signal Stage: market, contract, digit/barrier, confidence%, stake, runs/remaining, 30s countdown, state (IDLE→ANALYZING→SIGNAL. |
| Digit analytics | Digit heatmap/distribution 0-9 (marketing. | Small 4-col grid + tables | Weak visual |  ㅤ10-digit heatmap tile grid with %, intensity, rank, recent strip, parity/streak/gap cards |
| Chart | Candlestick + indicators (observable. | Single line chart | No candles/indicators | Candles + EMA20 + Bollinger(20,2. + RSI14 on real ticks |
| Market distribution | Over/under, even/odd panels | Parity + over/under tables buried in tabs | Tabular, buried | "Market Distribution" stage: parity bar, over/under split, same-digit streak card |
| Contract presentation | EVEN/ODD/OVER/UNDER/MATCHES/DIFFERS families | Raw family codes + tables | Developer tables | Trader-facing family chips/cards with observed stats |
| Trade workflow | ANALYZE → SIGNAL → PROPOSAL → TRADE progression | ExecutionPanel execution controls inline | Jumbled | Visual 4-step workflow strip, live state honest |
| Trade history | Clean session history | Ledger table mixing modes | Modes mixed | History section with filter tabs; modes never merged in stats |
| Automation | "Trading bot" is a feature | "Phase 6 Automated Trader" dominates | Dev-phase labels dominate | "Trading Automation" in collapsible area; all safety/server switches preserved |
| Terminology | Product language (signal, confidence, trade. | "Phase 2/4/5/6", "HARNESS", "WS", "MASTER LIVE SWITCH" | Implementation language | Removed phase numbers + dev jargon from visible UI |
| Responsive | Mobile-friendly product | Sidebar collapses; cards shrink | Not recomposed | Recomposed grids at 768/560 into trader stacks |
| Authentication | Login (public /auth; private creds not copied. | EAGLE-X own /auth | Kept independent | EAGLE-X login retained; copy de-jargoned; no 3rd-party creds |
| Honesty | n/a | Honest HARNESS/LIVE labeling | None (good. | PRESERVED: DEMO vs LIVE; unfaked data; LIVE only on genuine Deriv session |

## UNKNOWN sections (never invented
Authenticated ProTrader tool pages (exact colors, typography, signal-table internals, live chart layout. — private,paywalled,and TLS-unreachable from this sandbox. Only publicly observable evidence used; remaining layout = deliberate trader-facing composition, NOT a claim of copying private pages.

## Acceptance checklist
- Target-like navigation (topbar chrome.
- Target-like dashboard hierarchy ( signal → digits → distribution → chart → contracts → workflow → history .
- Target-like signal presentation (hero 30s/70% stage.
- Target-like digit analytics presentation (heatmap grid.
- Target-like chart presentation (candles+EMA20+BB+RSI14 on real ticks. 
- Target-like contract presentation (family chips/cards.
- Target-like trade workflow (ANALYZE→SIGNAL→PROPOSAL→TRADE strip.
- Target-like spacing/typography (unified trader theme.
- Target-like mobile layout (recomposed stacks at 768/560.
- No developer phase labels dominating UI.
- No fake live data (DEMO/LIVE honest. 
- Existing backend preserved;; Deriv integration preserved;; safety controls preserved;; automation preserved (collapsible.
- Browser tested + production build passes.
