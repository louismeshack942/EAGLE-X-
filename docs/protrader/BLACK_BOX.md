# Black-Box Register

Everything EAGLE-X could NOT legitimately determine about the ProTrader Analysis Tool, and is
therefore implemented as EAGLE-X's own transparent equivalent (never claimed to replicate the
proprietary behavior).

| Item | Why it is black-box | EAGLE-X policy |
|---|---|---|
| Proprietary scoring formula | Not publicly disclosed | Use own documented statistical scoring (later phases) |
| Proprietary prediction formula | Not publicly disclosed | Use own transparent model (later phases) |
| Private APIs of third-party tool | Not accessible; do not probe | N/A — use Deriv public API only |
| Private backend logic | Not accessible | N/A — own backend |
| Proprietary XML / internal data | Not accessible | N/A — own data model |
| Private model architecture | Not public | Own architecture, documented |
| Hidden strategy selection | Not public | Own explicit config |
| Undocumented execution logic | Not public | Execution disabled in Phase 1 |
| Exact UI layout/pixels of one tool | Not verifiable | Implement own clean UI, parity at feature level |
| Exact analysis window sizes | Not disclosed reliably | Make window configurable + documented default |
| Per-account market availability | Broker/region-specific | Read live `active_symbols` at runtime |

## Non-goals
EAGLE-X does not attempt, and will never attempt:
- credential theft / phishing, bypassing authentication, unauthorized access
- reverse-engineering protected/private systems, extracting private source code or private XML
- exploiting vulnerabilities, intercepting other users' credentials

## Evidence sources (public, accessible at build date)
- Deriv developers docs — OAuth2, Authentication, Workflows, Digit Even/Odd, Digit
  Matches/Differs, Digit Over/Under, Active Symbols, Proposal
- Deriv blog — synthetic indices vs forex; How to use technical analysis tools on Deriv Bot
- Deriv public e-book — *How to Trade Synthetic Indices*
- Public third-party tool marketing pages (category-level descriptors only)

> Final classification for anything not listed here and not derivable from public Deriv docs:
> **UNKNOWN / BLACK BOX.**