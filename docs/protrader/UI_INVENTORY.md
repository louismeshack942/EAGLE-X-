# UI Inventory — Observable ProTrader-class features

Complete inventory of observable interface components for the *class* of Deriv digit analyzer
tools. Each entry: NAME / LOCATION / PURPOSE / INPUT / OUTPUT / VISIBLE STATES / INTERACTIONS /
DEPENDENCIES. (Condensed from `docs/PHASE0_FINAL_SPECIFICATION.md` §2.)

| # | Name | Location | Purpose | Inputs | Outputs | States | Interactions | Dependencies |
|---|---|---|---|---|---|---|---|---|
| 1 | Landing page | `/` | introduce + CTA | – | hero/blurb | loading/loaded | → login | content |
| 2 | Login | `/login` | authenticate account | credentials | session/redirect | idle/submitting/error | submit | auth |
| 3 | Deriv OAuth consent | `deriv.com` | authorize app scopes | consent | auth code | consent/deny/error | approve/deny | OAuth |
| 4 | Dashboard/cockpit | `/cockpit` | main working area | live ticks | charts/stats/signals | connecting/live/disc. | select market | data bus |
| 5 | Sidebar | left | navigation | click | view swap | collapsed/expanded | nav | routing |
| 6 | Top bar | top | brand, account, status | – | – | – | account menu, logout | session |
| 7 | Market selector | cockpit | choose index | symbol | active market | idle/unavailable | select | active_symbols |
| 8 | Contract selector | cockpit | choose family | family | mode | idle/disabled | select | market |
| 9 | Analysis panels | cockpit | digit stats | ticks | freq %, rank | data/empty/stale | – | data |
| 10 | Chart | cockpit | price/ticks viz | ticks | series | live/loading/empty | pan/zoom | data |
| 11 | Digit displays | cockpit | per-digit analysis | ticks | freq%, streak, gap | data/empty | select digit | data |
| 12 | Signal display | cockpit | recommendation | analysis | signal state | NO_SIGNAL/active/expired | – | analysis |
| 13 | Trading controls | cockpit | configure trade | mkt,contract,stake,dur | readiness | valid/invalid | configure | contract spec |
| 14 | Stake controls | trading | stake amount | amount | stake | valid/invalid | input | money mgmt |
| 15 | Result displays | cockpit | outcome | contract result | WON/LOST | pending/won/lost | – | execution |
| 16 | History | cockpit | past trades | – | list | empty/populated | filter | storage |
| 17 | Settings | `/settings` | preferences | prefs | saved | idle/saving | save | storage |
| 18 | Account info | top bar | connected account | session | loginid/balance | connected/guest | view | session |
| 19 | Notifications | top bar | alerts | events | toast/banner | info/warn/error | dismiss | events |
| 20 | Modals | overlay | focused task | action | result | open/closed | confirm/cancel | UI state |
| 21 | Tooltips | hover | explain | hover | text | shown/hidden | hover | UI state |
| 22 | Loading states | all | in-flight | request | spinner/skeleton | active/done | – | request |
| 23 | Empty states | panels | no data | – | "No data" | empty | – | data |
| 24 | Error states | all | failure | – | message | error/retry | retry | service |
| 25 | Disconnected state | cockpit | link lost | – | CONNECTION LOST | disc./RECONNECTING | reconnect | transport |

**Unknown:** exact pixel spacing / typography / microcopy of any single canonical tool (BLACK BOX).