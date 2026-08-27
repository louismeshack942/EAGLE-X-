# Contracts — Deriv Digit Options

Publicly documented Deriv **digit** contract families. Used by EAGLE-X Phase 1 for contract
selection / analysis-mode configuration only; **execution is disabled in Phase 1.**

> Source: Deriv docs (Digit Matches/Differs, Digit Even/Odd, Digit Over/Under).
> Duration is ≤ 10 ticks for digits; barrier (Matches/Differs/Over/Under) is 0–9.
> Classification: PUBLICLY DOCUMENTED → HIGH confidence.

| Family | Win condition | Barrier | Duration | Fair base rate | Stake input | Result |
|---|---|---|---|---|---|---|
| MATCHES | last digit == predicted digit | 0–9 | ≤10 ticks | 1/10 | yes | WON / LOST |
| DIFFERS | last digit != predicted digit | 0–9 | ≤10 ticks | 9/10 | yes | WON / LOST |
| ODD | last digit in {1,3,5,7,9} | none | ≤10 ticks | 1/2 | yes | WON / LOST |
| EVEN | last digit in {0,2,4,6,8} | none | ≤10 ticks | 1/2 | yes | WON / LOST |
| OVER | last digit > barrier | 0–9 | ≤10 ticks | (9-barrier)/10 | yes | WON / LOST |
| UNDER | last digit < barrier | 0–9 | ≤10 ticks | barrier/10 | yes | WON / LOST |

**Payout:** not hard-coded. EAGLE-X prices live via the Deriv `proposal` API when available;
payouts shown without a live proposal are labeled `MARKET PRICE UNKNOWN / NOT AVAILABLE`.

**First-tick rule:** the first tick is the entry spot; for digit contracts, outcome is the last
digit of the last tick of the contract period.

### Unknown / Black box
- Live payouts / spreads for a specific symbol are only knowable from a live proposal
- Any restrictions a third-party tool adds on top of Deriv's are UNKNOWN