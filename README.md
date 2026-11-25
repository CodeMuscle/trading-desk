# trading-desk

A self-hosted, multi-market trading desk built under one hard constraint: **no paid
data subscriptions**. Four markets (crypto, forex, US, NSE/BSE), one Python metrics
engine as the single source of truth, and a chart that renders what the engine computed
rather than computing its own.

Private repository. Contains personal risk parameters and original strategy work.

## The idea the whole thing is built around

Four markets have four different free-data ceilings. Crypto gives a full order book;
Indian brokers give no aggressor-tagged ticks at all. A system that papers over that
difference produces numbers that look identical and mean different things.

So every metric returns exactly one of three states:

1. a value,
2. `Unavailable` with a machine-readable reason,
3. an error.

Never `NaN` standing in for "not supported here", and never a silent fallback to a worse
proxy. Cumulative Volume Delta on an NSE adapter returns
`Unavailable("adapter has no aggressor-tagged ticks")`. It does not return a CVD
inferred from candle direction, which is a fabrication.

A visible gap is safer than a confident fiction.

## Layout

```
docs/superpowers/specs/     the five design specs
docs/superpowers/plans/     implementation plans, plus a student edition
design/tokens.css           the Slate and Ember design system
reference/pine/             original TradingView strategies, the seed corpus
src/desk/                   the Python engine (arrives with M1)
web/                        the chart shell (arrives with M1)
```

## Specs

| Spec | Scope |
|---|---|
| 1 | Metrics spine: adapter interface, crypto adapter, 123 core metrics, risk engine, validation harness |
| 2 | Desk platform: chart, in-browser indicator authoring, strategy templates, paper engine, accounts, chatbot |
| 3 | Research, journal and review: parameter optimizer, backtest surface, behavioural analytics, agentic review |
| 4 | External data: US, forex and NSE adapters, fundamental and macro ingestion |
| 5 | The agent desk |

## Rules that are not negotiable

- **Risk is deterministic code, never an agent prompt.** A limit a model can reason
  around is not a limit.
- **The validation harness exists before any signal.** Nothing untested has a path to an
  order.
- **The optimizer reports its trial count to the validator on every run**, cumulative
  across re-runs. Deflated Sharpe is meaningless without N, and sweeping parameters
  while forgetting how many you tried is how false discoveries are manufactured.
- **Nothing auto-promotes.** An AI-authored strategy, or one found by a sweep, lands in a
  state with no order path and requires a human action to enable. A process that both
  generates and approves its own candidates has no gate at all.
- **The API binds `127.0.0.1` only.** A later milestone POSTs indicator source from a
  browser editor into a local Python process; that path must never be network-reachable.
- **Never forward-fill price.** Gaps stay gaps. Invented candles produce real backtest
  profits.

## Honest ceiling

Real-time consolidated US quotes, extended-hours data and full tick history are **data
licensing**, not features. They cost money regardless of who writes the interface.
Footprint, delta and VPIN stay crypto-only for the same reason. The project is built to
know the difference rather than paper over it.

## Status

Pre-implementation. Specs and the M1 plan are written; no engine code exists yet.
M1 ends with candles and EMA overlays on screen and no order path in existence.
