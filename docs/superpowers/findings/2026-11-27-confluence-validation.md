# Confluence strategy: validation record

Every run below is BTC/USDT unless named otherwise, Binance spot, commission
10 bps per side, slippage 5% of ATR. Trial counts are cumulative and never
reset.

## Result table

| Run | Timeframe | Span | Trials | Pooled OOS net | OOS trades | Deflated Sharpe |
|---|---|---|---|---|---|---|
| Sweep, 1000 bars | 1h | 41 days | 24 | n/a (in-sample only) | 443 signals | 0.059 |
| Sweep, 6000 bars | 1h | 249 days | 18 | n/a (in-sample only) | 36-261 | 0.045 |
| Walk-forward, rolling | 1h | 249 days | 126 | -7,884 | 30 | 0.002 |
| Walk-forward, anchored | 1h | 249 days | 126 | -16,090 | 36 | n/a |
| Walk-forward | 4h | 999 days | 126 | +25,183 | 86 | 0.052 |
| Walk-forward | 1d | 9.3 years | 216 | +18,079 | 58 | 0.015 |
| Reduced grid, BTC | 4h | 999 days | 405 | +6,739 | 104 | 0.003 |
| Reduced grid, ETH | 4h | 999 days | 405 | +30,201 | 69 | 0.396 |
| Reduced grid, SOL | 4h | 999 days | 405 | +11,493 | 61 | 0.157 |

Nothing in this table clears the 0.95 deflated-Sharpe threshold. Nothing here
is enabled.

## What the hourly runs got wrong

Three rounds of testing on 1h concluded the strategy was dead. On 4h and 1d it
is profitable out of sample. The strategy was written for swing trading and 1h
was the wrong instrument to judge it with: every training Sharpe across all
seven 1h folds and all 18 grid points was negative, so the selection procedure
never had a profitable candidate to choose between and was picking the least-bad
loser each fold.

Mean degradation on 4h is -0.131. The test windows outperformed the training
windows they were selected on, which is the opposite of the overfitting
signature.

## The candle filter, and why the finding was weaker than its vote

`use_candle=False` won 6 of 7 folds on 4h and 11 of 12 on 1d, across
non-overlapping windows in different years. That looked like the one parameter
walk-forward had actually resolved, and it matched the original observation that
`candle_up` was true on only 4.7% of bars: the filter was removing most entries
rather than selecting better ones.

Fixing it to False and re-running 4h on BTC produced **+6,739 instead of
+25,183**, a 73% drop. The full-grid figure included one fold that had selected
`use_candle=True` and returned +8,651, and the fold-majority vote discarded it.
A parameter that wins most folds is not therefore the parameter that maximises
the pooled result, and treating a majority vote as a settled finding cost most
of the edge on the symbol it was derived from.

The reduced grid did raise BTC's out-of-sample trade count from 86 to 104 and
resolved both remaining axes, so the run is not worthless. But the headline
number went the wrong way, and the honest reading is that the candle finding is
a weak preference rather than a resolved parameter.

## Cross-symbol

ETH and SOL were never used to select anything. Both are profitable pooled:
+30,201 over 69 trades and +11,493 over 61. ETH chose `tp1_r_multiple=3.0` in
7 of 7 folds, SOL in 6 of 7, BTC in 3 of 7. Wide targets are the most consistent
preference across symbols.

Two caveats the fold tables carry. SOL's per-fold hit rate is 0.00 across its
three scored folds while its pooled net is positive, so the profit came from
folds too thin to score. ETH's three most recent folds are likewise unscored.
Pooled figures are the only ones with a sample behind them on both.

## Why the threshold is now out of reach

The trial count only grows: 24, then 18, then 126, then 216, then 405. Deflation
divides credit by the number of parameter sets tried, so each additional round of
tuning on this strategy makes a deployable verdict strictly harder to reach,
regardless of what the next round finds. ETH's 0.396 is the best figure produced
so far and it was obtained at 405 trials.

Further parameter work on this strategy cannot produce a passing result. Two
honest paths remain:

1. A genuinely different strategy, which starts its own trial budget at zero.
2. Forward testing on data that does not exist yet. Paper trading generates
   out-of-sample returns with no selection applied to them at all, so its trial
   count is 1 and the deflation penalty is the smallest it can be.

The second is the only route that validates *this* strategy, and it needs the
paper engine rather than more research tooling.

## Engine bug found during this work

An intent arriving before ATR was seeded multiplied NaN into the entry fill and
from there into equity, which never recovers. The risk engine then refused every
later intent for a non-numeric account, so a 20-intent run reported one trade and
nineteen refusals whose stated reason pointed at the account rather than the
unseeded indicator. Unreachable through the confluence strategy, whose 200-EMA
gate cannot fire inside the first 14 bars; reachable on every walk-forward fold.
Fixed: the engine refuses that one bar and names the cause.

## Still unverified

- ATR has no end-to-end external verification (see `tools/README.md`).
- Every number here is Binance crypto spot. Forex, US, and NSE adapters do not
  exist, so the degradation contract has never been exercised by a market that
  genuinely lacks a capability.
- `atr_stop_mult` is unresolved on 4h BTC and ETH, sitting near the 1/3 that
  three values would produce by chance.
