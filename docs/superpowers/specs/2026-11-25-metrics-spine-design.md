# Metrics Spine Design

**Date:** 25 November 2026
**Status:** approved design, pre-implementation
**Spec:** 1 of 4

## Purpose

Build the computation core of the agentic trading desk: a Python metrics engine with a market-agnostic
data adapter interface, one working adapter (crypto via ccxt), and 123 of the 134 core-tier metrics from
the L0–L10 analytical inventory (the remaining 11 are 6 cross-layer duplicates and 5 modelling entries
that belong with the agents), implemented in three bands with the risk engine and validation harness first.

This spec deliberately contains no chart, no agents, and no broker execution. Those are Specs 2–4.

## Why the risk engine is inside this spec

The settled build order is risk-before-signal, so nothing untested gets a path to an order. An earlier
sequencing put the risk engine after the chart. That was wrong, because L9 risk and L10 validation
*are* metrics: they operate on returns and trades instead of on price. They belong in the metrics
engine, as its first band.

This also makes the ordering buildable: Band 1 requires no market data at all, so it is complete and
fully tested before a single candle is fetched.

## Spec sequence (context)

| Spec | Contents |
|---|---|
| **1 (this)** | Metrics spine: adapter interface, crypto adapter, 123 core metrics, risk engine, validation harness |
| 2 | Chart desk: OpenAlgo Charts + warm-engine WebSocket |
| 3 | External data: US (Alpaca) and NSE (Angel One / Fyers) adapters, L5/L6/L7 ingestion |
| 4 | Agent desk: Scout / News / Chart / bull-vs-bear researchers / Sizer / Executor |

## Decisions ledger

Reversibility is stated because several of these were decided without deep domain familiarity. Cheap
items can be changed later at no penalty. Expensive items get scrutiny before implementation begins.

| Decision | Choice | Reversibility |
|---|---|---|
| Chart source | Own it, OpenAlgo Charts, self-hosted. `thefreechart.com` rejected (see below) | Moderate. Spec 2 only; renderer swap leaves the engine untouched |
| Compute location | One Python engine is the single source of truth; chart renders precomputed series | **Expensive**. Reversing means reimplementing the metric library in TypeScript |
| Adapter interface shape | Market-agnostic, capability-declaring; NSE constraints encoded from day one | **Expensive**. Everything downstream assumes it |
| Metric scope | All core-tier entries (123 after dedup and deferral), plus order blocks, FVG, liquidity sweeps, Market Profile | Cheap. Bands are additive |
| Phasing | Three bands, risk band first | Cheap. Reordering costs nothing |
| First market | Crypto (ccxt), then US (Alpaca), then NSE | Cheap. One adapter among three by design |
| Fundamentals source | SEC EDGAR XBRL for US; NSE/BSE published filings for India; no paid keys | Cheap. Ingestion module, swappable source |
| Repo and tooling | `~/trading-desk`, Python with `uv` | Cheap |

### Why `thefreechart.com` was rejected as an integration target

Investigated 25 Nov 2026. Do not re-evaluate.

- Backend is Frappe; app name `trading_replay`; data host `marketlab.cloud`. No public or documented API.
  `frappe.auth.get_logged_user` returns `PermissionError: not whitelisted`.
- Cloudflare WAF fronts `/api/data/*` and returns an interstitial, not JSON.
- Bundle license header forbids exactly the intended use: *"unauthorized copying, extraction, reverse
  engineering, or redistribution of the first-party charting, drawing, and replay modules is strictly
  prohibited."*
- Ships `disable-devtool` client-side plus a server call `trading_replay.api.report_devtool_debugger`,
  and defines an error type `DevtoolBanned` with `devtool_banned_until_ms`. Inspection is reported and
  timeboxes an account ban.
- Sends `automation_bits` and `deep_gate_challenge` to a fingerprinting service with a persistent
  `visitor_id`. Headless and CDP drivers are a detected signal.

Its chart engine is KLineChart (Apache-2.0) and its "custom indicators" are plain JavaScript compiled
client-side in a Monaco editor. There is nothing proprietary there worth acquiring.

### Accepted trade-off of the Python-core decision

New indicators are authored in Python, where the tests are. There is no in-browser authoring loop. This
is a deliberate exchange of interactive authoring for single-source-of-truth between the chart, the
backtest, and the agents.

The latency that normally motivates client-side compute is removed rather than traded away:

1. **Warm engine.** The store holds the OHLCV frame in memory per (symbol, timeframe). Recomputing a
   metric over a few thousand bars in numpy is sub-millisecond; the frame load was the slow part and has
   already happened.
2. **Localhost WebSocket, not request/response.** A parameter change pushes `{metric, params}` and
   receives only the changed series. Round trip is ~1–2 ms, below human perception.
3. **Params cache**, keyed on `(symbol, timeframe, metric, params_hash)`. Revisiting a tried value is free.

Ownership rule: the Python engine owns anything that can produce a signal or enter a backtest. The chart
owns only look-at-it state (drawings, layout, crosshair). The chart's own built-in indicators remain
available as an explicitly labelled, non-authoritative scratchpad.

## Implementation bands

Core-tier counts per layer in the inventory are: L0 10, L1 25, L2 11, L3 2, L4 21, L5 15, L6 8, L7 4,
L8 12, L9 16, L10 10, for a total of 134.

Two accounting facts have to be stated before the bands make sense:

- **L2's 11 core entries are not all price-derived.** Eight are (RVOL, volume MA, climax/dry-up, OBV,
  A/D line, Chaikin Money Flow, Volume Profile/VPOC, value area/VAH/VAL). Three need external data
  (OI change matrix, Put-Call Ratio, FII/DII cash flows) and belong with B3.
- **Six of L8's 12 core entries duplicate L10**: purged K-fold CV with embargo, deflated Sharpe,
  probability of backtest overfitting, minimum backtest length, parameter sensitivity surface, and
  Monte Carlo trade reshuffling appear in both layers. They are implemented once, in B1, and counted once.

| Band | Contents | Core count | Market data |
|---|---|---|---|
| **B1** | L9 risk (16) + L10 validation (10), absorbing the 6 duplicated L8 entries | 26 | None. Synthetic trade sequences |
| **B2** | L0 (10), L1 (25), L2 price-derived (8), L4 (21), plus L8 stationarity testing (1) | 65 | Crypto adapter only |
| **B3** | L5 (15), L6 (8), L7 (4), L2 external (3), L3 depth (2) | 32 | Per-source; lands as each source lands |
| *deferred to Spec 4* | L8 modelling: gradient boosting, feature engineering from L0–L7, mean-decrease-accuracy importance, ensembling/stacking, LLM-as-analyst | 5 | Needs B2 metrics as features |

Reconciliation: 26 + 65 + 32 = 123 in this spec, plus 5 deferred to Spec 4, plus the 6 L8/L10 duplicates
counted once in B1 = 134.

Split by nature of work rather than by layer: 65 are pure functions over a dataframe, 32 are ingestion
adapters rather than formulas, 26 operate on returns and trades, 5 are modelling and belong with the agents.

Two consequences read off the inventory:

- **L3 microstructure has only 2 core entries** (bid-ask spread, 5-level quoted depth). Footprint, delta,
  Lee-Ready, VPIN and absorption all sit at depth or paid tier. Microstructure is therefore a crypto-only
  bonus, not a spine requirement.
- **L6 macro core is almost entirely Indian** (India 10Y G-Sec, USDINR, CPI/WPI/IIP/PMI, policy stance), so
  the macro layer ships with the NSE adapter in Spec 3 rather than out of sequence here.

Roughly 40 of B2's 65 metrics have TA-Lib or pandas-ta reference implementations. The remaining ~25 (VCP,
swing pivot detection, BOS, CHoCH, FVG, volume profile and VPOC, value area, initial balance, CPR,
order blocks, liquidity sweeps) are self-contained implementations with no reference to agree with.

## Modules

Dependency arrows point one way only: `adapters → store → metrics → risk → validation`, with `api`
hanging off the side. Nothing imports backwards.

### `adapters/`
One interface, N implementations. Returns normalized OHLCV frames and, where available, depth and
reference data. Each adapter declares its capabilities: `has_aggressor_ticks`, `has_l2_depth`,
`has_fundamentals`, `session_grid`, `symbol_master_expires`.

NSE lands third but shapes the interface now, so it does not become ccxt-shaped. The interface assumes
from the outset that a market may have no aggressor-tagged ticks, a session grid anchored to a fixed
open, an expiring-contract symbol master, and exchange-published positioning data with no equivalent
elsewhere.

### `metrics/`
Pure functions: frame in, series out. No I/O, no network, no state. One module per layer, L0 through L8.
The only place in the system where a metric is defined.

### `risk/`
Deterministic sizing (fixed fractional, ATR-based), portfolio heat cap, correlated-exposure cap, stops
(structure, ATR, time, trailing), partial scale-out, daily loss limit with kill switch, R-multiple and
expectancy tracking, the full Indian cost stack, and a slippage model. Plain Python. No LLM participates
in any risk decision, because a limit a model can reason around is not a limit.

### `validation/`
In-sample/out-of-sample split, walk-forward analysis, purged K-fold CV with embargo, deflated Sharpe,
probability of backtest overfitting, minimum backtest length, parameter plateau detection, Monte Carlo
trade reshuffling, cost-and-slippage inclusion, benchmark comparison, forward paper test harness.
Consumes `metrics/` and `risk/`. Nothing consumes it.

### `store/`
Local parquet cache keyed `(venue, symbol, timeframe)`, plus the warm in-memory frame registry the chart
later reads from, plus the params cache. The single place that knows about staleness.

### `api/`
Localhost FastAPI with a WebSocket channel. Read-only: serves series and risk state. Carries no logic.
Exists so Spec 2 has a connection point. No auth, no users, no deployment, it is a cable, not a service.

## The degradation contract

Three markets have genuinely different data ceilings. The failure mode that kills trading systems is not
a crash. It is a number that looks correct and is not. So capability mismatches are made loud by
construction.

Each adapter declares what it has. Each metric declares what it needs. A metric asked to run on an
adapter that cannot feed it returns `Unavailable(reason)`.

Every metric result is exactly one of three states:

1. a value,
2. `Unavailable` with a machine-readable reason,
3. an error.

Never `NaN` as a stand-in for "not supported here", and never a silent fallback to a worse proxy. Example:
Cumulative Volume Delta on an NSE adapter returns `Unavailable("adapter has no aggressor-tagged ticks")`.
It does not return a CVD inferred from candle direction, which is a fabrication.

This is also what makes the 32 source-dependent B3 metrics safe to ship incrementally. An un-wired source
reports `Unavailable`, which is a true statement, rather than leaving a hole that reads as zero.

## Error handling

1. **Staleness is a state, not a warning.** Every frame carries `as_of`. A metric computed over stale data
   is labelled stale and propagates the label to its consumers.
2. **Never forward-fill price.** Gaps stay gaps. A filled gap is an invented candle, and invented candles
   produce real backtest profits.
3. **Lookahead is guarded mechanically.** See testing below.
4. **Risk fails closed.** If risk state cannot be computed for any reason, position size is zero. Not
   "last known good", not a default percentage. Zero.

## Testing

| Target | Method |
|---|---|
| ~40 B2 metrics with reference implementations | Golden-value agreement with TA-Lib / pandas-ta |
| ~25 B2 metrics without references | Hand-built fixtures: a synthetic frame containing a known VCP, a known BOS, a known FVG, a known value area |
| All 123 metrics in this spec | **No-lookahead property test**: recompute on truncated inputs and assert the prefix is unchanged, so `output[t]` provably depends only on `input[:t+1]` |
| B1 risk engine | Synthetic trade sequences with known expectancy, no market data required |
| Kill switch | Boundary test: fires at the limit, not near it |
| Validation harness | **Self-test with a deliberately overfit strategy**: assert PBO flags it and deflated Sharpe lands at or below zero |
| Adapter capability flags | A metric requiring an absent capability returns `Unavailable`, never a value |

The no-lookahead property test is the highest-value item here. Lookahead is the most common and most
expensive bug class in this domain, and a single mechanical test catches it in metrics nobody thought
carefully about.

The validation-harness self-test matters for the same reason: a harness that passes everything is worse
than no harness, and feeding it known garbage is the only way to learn that before trusting it.

## Out of scope for this spec

- Any chart or UI (Spec 2)
- US and NSE adapters, and all L5/L6/L7 ingestion (Spec 3)
- Agents, LLM calls, news reading, bull-vs-bear debate (Spec 4)
- Broker execution of any kind, including paper. No brokerage key, funded or otherwise, is used.
- Edge, depth, thin and paid tier metrics. Interfaces are left so adding them is additive, not a refactor.

## Prerequisites

- The 23 agiprolabs computation skills are not yet installed. B2 begins by installing them and
  security-reading each `SKILL.md` before enabling: outbound calls to undeclared domains, reads of
  `~/.ssh`, `~/.aws` or `.env`, and prompt-injection text. Stars are not review.
- `marketcalls/openalgo-indicator-skills` has no LICENSE file and is stale since June 2026. Read as
  reference; do not depend on it.

## Open and provisional

These were decided without deep domain familiarity and are expected to be revisited when the code reaches
them. Re-raise at that point rather than on a timer:

- Whether the Python-core / thin-renderer split still holds once the chart is real (expensive to reverse,
  so it gets scrutiny before Spec 2 begins, not after).
- Whether the adapter interface genuinely accommodates NSE, verified against Angel One and Fyers docs
  before B2 locks the interface (expensive to reverse).
- Whether the ~25 reference-free metric definitions match the user's intent, since definitions of VCP,
  order blocks and value area vary between sources.
