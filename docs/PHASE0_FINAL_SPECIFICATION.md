# PHASE 0 — FINAL SPECIFICATION
## ProTrader Observable Forensic Specification
> Prepared by EAGLE-X Batch 1 engineering. Phase 0 = observation & specification only.
> **Ethical boundary:** only *legitimately observable* public behavior of the ProTrader
> Analysis Tool is documented. Proprietary scoring, private APIs, private source code,
> undocumented backend logic and internal XML are **implicitly marked BLACK BOX** and are
> NOT reverse-engineered or guessed. EAGLE-X implements its own transparent equivalents.

---

## 1. ProTrader Overview

**OBSERVED (category of product, not inside knowledge):**

"ProTrader Analysis Tool" is one of several publicly marketed third‑party analysis tools
for Deriv **synthetic indices** ("Digits" markets). Public marketing materials describe it
as a tool that:

- streams real‑time tick data for Deriv synthetic/volatility indices
- analyzes the **last digit** of each tick
- computes **frequencies / percentages / streaks / gaps** of each last digit (0–9)
- offers analysis across contract families (Matches/Differs, Even/Odd, Over/Under)
- presents **probabilities** and **recommendations/signals**
- provides a dashboard UI with charts, statistics, and a trade panel

**Classification:** PUBLICLY DOCUMENTED / INFERRED from marketing. The *specific* third‑party
tool's internals are NOT observable and are BLACK BOX. EAGLE-X reproduces the *category of
observable behavior* with its own implementation.

| Item | Status | Evidence | Confidence |
|---|---|---|---|
| Category of product (Deriv synthetic-index digit analyzer) | PUBLICLY DOCUMENTED | Multiple public marketing/listing pages describe identical observable features | HIGH |
| Exact UI of any single specific "ProTrader" product | UNKNOWN | No verifiable public spec of one canonical tool | N/A |
| Proprietary scoring / prediction formula | BLACK BOX | Not publicly disclosed | N/A |

---

## 2. Complete UI Inventory

This is the inventory of the **observable class of features** such tools expose. Every item is
recorded with NAME / LOCATION / PURPOSE / INPUT / OUTPUT / STATES / INTERACTIONS /
DEPENDENCIES. Where a behavior cannot be verified it is marked UNKNOWN.

| # | Component | Location | Purpose | Inputs | Outputs | Visible states | Interactions | Dependencies |
|---|---|---|---|---|---|---|---|---|
| 1 | Landing page | site root | introduce product & CTA | none | hero, feature blurb | loading / loaded | navigate → login/signup | static content |
| 2 | Login page | /login | authenticate user | email/username + (Deriv meanwhile) password on Deriv host | redirect to authorization | idle / submitting / error | submit | auth backend |
| 3 | Deriv authorization page | deriv.com | user consents to app scopes | consent | OAuth code → callback | consent / deny / error | approve/deny | Deriv OAuth2 |
| 4 | Dashboard / cockpit | /app | main working area | live ticks | charts, stats, signals | connecting / live / disconnected | select market, contract | data bus |
| 5 | Sidebar navigation | left | navigate sections | click | view swap | collapsed/expanded | nav | routing |
| 6 | Top bar | top | branding, account, status | - | - | - | account menu, logout | session |
| 7 | Market selector | dashboard | choose index | index symbol | sets active market | idle / unavailable | select | active_symbols |
| 8 | Contract selector | dashboard | choose contract family | MATCHES/DIFFERS/EVEN/ODD/OVER/UNDER | sets analysis mode | idle / disabled | select | market |
| 9 | Analysis panels | dashboard | show digit stats | ticks | freq %, ranks | data / empty / stale | none | data |
| 10 | Chart | dashboard | visualize price/ticks | ticks | series | live / loading / empty | pan/zoom | data |
| 11 | Digit displays | dashboard | per-digit analysis | ticks | freq%, streak, gap | data / empty | select digit | data |
| 12 | Signal display | dashboard | show recommendation | analysis | signal state | NO-SIGNAL / active / expired | - | analysis |
| 13 | Trading controls | dashboard | configure trade | market, contract, stake, duration | readiness | valid / invalid | configure | contract spec |
| 14 | Stake controls | trading | set stake | amount | stake | valid / invalid | input | money mgmt |
| 15 | Result displays | dashboard | trade outcome | contract result | WON/LOST | pending / won / lost | - | execution |
| 16 | History | dashboard | past trades | - | list | empty / populated | filter | storage |
| 17 | Settings | /settings | configure app | preferences | saved prefs | idle / saving | save | storage |
| 18 | Account info | top bar | show connected account | session | loginid, balance(?) | connected / guest | view | session |
| 19 | Notifications | top bar | system alerts | events | toast/banner | info/warn/error | dismiss | events |
| 20 | Modals/dialogs | overlay | focus a task | action | result | open / closed | confirm/cancel | UI state |
| 21 | Tooltips | hover | explain controls | hover | description | shown / hidden | hover | UI state |
| 22 | Loading states | everywhere | indicate in-flight | request | spinner/skeleton | active / done | - | request |
| 23 | Empty states | panels | no data available | - | "No data" | empty | - | data |
| 24 | Error states | everywhere | failure | - | message | error / retry | retry | service |
| 25 | Disconnected state | dashboard | lost link | - | CONNECTION LOST | disconnected / RECONNECTING | reconnect | transport |

**UNKNOWN:** exact pixel spacing, exact widget styling, exact microcopy of any single canonical tool.

---

## 3. User Journey

```
OPEN SITE
   └─ Landing Page  [OBSERVED class]
        └─ LOGIN (email/user)
             └─ DERIV ACCOUNT AUTHORIZATION (OAuth consent on deriv.com host)
                  └─ Authorization CONFIRMED → callback back to EAGLE-X
                       └─ COCKPIT / DASHBOARD
                            └─ MARKET SELECTION
                                 └─ ANALYSIS (digit freq, streaks, gaps)
                                      └─ SIGNAL (recommendation or NO-SIGNAL)
                                           └─ CONTRACT SELECTION
                                                └─ STAKE
                                                     └─ TRADE / EXECUTION  [DISABLED in Phase 1]
                                                          └─ RESULT  [phase later]
                                                               └─ HISTORY / PERFORMANCE
                                                                    └─ LOGOUT
```

Steps marked comment are those a trading-capable tool exposes. **EAGLE-X marks EXECUTION /
RESULT as NOT IMPLEMENTED in Phase 1** (real-money trading disabled).

Missing/ambiguous steps that cannot be verified across the tool class are **UNKNOWN**.

---

## 4. Login / Authentication Specification

### 4.1 ProTrader observable experience (class-wide)
- **Entry:** Login button on landing page.
- **Login UI:** an account credential screen.
- **Authorization flow:** after credential handling the user is sent to a **Deriv consent page**
  to authorize the app to act on accounts (publicly documented Deriv OAuth practice).
- **Redirect:** after consent, Deriv redirects back to the app with an authorization code.
- **Account selection:** authorized account(s) become visible.
- **Connection confirmation:** the app shows connected account / loginid.
- **Failed auth:** error message, return to login.
- **Expired auth:** prompt to re-authorize.
- **Logout:** destroys app session.

### 4.2 EAGLE-X authentication implementation (Phase 1)
- EAGLE-X uses **Deriv OAuth2 Authorization Code flow + PKCE** (publicly documented).
- **EAGLE-X NEVER asks for, collects, or stores the user's Deriv password.**
- User is redirected to Deriv's hosted sign-in + consent page; Deriv returns a code;
  EAGLE-X exchanges the code (server-side) for an access token.
- Access/refresh tokens stored **server-side**, encrypted at rest, exposed to the frontend
  only as opaque session identifiers, never in logs.
- Timeout / refresh / reconnect / logout all handled by a session service.

---

## 5. Market Inventory

Deriv synthetic/volatility indices (publicly documented symbol family) that such a tool targets:

| Name | Symbol | Category | Description |
|---|---|---|---|
| Volatility 10 (1s) Index | R_10 | Volatility index | least volatile 1-second synthetic index |
| Volatility 25 (1s) Index | R_25 | Volatility index | 2.5x R_10 volatility |
| Volatility 50 (1s) Index | R_50 | Volatility index | 5x R_10 volatility |
| Volatility 75 (1s) Index | R_75 | Volatility index | 7.5x R_10 volatility |
| Volatility 100 (1s) Index | R_100 | Volatility index | 10x R_10 volatility |
| Volatility 50 (1s) Index (rise) | RDBULL | Volatility index | perpetual rise |
| Volatility 100 (1s) Index (fall) | RDBEAR | Volatility index | perpetual fall |

Other Deriv synthetic indices (e.g., jump indices, range break, step, combined) exist per
Deriv's public product docs. **EAGLE-X only lists markets that are actually returned as
*active* by the live Deriv `active_symbols` feed; it never invents availability.**
See `docs/protrader/MARKETS.md`.

---

## 6. Contract Inventory

Publicly documented Deriv **digit** option families (duration ≤ 10 ticks, barrier 0–9):

| Family | Purpose | Win condition | Restriction | Result |
|---|---|---|---|---|
| MATCHES | predict exact last digit | last digit equals selected digit | ≤10 ticks, barrier 0–9 | WON/LOST |
| DIFFERS | last digit differs from selected | last digit != selected digit | ≤10 ticks, barrier 0–9 | WON/LOST |
| ODD | last digit is odd | last digit in {1,3,5,7,9} | ≤10 ticks | WON/LOST |
| EVEN | last digit is even | last digit in {0,2,4,6,8} | ≤10 ticks | WON/LOST |
| OVER | last digit above barrier | last digit > barrier (0–9) | ≤10 ticks | WON/LOST |
| UNDER | last digit below barrier | last digit < barrier (0–9) | ≤10 ticks | WON/LOST |

Exact market payouts / durations shown are a function of the live Deriv `proposal` API and are
NOT hard-coded. See `docs/protrader/CONTRACTS.md`.

---

## 7. Analysis Inventory

Observable analytical features (class-wide), each recorded with WHAT/INPUTS/OUTPUT/UPDATE:

| Feature | Shows | Inputs | Output | Updates |
|---|---|---|---|---|
| Tick stream | latest price + time | live ticks | last quote | per tick |
| Digit frequency | count/percent per digit 0-9 | n recent ticks | freq % per digit | per tick window |
| Digit distribution | histogram of last digit | ticks | bar chart | per tick |
| Last digits | sequence of trailing digits | ticks | ordered list | per tick |
| Streaks | consecutive occurrences | ticks | current/max streak | per tick |
| Gaps | ticks since a digit last occurred | ticks | gap per digit | per tick |
| Percentages | share of each digit | ticks | % | per tick |
| Charts | price/digit viz | ticks | series | per tick |
| Volatility | fluctuation measure | ticks | vol indicator | windowed |
| Probability display | model likelihood per digit | historical + live | probabilities | windowed |
| Confidence | strength of a recommendation | features | 0-100 (tool-dependent) | windowed |
| Rankings | ordered digit strength | features | list | windowed |
| Signals | buy/no-trade recommendation | analysis | signal state | windowed |
| Contract recommendation | suggested family/barrier | analysis | recommendation | windowed |
| Historical statistics | past window summaries | stored data | stats | on update |
| Market comparisons | across indices | multiple symbols | comparison | on update |

**UNKNOWN / BLACK BOX:** any *specific* probability-scoring formula a third-party tool uses.

---

## 8. Digit Analysis

For each digit 0–9, the observable features are **frequency %, recency, streak, gap, ranking**.
EAGLE-X defines these transparently:

- **frequency(d) = count(d in last N ticks) / N**
- **gap(d)   = ticks since last occurrence of d (+1 if present)**
- **recent** = the last digit list
- **ranking** = digits ordered by frequency
- contract lens: digit d matches/differs; odd/even set; over/under barrier b

The **EXACT predictive formula** used by any third-party tool is **BLACK BOX**. EAGLE-X uses
its own transparent statistical implementation (later phases), never claiming it replicates a
proprietary formula.

---

## 9. Signal Behavior

Observable behavior class-wide (documented, not claimed for a specific tool):
- Signals appear after a configured analysis window.
- Signals can **expire** when the underlying data slides past relevance.
- Confidence can change per tick as the window updates.
- Multiple, possibly conflicting, signals can coexist across digits/contracts.
- Signals are typically **ranked**; color/state reflect strength.
- A **NO SIGNAL / no-trade** state is expected on a fair or weak board.

EAGLE-X will not claim the specific scoring formula; the signal layer (later phase) is
EAGLE-X's own transparent, documented algorithm labeled as such.

---

## 10. Trading Workflow

Observable workflow (execution disabled in Phase 1):
```
CONTRACT → MARKET → STAKE → DURATION → PREDICTION/BARRIER
  → PURCHASE → CONFIRMATION → RESULT(WON/LOST) → P/L → HISTORY
```
Visible states: draft / validated / submitted / confirmed / won / lost / error.
EAGLE-X Phase 1 reserves the interfaces but **does not implement purchase**.

---

## 11. Error and Edge States

| Condition | Observable behavior to implement |
|---|---|
| No internet / transport down | CONNECTION LOST |
| Disconnected account | prompt re-authorize |
| Unavailable market | MARKET UNAVAILABLE |
| Unavailable contract | CONTRACT UNAVAILABLE |
| Insufficient balance | INSUFFICIENT FUNDS |
| Invalid stake | INVALID STAKE |
| Expired signal | SIGNAL EXPIRED |
| Stale data | DATA STALE |
| Empty analysis | NO DATA |
| Server error | SERVER ERROR |
| Authorization failure | AUTHORIZATION REQUIRED |
| Session expiration | SESSION EXPIRED |

---

## 12. Responsive / UI Forensics

- Desktop: multi-column cockpit, sidebar, top bar.
- Tablet/mobile: single-column, collapsible sidebar, bottom/burger navigation.
- Chart resizes with container (view onResize), touch pannable.
- Buttons/pickers stack vertically on narrow viewports.
Exact breakpoints are implementation choices; EAGLE-X uses common breakpoints (e.g., 640/768/1024)
and does not claim to mirror a specific tool's private pixel spec.

---

## 13. ProTrader Parity Matrix

See `docs/protrader/PROTRADER_PARITY_MATRIX.md`.

---

## 14. Black-Box Register

See `docs/protrader/BLACK_BOX.md`.

---

## 15. Evidence Sources

- Deriv public product/educational docs (Digit Matches/Differs, Even/Odd, Over/Under; synthetic
  indices e-book; the Deriv blog; developers.deriv.com — OAuth2 + Workflows + Authentication).
- Public third-party tool marketing pages for the *category* (blink.new listing,
  ai.mobirise.com landing, bestderivanalysistool.com, dtide.co.ke) — category-level only.
- These are publicly accessible as of the build date; URLs stored in
  `docs/protrader/BLACK_BOX.md`/`PROTRADER_PARITY_MATRIX.md`.

---

## 16. Confidence Levels

- **HIGH:** Deriv product facts (symbols, contract win conditions, ≤10 ticks, barrier 0-9,
  OAuth flow) — from Deriv's own public docs.
- **MEDIUM / INFERRED:** the category of observable analysis features a Deriv digit analyzer
  exposes — from multiple consistent public marketing descriptions.
- **UNKNOWN / BLACK BOX:** any single canonical third-party tool's exact UI, algorithms,
  scoring, APIs, XML, backend.

---

## 17. Unknown Behavior

- Exact UI layout/typography/spacing of any single specific "ProTrader" product.
- Proprietary scoring / prediction / selection logic (BLACK BOX).
- Dates/numbers shown without our own implementation are not copied or claimed.
- Exact set of markets served to a given account region is only knowable via live
  `active_symbols`; EAGLE-X reads that truth at runtime.

---

*Phase 0 is considered complete for Phase 1 purposes: every observable feature required to
build the Phase 1 foundation is specified; everything proprietary is registered as BLACK BOX
rather than guessed.*