# Research, Journal and Review Design

**Date:** 25 November 2026
**Status:** approved design, pre-implementation
**Spec:** 3 of 5

Companions: `2026-11-25-metrics-spine-design.md` (Spec 1, every number),
`2026-11-25-desk-platform-design.md` (Spec 2, every surface).
Spec 4 is external data (US, forex, NSE adapters, L5/L6/L7 ingestion).
Spec 5 is the agent desk.

## Purpose

The loop that turns trades into knowledge: a parameter optimizer that generates
candidate strategy variants, a backtest review surface, a trade journal, behavioural
analytics computed from logged facts, and an agentic review layer at three cadences.

## The governing constraint

Generating many strategy variants and keeping the ones that backtested well is
mechanically the procedure that produces false discoveries. With enough trials, the best
result is explained by chance. Every control in this spec exists to price that.

**The coupling that makes it work:** the optimizer reports its **trial count** to the
validation harness on every run, as plumbing rather than discipline. Deflated Sharpe
(Bailey and Lopez de Prado) takes the number of trials as an input. A sweep of 500
combinations scored as if it were one experiment produces a meaningless verdict. There
is no code path that runs a sweep without recording N.

Three further rules follow from the same concern:

1. **Plateau, not peak.** A candidate is scored on the mean performance of its
   *neighbourhood* in parameter space, penalised by the variance across that
   neighbourhood. An isolated spike surrounded by poor results is rejected regardless of
   how good the spike is. L10's parameter-plateau and parameter-sensitivity-surface
   entries are the selection criterion, not a report printed afterwards.
2. **Walk-forward, not full-sample.** Optimise on window W, evaluate on W+1, roll
   forward. The headline number is the aggregate of out-of-sample windows only. A
   full-sample optimised figure is never displayed as a result.
3. **PBO is a verdict, not a footnote.** Probability of backtest overfitting above 0.5
   means the sweep found nothing. It renders at the top of the candidate card, not
   buried in a details pane.

## Module: `research/`

Consumes `metrics/`, `risk/` and `validation/` from Spec 1. Nothing consumes it. It
cannot place an order or enable a template.

### Sweep definition

A sweep targets one template and varies parameters inside that template's declared
schema. Grid search for small spaces, random search for large ones, because random
search covers a high-dimensional space better per trial than a grid.

A sweep declares: the template, the parameter ranges, the market and symbol set, the
walk-forward window geometry, and a hard trial cap. The cap is required. An uncapped
sweep is a sweep whose trial count you will later be tempted to forget.

### What a candidate carries

Every candidate emitted by a sweep is a record, not a number:

| Field | Why it is there |
|---|---|
| Parameter vector | What was tested |
| **Trial count of the parent sweep** | The deflated Sharpe input. Without it nothing else is interpretable |
| Out-of-sample aggregate return, Sharpe, max drawdown | The result, from OOS windows only |
| In-sample / out-of-sample ratio | A ratio far from 1 is the overfitting tell |
| Deflated Sharpe | Sharpe adjusted for trial count, skew, kurtosis and sample length |
| Probability of backtest overfitting | The headline verdict |
| Plateau width | How broad the stable region around this vector is |
| Minimum backtest length check | Whether the sample is even long enough to support the claim |
| Monte Carlo drawdown distribution | What a bad run looks like, not just the realised one |
| Benchmark comparison | Against buy-and-hold on the same symbol and window |
| Costs and slippage applied | Always on. See below |

### Candidate lifecycle

```
sweep  ->  candidate  ->  [validation gate]  ->  shelf  ->  (you enable)  ->  live-paper
                                |
                                +-- fails -> archived, kept visible with its PBO
```

A candidate that passes the gate lands on the **shelf**: validated, and **not enabled**.
Enabling is a manual act. Nothing in this spec auto-promotes a variant into a tradeable
state, for the same reason an AI-authored template cannot self-certify in Spec 2: a
process that both generates and approves its own candidates has no gate at all.

Failed candidates are archived rather than deleted, and remain visible with their PBO
and deflated Sharpe shown. A research shelf showing only successes is a shelf that has
quietly discarded its own evidence.

### Scheduled re-runs

Sweeps can run on a cron cadence (weekly or monthly), since the point is to re-test as
new out-of-sample data arrives. A scheduled re-run of an already-shelved candidate is
the strongest evidence available short of forward paper trading: the window it is being
tested on did not exist when it was selected.

**Trial accounting across runs is cumulative.** Re-running the same sweep weekly for a
year is not 52 independent experiments; it is one long multiple-testing exercise. The
candidate record accumulates total trials across all runs of its parent sweep, and the
deflated Sharpe uses the cumulative figure. This is the single easiest control to get
wrong and the one most likely to flatter a result.

## Backtest review surface

Spec 2 owns the rendering; this spec owns what must be on screen.

**Always visible, never behind a toggle:** equity curve, underwater drawdown plot,
R-multiple histogram, monthly returns heatmap, trade list, and the full validation
record.

**Costs and slippage are always applied. There is no gross-returns view.** A gross
equity curve on a strategy that trades often is not an optimistic estimate; it is a
different and false strategy. Spec 1's L9 band provides the slippage model and the full
Indian cost stack, so the honest number is the only number available.

**Per-trade drill-down:** entry and exit marked on a mini-chart, which template
conditions fired and which did not, maximum adverse and favourable excursion, stop
distance at entry, realised R, costs paid, and slippage against expected.

MAE and MFE matter more than they look: a strategy whose winners routinely go far
against you first has a stop problem, and a strategy whose losers routinely showed good
MFE has an exit problem. Neither is visible in an equity curve.

**Variant comparison:** two to four candidates side by side on identical windows, with
the diff of their parameter vectors highlighted. This is how a plateau becomes legible
rather than theoretical.

## Journal

### Automatically captured

No typing required, because a journal that depends on discipline to populate will have
gaps exactly where they matter most, after the bad trades.

Timestamp, symbol, market, side, size, entry and exit price, stop at entry, target at
entry, realised R-multiple, MAE, MFE, holding duration, template used (or
`discretionary`), which conditions fired, **the L0 regime state at entry**, time of day,
day of week, costs paid, slippage against expected, and the account.

The regime capture is the quietly useful one. "This setup works" and "this setup works
in a trending regime and bleeds in a choppy one" are different claims, and only the
second is actionable. L0 gives regime for free at entry time.

### Manually entered

Thesis before entry, confidence on a 1 to 5 scale, an emotional-state tag, a post-trade
note, and a rule-adherence self-check.

**The pre-trade fields are required before the order submits.** This is the
pre-trade discipline gate, and it is deterministic UI, not advice: no thesis and no
confidence number means no order. *Trading in the Zone* is the reasoning behind it, and
the implementation is a required form field rather than a reminder.

Confidence is the most valuable field on the form and the least obvious. See below.

## Behavioural analytics

Computed from logged facts only. Every metric here is falsifiable from the journal
without asking you how you felt.

| Metric | Definition | What it detects |
|---|---|---|
| **Confidence calibration** | Logged pre-trade confidence (1-5) plotted against realised outcome, as a reliability curve | Whether you know when you have an edge. The single best psychology metric available, because it is the only one that is directly falsifiable |
| Rule adherence rate | Share of trades where every declared condition was satisfied, versus discretionary overrides | Whether your system is your system |
| Override performance | Expectancy of rule-following trades versus override trades | Whether your discretion adds or subtracts. Either answer is useful |
| Revenge-trade flag | Trade opened within N minutes of a realised loss, at size above your median | Tilt, measured rather than guessed |
| Post-streak sizing | Position size as a function of the preceding streak | Size rising after losses is the classic tilt signature; size falling after wins is its mirror |
| Winner/loser asymmetry | Mean R on wins versus mean R on losses, and mean hold time on each | Cutting winners early and holding losers, visible as a time asymmetry even when the R looks acceptable |
| Overtrading | Trades per day against your own baseline, correlated with that day's P&L | Activity mistaken for productivity |
| Time-of-day and day-of-week edge | Expectancy bucketed by session hour and weekday | Often the cheapest real edge in the whole journal |
| Plan deviation | Stop moved against you, or exit before the plan's level | The gap between the trader you designed and the trader you are |
| Drawdown behaviour | Trade frequency and size during your deepest 10% of drawdown | What you do when it hurts, which is the only time it matters |

**Scope limit, stated deliberately.** These measure behaviour, not mental state. The
system can observe that your size rises after losses; it cannot observe why, and it does
not try. Anything resembling a psychological diagnosis is out of scope, including when
the agent layer below could produce one fluently.

## Agentic review

Three cadences, each with a different question.

### Per-trade postmortem, on close

Short. Which conditions fired, what the plan specified, what actually happened, and one
observation. Cites the trade ID and the computed figures. No advice unless the deviation
is factual ("the stop was moved from 1.5 ATR to 0.8 ATR at 14:22").

### Weekly digest

Behavioural statistics for the week, the deviations, and one thing to watch next week.
Compares against your own trailing baseline rather than an external standard, because
the useful comparison is to yourself last month.

### Monthly portfolio review

Expectancy decomposed by template, market and regime. What to retire, what to size up,
what has drifted out of its validated regime. Consumes the research shelf, so a template
whose scheduled re-run has degraded surfaces here.

### Rules the review agent operates under

1. **Every claim cites a trade ID or a computed statistic.** An assertion without
   evidence is not permitted output, however reasonable it sounds.
2. **It may not recommend position size.** Sizing is the risk engine's job and is
   deterministic. An agent suggesting a size is an agent reasoning around a limit.
3. **It may not enable, promote, or disable a template.** It can report that one is
   degrading; the action is yours.
4. **It reports and asks. It does not diagnose.** The honest failure mode here is a
   fluent, confident psychological read with nothing behind it. Constraining the agent
   to evidence is what keeps the feature useful rather than flattering.
5. **Adversarial pairing.** A second agent challenges the first's summary before you see
   it, reusing the bull-researcher-versus-bear-researcher structure already settled for
   Spec 5. One reviewer drifts toward agreeable narrative; two with opposing briefs do
   not.

### What this layer honestly cannot do

An LLM reading your journal can find real patterns in your logged behaviour. It cannot
know your interior state, and it cannot tell you whether a trade will work. The
psychology value here comes from measurement made legible, not from insight generated on
request.

## Reuse: the discipline skills already surveyed

Six of the twelve no-API-key skills from `tradermonty/claude-trading-skills` map directly
onto this spec and should be read before anything here is written from scratch:
`pre-trade-discipline-gate`, `drawdown-circuit-breaker`, `signal-postmortem`,
`weekly-performance-digest`, `trade-performance-coach`, `trader-memory-core`.

Also relevant from the same set: `backtest-expert` and `data-quality-checker`.

The standing security rule applies: read every `SKILL.md` before enabling, checking for
outbound calls to undeclared domains, reads of `~/.ssh`, `~/.aws` or `.env`, and
prompt-injection text. Stars are not review.

`drawdown-circuit-breaker` deserves a specific note: its *logic* is useful as reference,
but the live circuit breaker must be deterministic code in Spec 1's `risk/` module, not
a skill an agent invokes. A kill switch that depends on an agent choosing to call it is
not a kill switch.

## Milestones

Appends to Spec 2's sequence.

| Milestone | Contents |
|---|---|
| **M4** (extended) | Journal data model, automatic capture, the required pre-trade gate |
| **M7** | Backtest review surface with the full validation record and per-trade drill-down |
| **M8** | `research/` optimizer: sweeps, walk-forward, cumulative trial accounting, plateau scoring, the candidate shelf |
| **M9** | Behavioural analytics, confidence calibration curve |
| **M10** | Agentic review at all three cadences, with the adversarial pairing |

M8 depends on M3's validation harness existing and being self-tested against a
deliberately overfit strategy. Building an optimizer before the harness that judges its
output is the wrong order, and tempting, because the optimizer is the fun part.

## Out of scope

- Live-money execution at any milestone
- Automatic promotion of any candidate to a tradeable state
- Psychological diagnosis, counselling, or mental-health inference
- Any agent with authority over size, enablement, or the kill switch

## Open and provisional

- The revenge-trade window N and the "above median size" threshold are guesses until
  there is enough journal data to calibrate them. Start at 30 minutes and 1.25x median,
  then tune against your own history.
- Whether the emotional-state tag is a fixed vocabulary or free text. Fixed is
  analysable, free text is honest. Leaning fixed with an optional note field, revisit
  once the journal has real entries.
- Plateau neighbourhood radius in parameter space is unspecified and needs a sensible
  default per parameter type (integer lengths versus float multipliers behave
  differently).
- Risk limits supplied 25 Nov 2026 and recorded in Spec 1. M3 is unblocked.
