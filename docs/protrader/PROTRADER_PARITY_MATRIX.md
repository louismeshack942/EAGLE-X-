# ProTrader Parity Matrix

Columns: FEATURE | OBSERVABLE PROTRADER BEHAVIOR | EAGLE-X REQUIREMENT | IMPLEMENTATION STATUS |
EVIDENCE | CONFIDENCE | UNKNOWN/BLACK BOX

Legend — Classification: OBSERVED (verified from public Deriv docs), PUBLICLY DOCUMENTED,
INFERRED (from consistent public marketing), UNKNOWN, BLACK BOX (proprietary).

## Authentication & Sessions
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| OAuth-style login | redirect to Deriv consent | Deriv OAuth2 code flow + PKCE | IMPLEMENTED | Deriv OAuth docs | HIGH (method) | specific UI |
| No-password model | password stays on Deriv | EAGLE-X never sees pwd | IMPLEMENTED | OAuth docs | HIGH | – |
| Session | persistent connected session | encrypted token store, session cookie | IMPLEMENTED | auth design | HIGH | – |
| Logout | user-initiated disconnect | destroy session server-side | IMPLEMENTED | UI spec | HIGH | – |
| Expired session | re-auth prompt | refresh / re-authorize state | PARTIAL | auth design | MEDIUM | exact UX |

## Markets & Contracts
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| Synthetic index list | R_10..R_100, RDBULL/BEAR | read live active_symbols | IMPLEMENTED | Deriv docs | HIGH | per-account availability |
| Contract families | MATCHES/DIFFERS/EVEN/ODD/OVER/UNDER | same 6 families | IMPLEMENTED | Deriv docs | HIGH | – |
| ≤10 tick duration | digit contracts ≤10 ticks | enforced in model | IMPLEMENTED | Deriv docs | HIGH | – |
| Barrier 0-9 | digit prediction barrier | 0-9 selectors | IMPLEMENTED | Deriv docs | HIGH | – |
| Live payout | payout shown per proposal | via Deriv proposal API | PARTIAL | Deriv API docs | MEDIUM | live price w/o creds |

## Analysis & Data
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| Real tick stream | live last digit ticks | connector + normalization | IMPLEMENTED | Deriv WS docs | HIGH | – |
| Last digit extraction | trailing digit shown | transparent digit extraction | IMPLEMENTED | public fact | HIGH | – |
| Frequency % | share per digit 0-9 | transparent window freq | IMPLEMENTED (basic) | marketing | MEDIUM | exact window |
| Streaks/gaps | consecutive / gaps | basic streaks/gaps | PARTIAL | marketing | MEDIUM | exact def |
| Charts | live price/digit viz | TradingView lightweight | IMPLEMENTED | UI spec | MEDIUM | exact style |
| Digit ranking | ordered digits | ordered by freq | PARTIAL | marketing | MEDIUM | exact key |
| Probability display | likelihood per digit | EAGLE-X own stats (later) | NOT IMPLEMENTED | marketing | MEDIUM | BB formula |
| Confidence | strength measure | EAGLE-X own (later) | NOT IMPLEMENTED | marketing | MEDIUM | BB formula |
| Signals | recommendation | EAGLE-X own (later) | NOT IMPLEMENTED | marketing | MEDIUM | BB formula |
| Contract recommendation | suggested trade | EAGLE-X own (later) | NOT IMPLEMENTED | marketing | MEDIUM | BB formula |

## Trading Workflow
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| Trade configure | market+contract+stake+dur | configurable UI | PARTIAL | UI spec | MEDIUM | exact flows |
| Purchase | place contract | **DISABLED in Phase 1** | NOT IMPLEMENTED (by design) | directive | HIGH | – |
| Result display | WON/LOST | reserved | NOT IMPLEMENTED | directive | HIGH | – |
| History/perf | past trades | reserved DB tables | NOT IMPLEMENTED | directive | HIGH | – |

## Error / State Handling
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| Connection lost | CONNECTION LOST | server + UI state | IMPLEMENTED | UI spec | HIGH | – |
| Reconnect | auto re-probe | reconnect + heartbeat | IMPLEMENTED | conn design | HIGH | – |
| Market unavailable | shows unavailable | from live active_symbols | IMPLEMENTED | feed | HIGH | – |
| Auth failure | re-auth prompt | AUTHORIZATION REQUIRED | IMPLEMENTED | auth design | HIGH | – |
| Empty/stale data | honest empty state | ENUM states | IMPLEMENTED | UI spec | HIGH | – |

## Responsive
| FEATURE | PROTRADER OBSERVABLE | EAGLE-X REQUIREMENT | STATUS | EVIDENCE | CONFIDENCE | UNKNOWN/BB |
|---|---|---|---|---|---|---|
| Mobile usable | works on mobile | responsive cockpit | IMPLEMENTED | UI spec | MEDIUM | exact breakpoints |
| Chart resizing | resizes w/ container | onResize | IMPLEMENTED | UI spec | MEDIUM | – |

## Notes
- No feature is marked OBSERVED-from-a-canonical-tool unless backed by public Deriv docs.
- Anything marked *marketing* is category-level (INFERRED), never a claim about one tool.