# Desk Platform Design

**Date:** 25 November 2026
**Status:** approved design, pre-implementation
**Spec:** 2 of 4 (see `2026-11-25-metrics-spine-design.md` for Spec 1)

## Purpose

The surfaces a human touches: the chart desk, the in-browser indicator authoring loop,
the strategy template library, the local paper-trading engine, per-account workspaces,
and the in-app agentic chatbot.

Spec 1 owns every number. This spec owns everything that displays a number, and owns
nothing that computes one.

## Design system

Tokens live in `design/tokens.css`. Theme name: **Slate and Ember**.

### Where the direction came from, and what was deliberately not taken

The brief was "as clean and detailed as TradingView, with the design system and
spiciness of thefreechart.com". Their stylesheet was read and their token set extracted
to ground the direction in fact rather than impression. What was taken is craft that
nobody owns: a blue-black ground rather than pure black, warm off-white ink rather than
pure white, a four-step alpha border ladder instead of drop shadows, a 10 to 22px
density scale with a 13px base, small radii, a 1.75 icon stroke, and 120 to 150ms
transitions.

What was **not** taken: their palette, their stylesheet, and their brand. Their bundle
carries an explicit proprietary notice, and a design system extracted from a third
party's live site is not ours to lock into a portable file. Slate and Ember is its own
palette built on the same craft principles.

### The one correction made to their approach

In their tokens, `--brand-accent` and `--bv-pos` are both `#10b981`. The UI accent
colour and "price is up" are the same green, so a selected control and a rising candle
are chromatically identical. On a dense chart that is a real ambiguity.

Slate and Ember separates them:

- **UI accent is warm ember** (`--accent: #D98A3D`). Selection, focus, active tab,
  primary action. It never appears in a candle, a metric value, or a P&L figure.
- **Data semantics are reserved** (`--pos: #2FBF84`, `--neg: #E8524F`,
  `--caution`, `--info`, `--annotate`). These never appear on chrome.

A reviewer can therefore tell at a glance whether a green pixel means "the market went
up" or "you have this selected". That distinction is load-bearing on a trading screen.

### Order-entry buttons are desaturated on purpose

`--buy-bg: #1E8A63` is a muted version of `--pos`, and `--sell-bg: #B8413F` a muted
version of `--neg`. The chart's greens and reds stay the loudest thing on screen; the
buttons are legible without competing with the data. This idea is theirs and it is a
good one.

### Staleness has a visual vocabulary

Spec 1's degradation contract returns three states per metric: a value, `Unavailable`,
or an error. The UI needs to render all three distinctly, so the tokens include
`--stale`, `--stale-hatch` (a 45-degree hairline hatch), and `--unavailable-fg`.

A stale value renders in `--stale` with the hatch behind it. An `Unavailable` metric
renders its reason as dimmed text, not a zero and not a blank. This is the single most
important rendering rule in the spec: a fabricated number is worse than a visible gap.

### Typography

A 2+1 pairing: **Geist** for display (titles, account names, the live price), **Inter**
for UI and body, **JetBrains Mono** for every number.

Inter is conventionally flagged as a generic choice. That guidance is about marketing
pages. At 10 and 11px in a data table, Inter's hinting is the correct engineering
decision and a more characterful face would cost legibility. Geist carries the character
at display sizes where legibility is not at risk.

Every numeric cell gets `font-variant-numeric: tabular-nums`. Price columns that do not
align are misread.

### Component discipline

Every interactive control ships all eight states: default, hover, `:focus-visible`,
`:active`, disabled, loading, error, success. The focus ring is 2px of `--accent` and is
never animated. Motion animates `transform` and `opacity` only, and
`prefers-reduced-motion` collapses everything to 1ms.

## Chart desk

**Engine:** OpenAlgo Charts (`marketcalls/openalgo-charts`, Apache-2.0, TypeScript).
Chosen in Spec 1's decisions ledger. 112 indicators, 90 drawing tools, replay, Market
Profile, order entry on chart, multi-chart sync, and an NSE session grid anchored to the
09:15 open.

**Layout:** a 52px left tool rail, a 42px top bar carrying symbol / timeframe / account
selector, the chart canvas, and a right panel that is tabbed rather than stacked
(metrics, positions, journal, chatbot). Floating panels over the canvas use
`--chrome-bg` at 85% opacity with a 20px blur so candles stay partly visible behind
them.

**Ownership boundary, restated from Spec 1:** the chart owns look-at-it state only.
Drawings, layout, crosshair, theme. It computes nothing that can produce a signal. Its
112 built-in indicators remain available as an explicitly labelled scratchpad, rendered
in `--ink-3` with a "not the engine" marker so they can never be mistaken for
authoritative series.

## In-browser indicator authoring

Monaco editor in the browser; **execution stays in Python**.

1. You type an indicator in Monaco.
2. The file POSTs to the local engine over the existing WebSocket.
3. The engine re-imports the module and pushes the recomputed series back to the chart.

Round trip is roughly 100ms against thefreechart's 38ms, which is imperceptible while
authoring. The decisive advantage: the file you edit is literally the file the backtest
imports, so there is no divergence surface at all, and the full Python ecosystem
(TA-Lib's C extension, numba, vectorbt) stays available. Running Python in the browser
via WASM would cost all three.

**Security, non-negotiable:** this creates a path from a browser editor to arbitrary
execution in a local Python process. The API binds to `127.0.0.1` only, never
`0.0.0.0`, and there is no CORS wildcard. Single user, own machine, own code. The
binding is asserted in a test so a later refactor cannot quietly widen it.

## Strategy templates

Two Pine strategies supplied by the user are the seed corpus:
`Forex_Confluence_AutoTrade_Pro.pine` and `Forex_Confluence_AutoTrade_Strategy.pine`.
They are ported to Python as the first two templates, and their logic is the
authoritative definition for several metrics in Spec 1.

### The template library

Templates are Python files with a declared parameter schema, so the same object drives
three surfaces: the chart overlay, the backtest, and the paper engine. Each ships with
its parameter defaults, its entry and exit rules, and its own validation record.

Seed set, in build order:

1. **Confluence gate** (ported from the user's Pro script), EMA trend, RSI and MACD
   momentum, swing breakout, candle confirmation, ATR risk with partial booking.
2. **Confluence gate, simple exits** (ported from the Strategy script), same entry,
   fixed 1.5x ATR stop and 3.0x ATR target.
3. **Supply and demand zone reaction**, zone drawn from the base before an impulsive
   departure, entry on first return.
4. **Imbalance fill**, three-candle gap, entry on partial fill.
5. **Anchored VWAP reversion**, Shannon's method, anchored to a user-chosen event.
6. **Value area edge**, Dalton's auction framing, entry at VAH/VAL rejection.

Every template must pass the Spec 1 M3 validation gate before it is allowed an order
path. A template that fails walk-forward stays in the library, visibly marked failed,
with its PBO and deflated Sharpe shown. Keeping failures visible is the point: a library
where everything passes is a library that is not testing anything.

### AI-authored and AI-customised templates

Three levels, increasing in autonomy:

- **Customise:** you pick a template and describe a change in words. The agent edits the
  parameter schema or the rule body, and the diff is shown before it is applied.
- **Author:** you describe a strategy and the agent writes a new template from scratch
  against the template interface.
- **Explain:** the agent reads an existing template and tells you what it actually does,
  which is the one that will get the most use.

**Hard gate on all three:** an AI-authored or AI-modified template is created in a
`draft` state that has **no order path at all**, not even paper. It must pass the same
walk-forward, purged CV, deflated Sharpe and PBO checks as a hand-written one before it
can be promoted. The agent can write a strategy; it cannot certify one. This follows
directly from the settled rule that a limit an LLM can reason around is not a limit.

## The in-app chatbot

A corner launcher opening a panel in the right tab group.

### Model routing

- **Default:** drive Claude Code headlessly, or the Claude Agent SDK
  (`claude-agent-sdk`), so calls run under the existing Claude subscription rather than
  metered API credits. Subscription rate limits apply. This is correct for a single-user
  desk and must be revisited if the desk ever serves anyone else.
- **Metered fallback:** the Anthropic API directly, `claude-opus-5` for analysis and
  `claude-haiku-4-5` for routing and short answers.
- **Local fallback:** Ollama, for offline use. Expected to be materially worse at
  chart reasoning, so it is a fallback and not a peer.

Cost control: prompt-cache the stable prefix (metric catalogue, the user's own
definitions, system prompt), use `low` effort for lookups and `high` for critique, and
send the underlying numbers rather than a chart screenshot unless the question is
genuinely visual. Note the minimum cacheable prefix differs by model (512 tokens on
Opus 5, 4096 on Haiku 4.5), so a short prefix silently will not cache on the cheap
model.

### Tools it gets

Read-only: current chart context (symbol, timeframe, visible range), any metric from the
engine, the validation verdict for any template, the trade journal, open paper
positions, and the template source.

### Tools it does not get

No tool places an order, alters a risk parameter, promotes a draft template, or
overrides the kill switch. These are not prompt instructions; the tools do not exist.

### Suggestion pills

On open, the panel shows pills rather than an empty input, because an empty box invites
nothing. Pills are **context-derived, not a fixed list**, they are generated from what
is currently on screen, which is what makes them useful rather than decorative.

Generation rules, in priority order:

1. **If a position is open:** "How is this trade doing against its plan?", "Where is my
   stop relative to structure?"
2. **If a template is active on the chart:** "Why no signal here?", "Which condition is
   failing right now?", "Show me this template's walk-forward result."
3. **If a metric on screen is `Unavailable`:** "Why is CVD unavailable on this symbol?"
   This turns the degradation contract into a teaching surface.
4. **If a drawing was just placed:** "Is this zone valid by my own rules?"
5. **Always available, as the last two pills:** "Explain this chart to me", "Critique my
   analysis."

Maximum five pills, single line, no horizontal scroll. Pills are `--surface-2` with a
`--line-2` hairline, `--radius-pill`, `--fs-dense`, and they use `--accent` on hover
only. They are buttons, so they carry all eight states, and their labels never wrap to
two lines.

## Local paper engine

One simulator, uniform across all four markets.

This is forced rather than chosen: Indian brokers do not offer paper order APIs, so NSE
requires a local simulator regardless. Given that, running crypto, forex and US through
the same simulator is strictly better than four different fill behaviours, because it
yields one slippage model, one cost model, one journal, and numbers that are comparable
across markets. Spec 1's L9 band already specifies the slippage model and the full
Indian cost stack.

Broker paper APIs (OANDA v20 practice for forex, Alpaca paper for US) become an optional
later cross-check to validate the simulator's fills, not the mechanism.

**Sizing correction, carried over from the user's Pine scripts.** Both scripts use
`default_qty_type=strategy.percent_of_equity` with `default_qty_value=100`, and emit
`"qty_pct":100` in their webhook payload. With `pyramiding=0` that is one position at a
time rather than stacked leverage, but it decouples position size from risk: a wide ATR
stop and a tight one receive identical capital, so realised risk per trade swings with
volatility. The paper engine sizes by **risk per trade** via L9 fixed-fractional and
ATR-based sizing instead. Same strategy logic, different and more stable exposure.

## Accounts and workspaces

Three to five local accounts. An account is: name, starting capital, open positions,
closed-trade journal, saved chart layouts, watchlists, and enabled templates.

Local SQLite. No auth, no login, no users table beyond the account list, because it is a
single-user application on one machine. Switching accounts swaps layout, watchlists,
positions and journal together, so a crypto account and an NSE account do not bleed into
each other.

## MCP server

The metrics engine exposes an MCP server over **local stdio** for Claude Code and Claude
Desktop, giving terminal access to the same metrics the chatbot sees.

**Important correction to an earlier assumption:** Anthropic's MCP *connector* for the
Messages API fetches from a publicly reachable URL on Anthropic's side. It cannot reach
`127.0.0.1`, and exposing the engine publicly to satisfy it would contradict the
security decision above. So the in-app chatbot uses **direct tool use** (the SDK's Tool
Runner) against in-process Python functions, and MCP-over-stdio serves Claude Code only.
One engine, two front doors, neither open to the internet.

## TradingView parity

Free by construction, because there is no paywall to enforce: unlimited indicators per
chart, unlimited saved layouts, multi-chart layouts with sync, bar replay including
intraday, Volume Profile, unlimited local alerts, custom intervals, data export, and
multiple watchlists per account.

**The honest ceiling.** What stays out of reach is data licensing rather than features:
real-time consolidated US quotes, extended-hours data, and full tick history cost money
regardless of who writes the interface. This is unchanged from the settled free-data
constraints.

## Milestones

Reordered from Spec 1's original sequence so something visual arrives early. The rule
that permits this: risk-before-**order**, not risk-before-chart. Looking at candles
cannot lose money.

| Milestone | Contents | What you see |
|---|---|---|
| **M1** | Adapter interface, crypto adapter, store, localhost API, chart shell, 8 metrics (EMA 9/21/50/200, session VWAP, candlestick patterns, swing pivots) | **Candles and your EMAs on screen.** No order path exists |
| **M2** | Monaco authoring loop, supply/demand zones, imbalance, BOS, Fibonacci, value area, first two Pine ports | You write an indicator in the browser and watch it bind |
| **M3** | L9 risk engine + L10 validation harness | Nothing visual. **The gate: no order path opens until this passes** |
| **M4** | Local paper engine, accounts and workspaces, template library with validation records | Paper trades, per account |
| **M5** | Chatbot, suggestion pills, MCP server, AI template authoring in `draft` state | Ask the chart questions |
| **M6+** | Forex (OANDA v20 practice), US (Alpaca), NSE (Angel One / Fyers), L5/L6/L7 ingestion, then the agent desk | Breadth |

## Out of scope for this spec

- Every metric definition and computation (Spec 1)
- External market data beyond the crypto adapter (Spec 3)
- The six-role agent desk and bull-versus-bear debate (Spec 4)
- Any live-money order path, at any milestone

## Open and provisional

- Whether the right panel should be tabbed or splittable. Tabbed is specified because
  stacking panels in a 1440px-wide window starves the chart, but this is a preference to
  revisit once M1 is on screen.
- Whether Geist earns its place over using Inter at every size. Decide by looking at M1.
- The portfolio heat cap and daily loss limit are still unknown. The user's Pine scripts
  supply per-trade risk (1.5x ATR stop, 1R partial at 50%, breakeven move, 2x ATR trail)
  but say nothing about portfolio-level limits, which M3 needs.
