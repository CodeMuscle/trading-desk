# tools

## cross-check.mjs

Compares the Python engine's metric output against `openalgo-charts`, an
independent implementation that ships with the frontend. This is the
golden-value check the M1 plan deferred: it needs no TA-Lib install, and the
reference is a trading library rather than a generic numerics package.

```bash
# 1. dump bars and metrics from ONE frame (they must be the same fetch, or a
#    newly printed bar offsets the two series and every comparison lies)
uv run python tools/dump_reference.py

# 2. compare, from inside web/ so node resolves the package
cd web && node ../tools/cross-check.mjs
```

The comparison refuses to report AGREE when fewer than half the bars were
comparable. An earlier version did not, and reported agreement on a run where
nothing had been compared at all because the library had been handed raw
closes instead of bar objects.

### Findings so far

| Metric | Result |
|---|---|
| EMA(20) | agrees to 7e-11, floating point noise |
| RSI(14) | agrees exactly, after two seeding fixes |
| ATR(14) | **unverified externally.** The library's `atr` export takes a different shape. Its Wilder smoothing is validated transitively through RSI, and `true_range` is unit-tested, but there is no end-to-end check |
