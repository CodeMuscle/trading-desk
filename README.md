<div align="center">

# trading-desk

**A multi-market trading desk that refuses its own results.**

Zero paid data subscriptions · one Python metrics engine as the single source of truth ·
a validation harness that has rejected every strategy it has been given, including the
author's

[![tests](https://img.shields.io/badge/tests-343%20passing%20%7C%2012%20skipped-2FBF84?style=flat-square)](tests/)
[![python](https://img.shields.io/badge/python-3.13-5B9BD5?style=flat-square)](pyproject.toml)
[![engine](https://img.shields.io/badge/engine-pandas%203.1%20%2F%20numpy%202.5-5B9BD5?style=flat-square)](pyproject.toml)
[![strategies enabled](https://img.shields.io/badge/strategies%20enabled-0-E8524F?style=flat-square)](#the-scoreboard)
[![status](https://img.shields.io/badge/status-research%20harness-E0A830?style=flat-square)](#status)

</div>

---

## The point

Most trading software is built to make a strategy look good. The hard part is not
computing an indicator; it is not fooling yourself. So this desk is built inside out:
the risk engine and the validation harness were written **before the first signal
existed**, and nothing untested has a path to an order.

The harness works. It has refused every strategy put through it, including the two Pine
scripts that motivated the project. That is the feature.

```
VERDICT: out-of-sample deflated Sharpe 0.40 below 0.95 at 405 trials:
         profitable out of sample, with tp1_r_multiple resolved,
         but not distinguishable from luck
```

A desk that cannot say that sentence is a desk that will eventually hand you a number
you believe.

---

## The three ideas it is built on

### 1. Degradation is a type, not a footnote

Four markets have four different free-data ceilings. Crypto gives a full order book;
Indian brokers give no aggressor-tagged ticks at all. Software that papers over that
difference produces numbers that look identical and mean different things.

Every metric returns exactly one of three states, and there is no fourth:

```python
ok(value)                       # a number, and a real one
unavailable("reason")           # this venue cannot answer this question
failed(exception)               # something broke, and here is what
```

Never `NaN` standing in for "not supported here". Never a silent fallback to a worse
proxy. Cumulative Volume Delta on an NSE adapter returns
`Unavailable("adapter has no aggressor-tagged ticks")` — not a CVD inferred from candle
direction, which is a fabrication with a plausible shape.

`ok(nan)` raises. A visible gap is safer than a confident fiction.

### 2. One simulator, so forward results are comparable to backtests

A backtest Sharpe is deflated by every parameter set ever tried. Forward paper returns
carry no selection at all, so their trial count is one, which makes paper trading the
only remaining way to validate a strategy the research tooling has already exhausted.

That argument requires paper and backtest numbers to be produced by the same
arithmetic. Two separate implementations are comparable right up until one of them is
edited. So there is one `Simulator`, and both drive it:

```
                  ┌──────────────────┐
   DataFrame ────▶ │                  │
                  │   Simulator      │──▶ Fill · refusal · equity
   live bars ────▶ │   .on_bar()      │
                  └──────────────────┘
                     ▲            ▲
              CostModel       RiskLimits
          (always applied)  (fail-closed)
```

There is no gross-returns view anywhere in the package. A gross equity curve is not an
optimistic estimate of a strategy; it is a different and false one.

### 3. The trial count is cumulative and cannot be reset

Sweeping parameters and keeping the best is, mechanically, the procedure that
manufactures false discoveries. The control is plumbing rather than restraint: every
sweep and every walk-forward hands its trial count to the statistics, `prior_trials`
carries the count from earlier runs that informed the current grid, and the deflation
divides by the total.

Narrowing a grid after reading the output is a decision taken with knowledge of the
test. Re-running a 9-point grid and reporting 63 trials, having reached that grid by
reading 342, understates the tax fivefold — and it is the one way a harness like this
gets defeated from the inside.

---

## Architecture

```
            MARKET ADAPTERS            declare capabilities, never assume them
    ┌───────────┬───────────┬───────────┬───────────┐
    │  crypto   │  forex    │    US     │  NSE/BSE  │
    │  (ccxt)   │ (planned) │ (planned) │ (planned) │
    └─────┬─────┴───────────┴───────────┴───────────┘
          │  OHLCVFrame + frozenset[Capability] + as_of
          ▼
    ┌─────────────────────────────────────────────────┐
    │  METRICS ENGINE          single source of truth │
    │  trend · momentum · profile · price action ·    │
    │  structure              → MetricResult always   │
    └─────┬───────────────────────────────────────────┘
          │
          ├──▶ RISK ENGINE ──────── fail-closed sizing, checked before orders
          │                         not before charts
          ├──▶ SIMULATOR ────────── backtest and paper, one code path
          ├──▶ VALIDATION ───────── deflated Sharpe · PBO · walk-forward ·
          │                         minimum backtest length
          └──▶ localhost API ────── 127.0.0.1 only, never 0.0.0.0
                    │
                    ▼
            CHART DESK (TypeScript) renders what the engine computed;
                                    computes nothing that can reach an order
```

The chart is a renderer. Anything that can produce a signal or enter a backtest lives in
Python, because two implementations of an indicator is two indicators.

---

## The scoreboard

Every run that has ever been executed, with its verdict. Nothing here is enabled.

| Run | Timeframe | Span | Trials | Out-of-sample net | Deflated Sharpe |
|---|---|---|---|---|---|
| Sweep | 1h | 41 days | 24 | in-sample only | 0.059 |
| Sweep | 1h | 249 days | 18 | in-sample only | 0.045 |
| Walk-forward, rolling | 1h | 249 days | 126 | −7,884 | 0.002 |
| Walk-forward, anchored | 1h | 249 days | 126 | −16,090 | n/a |
| Walk-forward | 4h | 999 days | 126 | **+25,183** | 0.052 |
| Walk-forward | 1d | 9.3 years | 216 | **+18,079** | 0.015 |
| Reduced grid, BTC | 4h | 999 days | 405 | +6,739 | 0.003 |
| Reduced grid, ETH | 4h | 999 days | 405 | **+30,201** | 0.396 |
| Reduced grid, SOL | 4h | 999 days | 405 | +11,493 | 0.157 |

Threshold is 0.95. Nothing clears it. Three symbols are profitable out of sample and
none of them is distinguishable from luck at the trial count it took to find.

Full record with the reasoning: [`docs/superpowers/findings/`](docs/superpowers/findings/)

---

## Bugs this harness found in itself

Each of these produced a plausible number before it was caught, which is the point of
writing them down.

| Bug | Symptom | Why it mattered |
|---|---|---|
| Wilder seeding | RSI off by 6.83 points | `ewm(adjust=False)` seeds on the first observation, not an SMA; and bar 0's `diff()` NaN was filled with a fabricated 0 |
| Unseeded ATR | one trade, nineteen refusals | NaN entered equity and never left; the refusal blamed the account, not the indicator |
| Gap fills | stops filled at the stop price | a bar opening beyond a stop fills at the open; free money on every gapped exit |
| `variance_of_sharpes` default | planted real edge scored 0.00 | annualised-vs-per-trade unit mismatch rejected everything equally |
| Cluster default `"uncorrelated"` | all positions in one cluster | summed supposedly-uncorrelated risk into a single cap |
| `itertools.product()` empty | empty grid became 1 trial | a sweep reporting a trial count for work it never did |
| Cross-check false pass | compared 0 pairs, printed AGREE | the tool now refuses to report agreement below 50% comparable |
| Tuple-only stability | one resolved axis read as none | conflated "nothing holds" with "one parameter is decided" |
| Launderable trial count | a narrowed grid reported 63 of 405 trials | deflation would improve because the count shrank, not the strategy |
| Forming-bar processing | a provisional low locked in a stop permanently | idempotency then skips the corrected bar, so a transient wrong answer becomes final |
| First-run backfill | 17 paper trades at a 70.6% win rate | a replay of an already-selected strategy, written into the journal as forward evidence |
| Lag on backfilled bars | a two-month backfill read as a two-month-late poller | a column that cannot tell "late" from "backfilled" reads as a permanently broken desk |
| Cache never checked for age | chart served two-day-old candles under a live header | the engine marked all 18 metrics stale; the UI drew that as a dimmer colour |
| `scalars` shape unhandled in UI | `value_area` rendered as `-` | identical to what *unavailable* renders, in a project whose premise is that the difference is visible |
| `dict_of_series` rendered lengths | `macd:300 signal:300 hist:300` | three identical bar counts wearing the names of three different numbers |
| Zone tested by its own departure | every zone tested, `fresh` always 0 | price moves continuously, so the bar leaving a shelf still touches it |
| Unbounded zone departure window | a zone rescored by every future bar | same base scored differently each week; unusable in a backtest |
| Over-asking defeated the cache | metrics panel took 67s to fill | `len(cached) >= limit` is false forever when limit exceeds the listing, so every metric refetched a decade |
| Negative `since` on a full fetch | venue rejected the request outright | asking for more bars than exist counted backwards past the epoch |
| Tail applied before the NaN filter | sparse metrics showed `–` | trimming to the last *bar* returns nothing on a series that is mostly empty |
| Venue errors surfaced as bare 500s | `Internal Server Error`, no body | an unlisted symbol is the caller's mistake (404) and an outage is upstream (502) |

---

## Rules that are not negotiable

- **Risk is deterministic code, never an agent prompt.** A limit a model can reason
  around is not a limit. The tools that place orders, alter risk parameters, promote a
  draft, or override the kill switch do not exist — this is not a system-prompt
  instruction.
- **The validation harness exists before any signal.** Nothing untested has a path to
  an order.
- **Nothing auto-promotes.** An AI-authored strategy, or one found by a sweep, lands in
  a state with no order path. A process that both generates and approves its own
  candidates has no gate at all.
- **The API binds `127.0.0.1` only.** A later milestone POSTs indicator source from a
  browser editor into a local Python process; that path must never be network-reachable.
  No CORS wildcard.
- **A limit a restart clears is not a limit.** The daily and monthly loss baselines are
  persisted with the account, so restarting the process after a bad morning does not
  hand back the allowance. Same rule as keeping risk out of agent prompts, applied to
  the process lifecycle.
- **Never forward-fill price.** Gaps stay gaps. Invented candles produce real backtest
  profits.
- **No gross-returns view exists.** Costs are applied always, with no flag to disable
  them outside tests.
- **Paper keys only.** Never a funded brokerage key.

---

## Layout

```
src/desk/
  result.py           the three-state return type everything uses
  capabilities.py     what a venue can answer, declared not assumed
  adapters/           market adapters; crypto (ccxt) is live
  metrics/            trend · momentum · profile · price action · structure ·
                      supply/demand zones · Fibonacci anchored to real swings
  store/              OHLCVFrame, parquet cache, staleness
  risk/               fail-closed sizing and the limit table
  paper/simulator.py  the one bar-stepping simulator
  paper/engine.py     the live driver; same arithmetic, different bar source
  paper/account.py    SQLite accounts, journal and refusal log
  cli.py              on-demand operation; nothing runs in the background
  backtest/           a driver over the simulator, plus the cost model
  research/           sweeps and walk-forward, both with trial accounting
  validation/         deflated Sharpe, PBO, minimum backtest length
  api/                localhost FastAPI

docs/superpowers/specs/      five design specs
docs/superpowers/plans/      implementation plans, plus a student edition
docs/superpowers/findings/   every validation run and its verdict
design/tokens.css            the Slate and Ember design system, 104 tokens
reference/pine/              the original TradingView strategies
tools/                       golden-value cross-check against openalgo-charts
web/                         the chart desk: left rail, chart, metrics, paper panel
```

---

## Quickstart

```bash
uv sync
uv run pytest                               # 343 passing, 12 skipped
uv run python -m desk.api.app               # binds 127.0.0.1:8787
cd web && npm install && npm run dev
```

Run the paper desk. On demand only, so cron and a manual invocation are the same
code path:

```bash
uv run desk create swing --equity 100000
uv run desk advance swing --symbol BTC/USDT --timeframe 4h
uv run desk journal swing
```

The first advance sets a baseline and trades none of the fetched history. That is
deliberate: a paper journal is worth having because forward returns carry no selection,
so their trial count is one, and replaying four hundred bars writes a backtest into the
journal and labels it paper. `--backfill` opts into replay when replay is what you want.

Bar processing is idempotent and the bar still forming is held back, so running this more
often than the timeframe is free and running it late loses nothing. A 4h chart is served
correctly by:

```cron
*/15 * * * * cd ~/trading-desk && uv run desk advance swing >> ~/.trading-desk/log 2>&1
```

Chosen over a daemon because a daemon that dies quietly leaves a gap in the journal that
reads as "no signals", and after the fact that is indistinguishable from a quiet market.
A failed invocation exits non-zero to a scheduler that can be made to shout.

Walk a strategy through the harness:

```python
from desk.adapters.crypto import CryptoAdapter
from desk.backtest.costs import CostModel
from desk.research.walk_forward import walk_forward
from desk.strategy.confluence import run

frame = CryptoAdapter().fetch_frame("BTC/USDT", "4h", 6000)
result = walk_forward(
    frame,
    {"tp1_r_multiple": [1.0, 2.0, 3.0], "atr_stop_mult": [1.5, 2.0, 2.5]},
    run,
    costs=CostModel(),        # there is no way to turn these off
    train_bars=2500,
    test_bars=500,
    prior_trials=342,         # cumulative, and it does not reset
)
print(result.value.verdict)
print(result.value.axis_stability)   # per-parameter, not tuple-only
```

---

## Honest ceiling

Real-time consolidated US quotes, extended-hours data and full tick history are **data
licensing**, not engineering. They cost money regardless of who writes the interface.
Footprint, delta and VPIN stay crypto-only for the same reason.

Known gaps, stated rather than buried:

- ATR has no end-to-end external verification (see [`tools/README.md`](tools/README.md)).
- Every number produced so far is Binance crypto spot. The degradation contract has
  never been exercised by a market that genuinely lacks a capability, because those
  adapters do not exist yet.
- `atr_stop_mult` is unresolved on every timeframe tested, sitting near the 1/3 that
  three values would give by chance.

---

## Status

**Research harness, working. Trading desk, not yet.**

Live: the metrics spine, crypto adapter with pagination past the exchange's 1000-bar
cap, risk engine, one simulator driving both backtest and paper, the paper engine with
SQLite accounts and a journal, parameter sweeps and walk-forward with cumulative trial
accounting, and a localhost API.

The paper engine is verified against the backtest on real data, not only in fixtures:
6000 BTC/USDT 4h bars through twelve overlapping poller calls returned net −18,679.06
against the backtest's −18,679.0633950872, with 263 trades on both sides and 7,500
re-sent bars correctly skipped.

Next: supply/demand zones and Fibonacci, the chart desk, then the US and NSE adapters
that will exercise the degradation contract for the first time.

Private repository. Contains personal risk parameters and original strategy work.
