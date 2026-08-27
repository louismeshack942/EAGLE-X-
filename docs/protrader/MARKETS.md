# Markets — Deriv Synthetic / Volatility Indices

Publicly documented Deriv synthetic symbols that a digit analysis tool targets. EAGLE-X does
**not** invent availability: at runtime it queries the live Deriv `active_symbols` feed and
only displays indices Deriv actually serves to the connected account/region.

> Source: Deriv public product docs (How to Trade Synthetic Indices e-book, Deriv docs).
> Classification: PUBLICLY DOCUMENTED → HIGH confidence for symbol identity. Availability is
> account/region dependent (UNKNOWN until a live feed speaks).

| Name | Symbol | Category | Description |
|---|---|---|---|
| Volatility 10 (1s) Index | R_10 | Volatility index (1s) | lowest-volatility 1-second synthetic index |
| Volatility 25 (1s) Index | R_25 | Volatility index (1s) | 2.5x R_10 |
| Volatility 50 (1s) Index | R_50 | Volatility index (1s) | 5x R_10 |
| Volatility 75 (1s) Index | R_75 | Volatility index (1s) | 7.5x R_10 |
| Volatility 100 (1s) Index | R_100 | Volatility index (1s) | 10x R_10 |
| Volatility 50 (1s) Index (rise) | RDBULL | Volatility index | perpetual-rise variant |
| Volatility 100 (1s) Index (fall) | RDBEAR | Volatility index | perpetual-fall variant |

Other synthetic indices exist in Deriv's public catalogue (jump, range break, step, combined,
drift-switch). EAGLE-X lists them only if the live `active_symbols` feed returns them active.

### Selector behavior
- symbol → subscription to that index's tick stream
- selector disabled when the symbol is unavailable to the connected account

### Unknown / Black box
- Per-account availability grids are not public → read live
- Broker-specific descriptions beyond the above are UNKNOWN