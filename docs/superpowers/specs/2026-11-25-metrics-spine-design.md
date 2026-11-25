# Metrics Spine Design

**Date:** 25 November 2026 (revised same day)
**Status:** approved design, pre-implementation
**Spec:** 1 of 5. Companions: `2026-11-25-desk-platform-design.md` (Spec 2),
`2026-11-25-research-journal-review-design.md` (Spec 3).

## Purpose

The computation core of the trading desk: a Python metrics engine, a market-agnostic
data adapter interface, one working adapter (crypto via ccxt), and the core-tier metrics
from the L0 to L10 analytical inventory, implemented in bands with the risk engine and
validation harness ahead of any order path.

This spec owns every number. Spec 2 owns every surface that displays one. No chart, no
agents, no broker execution here.

## Revision note

This replaces the morning version, which was written before four things were known: the
user trades **forex** as well as crypto, US and NSE; in-browser indicator authoring is a
requirement; paper trading and multi-account workspaces are in scope; and the user's own
Pine scripts exist and settle several metric definitions that would otherwise have been
guessed. The band structure and the market list both changed as a result.

## Decisions ledger

Reversibility is recorded because several of these were decided without deep domain
familiarity. Cheap items change later at no penalty; expensive items get scrutiny before
implementation.

| Decision | Choice | Reversibility |
|---|---|---|
| Chart source | Own it. OpenAlgo Charts, self-hosted. `thefreechart.com` rejected (below) | Moderate. Spec 2 only; a renderer swap leaves the engine untouched |
| Compute location | One Python engine is the single source of truth; the chart renders precomputed series | **Expensive**. Reversing means reimplementing the library in TypeScript |
| In-browser authoring | Monaco in the browser, hot reload in Python. Not WASM | Cheap. Transport detail, same engine |
| Adapter interface shape | Market-agnostic, capability-declaring; NSE and forex constraints encoded from day one | **Expensive**. Everything downstream assumes it |
| Markets | Four: crypto, forex, US, NSE | Cheap. Additive adapters |
| Market order | Crypto, then US, then forex, then NSE | Cheap |
| Paper trading | One local simulator, uniform across all four markets | Moderate. Broker paper APIs become cross-checks |
| Metric scope | All core-tier entries, plus supply/demand zones, imbalance, BOS, value area, Fibonacci | Cheap. Bands are additive |
| Phasing | Risk band first | Cheap |
| Fundamentals source | SEC EDGAR XBRL for US, NSE/BSE filings for India, no paid keys | Cheap |
| Repo and tooling | `~/trading-desk`, Python with `uv` | Cheap |

### Why `thefreechart.com` was rejected as an integration target

Investigated 25 Nov 2026. Do not re-evaluate.

- Backend is Frappe; app name `trading_replay`; data host `marketlab.cloud`. No public
  or documented API; `frappe.auth.get_logged_user` returns
  `PermissionError: not whitelisted`.
- Cloudflare WAF fronts `/api/data/*` and returns an interstitial, not JSON.
- The bundle's licence header forbids the intended use verbatim: *"unauthorized copying,
  extraction, reverse engineering, or redistribution of the first-party charting,
  drawing, and replay modules is strictly prohibited."*
- Ships `disable-devtool` plus a server call `trading_replay.api.report_devtool_debugger`
  and an error type `DevtoolBanned` carrying `devtool_banned_until_ms`. Inspection is
  reported and timeboxes an account ban.
- Sends `automation_bits` and `deep_gate_challenge` to a fingerprinting service with a
  persistent `visitor_id`. Headless and CDP drivers are a detected signal.

Its engine is KLineChart (Apache-2.0) and its custom indicators are plain JavaScript
compiled client-side in Monaco. Nothing proprietary there is worth acquiring. Its design
*craft* informed Spec 2's token set; its palette and stylesheet were not taken.

### The Python-core trade-off, and why in-browser authoring survives it

Indicators are authored in Python, where the tests are. The browser supplies the editor
and the feedback loop; the engine supplies execution. A parameter change pushes over an
open localhost WebSocket and only the changed series returns.

Three things remove the latency that normally motivates client-side compute:

1. **Warm engine.** The store holds the OHLCV frame in memory per (symbol, timeframe).
   Recomputing a metric over a few thousand bars in numpy is sub-millisecond; the frame
   load was the slow part and already happened.
2. **Socket, not request/response.** Round trip is 1 to 2ms for a parameter change, and
   roughly 100ms for a full module reload after an edit. Both are below the threshold
   that interrupts authoring.
3. **Params cache** keyed on `(symbol, timeframe, metric, params_hash)`.

Running Python in the browser via WASM was considered and rejected: it would cost
TA-Lib's C extension, numba, and vectorbt, and would introduce a second execution
environment to keep in sync.

## Markets and their free-data ceilings

| Market | Data | Paper orders | Notes |
|---|---|---|---|
| Crypto | ccxt | local simulator | Only market where full L3 orderbook and real footprint are free |
| Forex | **OANDA v20 practice API** (`api-fxpractice.oanda.com`) | yes, free | Free with a demo account: real-time ticks, history to 2005, order placement. Not to be confused with OANDA's separately-branded Exchange Rates API, which is a paid product with a 7-day trial |
| US | Alpaca free tier (IEX) | yes, free | IEX-only, so intraday and microstructure sit on an unrepresentative tape. Fine for swing and daily |
| NSE/BSE | Angel One SmartAPI / Fyers v3 | **none** | Indian brokers offer no paper order API. This is what forces a local simulator |

The user trades mostly swing, which is why the US IEX limitation is tolerable and NSE
does not need to jump the queue.

**NSE lands last but shapes the interface first.** The adapter interface assumes from the
outset that a market may have no aggressor-tagged ticks, a session grid anchored to a
fixed open, an expiring-contract symbol master, and exchange-published positioning data
with no equivalent elsewhere. Without that, the interface silently becomes ccxt-shaped
and NSE arrives as a pile of special cases.

## Metric definitions taken from the user's own Pine scripts

`~/Downloads/Forex_Confluence_AutoTrade_Pro.pine` and `..._Strategy.pine` are the
authoritative source for the definitions below. These were going to be guessed at from
six different literatures; they no longer need to be.

| Metric | The user's definition, to be matched exactly |
|---|---|
| Swing pivot | `ta.pivothigh(high, 10, 10)` / `ta.pivotlow(low, 10, 10)`. N bars left and N right, N default 10 |
| Break of structure | `close > lastSwingHigh` (up) / `close < lastSwingLow` (down). Close beyond the prior swing, not wick |
| Bullish engulfing | `close > open and close[1] < open[1] and close > open[1] and open < close[1]` |
| Bearish engulfing | `close < open and close[1] > open[1] and close < open[1] and open > close[1]` |
| Hammer | `lowerWick > body * 2 and upperWick < body * 0.6 and close > open` |
| Shooting star | `upperWick > body * 2 and lowerWick < body * 0.6 and close < open` |
| Trend state | `ema(20) > ema(50)` up, `<` down, with `ema(200)` as a separate bias filter |
| Momentum state | `rsi(14) > 50 and macd_hist > 0` up, inverted for down |
| Confluence gate | All of trend, momentum, bias, breakout and candle must agree. Not a score |
| Signal cooldown | Minimum N bars between signals, default 5 |
| Initial stop | `1.5 x atr(14)` from entry |
| Partial booking | 50% of position at `1.0R`, where R is entry minus initial stop |
| Post-TP1 stop | Move to breakeven (entry price) |
| Runner trail | `2.0 x atr(14)` once TP1 is booked |
| Simple-exit variant | Fixed `1.5x ATR` stop, `3.0x ATR` target, no partial |

**Bulkowski's role.** The candle patterns above are the user's definitions and are
implemented as written. *Encyclopedia of Candlestick Charts* supplies measured base
rates, which are attached to each pattern as metadata rather than used to redefine it.
A pattern that tests badly is implemented and labelled with its real hit rate, not
silently dropped or silently treated as equal to the others.

### Vocabulary: supply/demand rather than ICT

The user asked for order blocks and FVG but does not use ICT. Those two names are ICT
vocabulary for price behaviour that predates ICT by decades, so:

- **Supply/demand zone** is the primary construct, in the Wyckoff and Seiden lineage
  (the zone is drawn from the base that caused an impulsive departure). "Order block"
  is an alias for the same object, not a separate construct.
- **Imbalance** is the primary name for the three-candle gap. "FVG" is an alias.
- No ICT-specific rule sets: no killzones, no power-of-three, no silver bullet.

Villahermosa's *The Wyckoff Methodology in Depth* and Weis's *Trades About to Happen*
are the reference texts; Dalton's *Mind Over Markets* for value area and auction theory;
Shannon's *Maximum Trading Gains with Anchored VWAP* for anchored VWAP.

### Metrics accepted, and two declined

In scope because the user named them: supply/demand zones, price action, candlestick
patterns, EMAs, VWAP, imbalance, Fibonacci. Added on recommendation and accepted: swing
pivots (the foundation both zones and BOS are defined on), BOS, value area.

**Declined for now:** VCP, which is Minervini's specific setup and only coherent inside
his whole system; and CPR, an Indian-retail pivot variant that overlaps what VWAP bands
already provide. Both are cheap to add later if wanted.

Fibonacci is included because it was asked for, with a note that the user's own
inventory tiers it "thin".

## Two defects found in the user's Pine scripts

Both will carry into the port unless fixed, and both are flagged rather than silently
corrected.

1. **Position size is decoupled from risk.** Both scripts use
   `default_qty_type=strategy.percent_of_equity` with `default_qty_value=100` and emit
   `"qty_pct":100`. With `pyramiding=0` this is one position at a time rather than
   stacked leverage, but a wide ATR stop and a tight one receive identical capital, so
   realised risk per trade swings with volatility. The port sizes by risk per trade
   using L9 fixed-fractional and ATR-based sizing.
2. **TP1 can be marked on the entry bar.** `tp1Done := true` fires when
   `high >= tp1Level`, which on the entry bar uses that same bar's high. Pine evaluates
   on bar close, so this is an optimistic intrabar fill assumption and it inflates
   backtest results. The port requires TP1 to be confirmed on a bar strictly after
   entry.

Defect 2 is precisely the bug class the no-lookahead property test below exists to
catch, found in real code before a line of the engine was written.

## Implementation bands

Core-tier counts per layer in the inventory: L0 10, L1 25, L2 11, L3 2, L4 21, L5 15,
L6 8, L7 4, L8 12, L9 16, L10 10, for a total of 134.

Two accounting facts have to be stated before the bands make sense:

- **L2's 11 core entries are not all price-derived.** Eight are (RVOL, volume MA,
  climax/dry-up, OBV, A/D line, Chaikin Money Flow, Volume Profile/VPOC, value
  area/VAH/VAL). Three need external data (OI change matrix, Put-Call Ratio, FII/DII
  cash flows) and belong with B3.
- **Six of L8's 12 core entries duplicate L10**: purged K-fold CV with embargo, deflated
  Sharpe, probability of backtest overfitting, minimum backtest length, parameter
  sensitivity surface, and Monte Carlo trade reshuffling appear in both layers. They are
  implemented once, in B1, and counted once.

| Band | Contents | Core count | Market data |
|---|---|---|---|
| **B1** | L9 risk (16) + L10 validation (10), absorbing the 6 duplicated L8 entries | 26 | None. Synthetic trade sequences |
| **B2** | L0 (10), L1 (25), L2 price-derived (8), L4 (21), plus L8 stationarity testing (1) | 65 | Crypto adapter only |
| **B3** | L5 (15), L6 (8), L7 (4), L2 external (3), L3 depth (2) | 32 | Per-source; lands as each source lands |
| *deferred to Spec 4* | L8 modelling: gradient boosting, feature engineering from L0 to L7, mean-decrease-accuracy importance, ensembling, LLM-as-analyst | 5 | Needs B2 metrics as features |

Reconciliation: 26 + 65 + 32 = 123 in this spec, plus 5 deferred to Spec 4, plus the 6
L8/L10 duplicates counted once in B1, totalling 134.

By nature of work rather than by layer: 65 are pure functions over a dataframe, 32 are
ingestion adapters rather than formulas, 26 operate on returns and trades, 5 are
modelling and belong with the agents.

Two consequences read directly off the inventory:

- **L3 microstructure has only 2 core entries** (bid-ask spread, 5-level quoted depth).
  Footprint, delta, Lee-Ready, VPIN and absorption all sit at depth or paid tier.
  Microstructure is a crypto-only bonus, not a spine requirement.
- **L6 macro core is almost entirely Indian** (India 10Y G-Sec, USDINR, CPI/WPI/IIP/PMI,
  policy stance), so the macro layer ships with the NSE adapter rather than out of
  sequence here.

Roughly 40 of B2's 65 metrics have TA-Lib or pandas-ta reference implementations. The
remaining ~25 (supply/demand zones, swing pivots, BOS, imbalance, volume profile and
VPOC, value area, initial balance, anchored VWAP, the candle patterns) are
self-contained, and the table above now fixes their definitions.

B1 is deliberately first **and** needs no market data, so it is complete and fully
tested before a single candle is fetched.

## Modules

Dependency arrows point one way only: `adapters -> store -> metrics -> risk ->
validation`, with `api` hanging off the side. Nothing imports backwards.

### `adapters/`
One interface, N implementations. Returns normalized OHLCV frames and, where available,
depth and reference data. Each adapter declares its capabilities: `has_aggressor_ticks`,
`has_l2_depth`, `has_fundamentals`, `session_grid`, `symbol_master_expires`,
`has_broker_paper_api`.

### `metrics/`
Pure functions: frame in, series out. No I/O, no network, no state. One module per
layer, L0 through L8. The only place a metric is defined.

### `risk/`
Deterministic sizing (fixed fractional, ATR-based), portfolio heat cap, correlated
exposure cap, stops (structure, ATR, time, trailing), partial scale-out, daily loss
limit with kill switch, R-multiple and expectancy tracking, the full Indian cost stack,
and a slippage model. Plain Python. No LLM participates in any risk decision, because a
limit a model can reason around is not a limit.

### `validation/`
In-sample/out-of-sample split, walk-forward, purged K-fold CV with embargo, deflated
Sharpe, probability of backtest overfitting, minimum backtest length, parameter plateau
detection, Monte Carlo trade reshuffling, cost-and-slippage inclusion, benchmark
comparison, forward paper test harness. Consumes `metrics/` and `risk/`. Nothing
consumes it.

### `store/`
Local parquet cache keyed `(venue, symbol, timeframe)`, the warm in-memory frame
registry the chart reads from, and the params cache. The single place that knows about
staleness.

### `api/`
Localhost FastAPI with a WebSocket channel. Serves series and risk state; accepts
indicator-module reloads from the Monaco editor. Binds `127.0.0.1` only, asserted in a
test. No auth, no users, no deployment.

### `templates/`
Strategy templates as Python files with declared parameter schemas, so one object drives
the chart overlay, the backtest, and the paper engine. Seeded from the user's two Pine
scripts. Each template carries a validation record and a state: `draft` (no order path),
`validated`, or `failed` (kept visible with its PBO and deflated Sharpe shown).

## Risk limits (supplied 25 Nov 2026)

These are the numbers B1 was blocked on. All are deterministic code in `risk/`, never
agent-adjustable.

| Limit | Value | Mechanism |
|---|---|---|
| Risk per trade | 1% of account equity | Fixed fractional, ATR-based stop distance (1.5x ATR per the user's Pine scripts) |
| **Portfolio heat cap** | **6%** total open risk | Refuses a new entry that would push summed open risk above 6%. Six concurrent positions at 1% |
| **Correlated-exposure cap** | **2.5%** per correlated cluster | Clusters computed from the L0 average-pairwise-correlation matrix, not assumed by asset class. Four dollar-correlated FX positions are one bet, not four |
| **Daily loss limit** | **3%** realised | Kill switch. Halts new entries for the session. Open positions keep their stops |
| **Monthly loss limit** | **10%** soft, **15%** hard | At -10% risk per trade halves; at -15% new entries stop until the month rolls |

### Why heat is 6% and not 3%

Heat above the daily limit means a simultaneous stop-out can breach the daily limit in
one move. Setting heat equal to the daily limit (3%) makes that structurally impossible
but allows only three concurrent positions, which is tight across four markets.

6% accepts the theoretical breach and defends against the realistic version of it with
the correlated-exposure cap, since simultaneous stop-outs are overwhelmingly a
correlation event rather than a coincidence. The daily limit remains the binding
constraint in normal operation; heat is the structural ceiling.

The arithmetic is self-consistent: 3% daily across roughly four bad days reaches the 15%
monthly hard stop, so the two limits bind at a similar horizon rather than one making
the other decorative.

### Tests these limits require

- Heat cap: the entry that would take summed open risk to 6.01% is refused; 6.00% is
  accepted. Boundary, not approximate.
- Correlated cluster: four positions whose pairwise correlation exceeds the cluster
  threshold are summed as one cluster, and the fifth is refused.
- Daily loss limit: fires at exactly -3.00% realised, halts new entries, and does **not**
  liquidate open positions.
- Monthly: at -10.00% the computed per-trade risk is exactly 0.5%; at -15.00% entries
  return zero size.
- Fail-closed: if any limit cannot be computed, size is zero.

## The degradation contract

Four markets have genuinely different data ceilings. The failure mode that kills trading
systems is not a crash; it is a number that looks correct and is not. So capability
mismatches are made loud by construction.

Each adapter declares what it has. Each metric declares what it needs. Every metric
result is exactly one of three states:

1. a value,
2. `Unavailable` with a machine-readable reason,
3. an error.

Never `NaN` standing in for "not supported here", and never a silent fallback to a worse
proxy. Cumulative Volume Delta on an NSE adapter returns
`Unavailable("adapter has no aggressor-tagged ticks")`. It does not return a CVD
inferred from candle direction, which is a fabrication.

This is also what makes B3's 32 source-dependent metrics safe to ship incrementally: an
un-wired source reports `Unavailable`, which is a true statement, rather than leaving a
hole that reads as zero. Spec 2 gives all three states a distinct visual treatment.

## Error handling

1. **Staleness is a state, not a warning.** Every frame carries `as_of`. A metric over
   stale data is labelled stale and propagates the label.
2. **Never forward-fill price.** Gaps stay gaps. A filled gap is an invented candle, and
   invented candles produce real backtest profits.
3. **Lookahead is guarded mechanically.** See testing.
4. **Risk fails closed.** If risk state cannot be computed for any reason, position size
   is zero. Not last-known-good, not a default percentage. Zero.

## Testing

| Target | Method |
|---|---|
| ~40 B2 metrics with reference implementations | Golden-value agreement with TA-Lib / pandas-ta |
| ~25 B2 metrics without references | Hand-built fixtures containing a known zone, a known BOS, a known imbalance, a known value area, and one of each candle pattern per the definitions table |
| The two Pine ports | Agreement with the originals on the same OHLCV, **after** correcting the entry-bar TP1 defect. A disagreement that traces to the defect is the expected and correct outcome |
| All 123 metrics in this spec | **No-lookahead property test**: recompute on truncated inputs and assert the prefix is unchanged, so `output[t]` provably depends only on `input[:t+1]` |
| B1 risk engine | Synthetic trade sequences with known expectancy. No market data required |
| Kill switch | Boundary test: fires at the limit, not near it |
| Validation harness | **Self-test with a deliberately overfit strategy**: assert PBO flags it and deflated Sharpe lands at or below zero |
| Adapter capability flags | A metric requiring an absent capability returns `Unavailable`, never a value |
| API binding | Assert the server binds `127.0.0.1` and not `0.0.0.0` |

The no-lookahead property test is the highest-value item. Lookahead is the most common
and most expensive bug class in this domain, one mechanical test catches it in metrics
nobody thought carefully about, and it has already proven itself by catching the
entry-bar TP1 defect in the user's existing code.

The validation-harness self-test matters for the same reason: a harness that passes
everything is worse than no harness, and feeding it known garbage is the only way to
learn that before trusting it.

## Out of scope for this spec

- Any chart, UI, editor, chatbot or account surface (Spec 2)
- Forex, US and NSE adapters, and all L5/L6/L7 ingestion (Spec 3)
- Agents, LLM calls, news reading, bull-versus-bear debate (Spec 4)
- Broker execution of any kind, including paper. No brokerage key, funded or otherwise
- Edge, depth, thin and paid tier metrics. Interfaces are left so adding them is
  additive, not a refactor

## Prerequisites

- The 23 agiprolabs computation skills are not installed yet. B2 begins by installing
  them and security-reading each `SKILL.md` before enabling: outbound calls to
  undeclared domains, reads of `~/.ssh`, `~/.aws` or `.env`, and prompt-injection text.
  Stars are not review.
- `marketcalls/openalgo-indicator-skills` has no LICENSE file and is stale since June
  2026. Read as reference; do not depend on it.

## Open and provisional

Decided without deep domain familiarity and expected to be revisited when the code
reaches them. Re-raise at that point rather than on a timer.

- Whether the Python-core / thin-renderer split holds once the chart is real. Expensive
  to reverse, so it gets scrutiny before Spec 2 begins.
- Whether the adapter interface genuinely accommodates NSE, verified against Angel One
  and Fyers docs before B2 locks the interface. Expensive to reverse.
- Whether the ~25 reference-free definitions not covered by the Pine scripts (zones,
  value area, anchored VWAP, volume profile) match the user's intent. The reading list
  he supplied is the source; where a definition still varies, ask rather than pick.
