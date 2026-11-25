# M1: Metrics Spine Foundation, Student Edition

> **You write the code. I explain before, review after, and hint when you ask.**
>
> Tests are given to you, because reading good tests is how you learn to write
> them. Implementations are withheld. The full plan
> (`2026-11-25-m1-metrics-spine-foundation.md`) is the answer key: open it after
> your test passes, never before.
>
> From Task 6 onward, try writing the test yourself first from the `Interfaces:`
> block, then compare against the one given here. That is the harder and more
> valuable half of TDD.

**Goal:** Build the capability-declaring adapter interface, a crypto adapter, the frame store, eight hand-verifiable metrics, and a localhost API, then render candles and EMAs in a browser chart.

**Architecture:** A Python package whose dependencies point one way: `adapters -> store -> metrics`, with a thin FastAPI layer bound to loopback serving a TypeScript chart. Every metric returns a three-state result (value, unavailable-with-reason, error) rather than a bare number, so a capability a market lacks surfaces as an explicit gap instead of a fabricated figure.

**Tech Stack:** Python 3.13 (pinned via uv), numpy, pandas, pyarrow, ccxt, FastAPI, uvicorn, pytest, hypothesis. Frontend: TypeScript, Vite, `@openalgo/charts`.

## Global Constraints

Copied verbatim from the specs. Every task's requirements implicitly include this section.

- **The API binds `127.0.0.1` only, never `0.0.0.0`, and there is no CORS wildcard.** Asserted in a test so a later refactor cannot quietly widen it.
- **Every metric returns exactly one of three states:** a value, `Unavailable` with a machine-readable reason, or an error. Never `NaN` standing in for "not supported here", and never a silent fallback to a worse proxy.
- **Never forward-fill price.** Gaps stay gaps. A filled gap is an invented candle, and invented candles produce real backtest profits.
- **Staleness is a state, not a warning.** Every frame carries `as_of`. A metric computed over stale data is labelled stale and propagates the label.
- **Dependencies point one way:** `adapters -> store -> metrics -> risk -> validation`, with `api` hanging off the side. Nothing imports backwards.
- **No order path exists in M1.** No broker key, funded or paper. No `risk/` module yet, so nothing can size a position.
- **Metric definitions are taken verbatim from `reference/pine/Forex_Confluence_AutoTrade_Pro.pine`.** Where this plan states a formula, it matches that file. Do not "improve" a definition.
- **Commit after every task.** Commit messages use Conventional Commits. Never add Claude as co-author; commits are authored solely by CodeMuscle.
- **No em dashes in any generated file content**, including docstrings and comments.

## File Structure

```
~/trading-desk/
  pyproject.toml                   uv project, deps, pytest config
  .python-version                  pinned 3.13
  src/desk/
    __init__.py
    result.py                      MetricResult: the three-state return type
    capabilities.py                Capability flags an adapter declares
    adapters/
      base.py                      MarketAdapter protocol + AdapterInfo
      crypto.py                    ccxt-backed adapter
    store/
      frame.py                     OHLCVFrame: data + as_of + staleness
      cache.py                      parquet cache + warm in-memory registry
    metrics/
      l1_trend.py                  ema, trend_state, session_vwap
      l4_price_action.py           4 candle patterns, swing_pivots
      registry.py                  name -> (fn, required capabilities)
    api/
      app.py                       FastAPI app, loopback-bound
  tests/
    test_result.py
    test_capabilities.py
    test_adapter_contract.py       shared contract every adapter must pass
    test_crypto_adapter.py
    test_frame.py
    test_cache.py
    test_metrics_l1.py
    test_metrics_l4.py
    test_no_lookahead.py           property test over the whole registry
    test_degradation.py            capability mismatch -> Unavailable
    test_api.py
    fixtures.py                    hand-built frames with known features
  web/
    package.json
    index.html
    src/api.ts                     typed client for the localhost API
    src/chart.ts                   OpenAlgo Charts wiring
    src/main.ts                    entry
```

`metrics/registry.py` is the file that makes the degradation contract enforceable rather than aspirational: it is the single place where a metric's required capabilities are declared, so Task 11's test can iterate every metric without a hand-maintained list.

---

### Task 1: Project scaffold and the three-state result type

**Files:**
- Create: `pyproject.toml`, `.python-version`, `src/desk/__init__.py`, `src/desk/result.py`
- Test: `tests/test_result.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MetricResult` (a frozen dataclass union), constructors `ok(value, *, stale=False)`, `unavailable(reason)`, `failed(error)`; properties `.is_ok`, `.is_unavailable`, `.is_error`, `.value`, `.reason`, `.stale`. Every later task returns this type from every metric.

- [ ] **Step 1: Create the uv project and pin Python**

```bash
cd ~/trading-desk
uv init --lib --name desk --python 3.13
uv add numpy pandas pyarrow ccxt "fastapi[standard]" uvicorn
uv add --dev pytest pytest-asyncio hypothesis
rm -rf src/desk/py.typed 2>/dev/null; true
cat .python-version
```

Expected: `.python-version` contains `3.13`. If `uv init` refuses because the directory is non-empty, run `uv init --lib --name desk --python 3.13 --no-workspace` and merge the generated `pyproject.toml` by hand, keeping the existing repo files untouched.

- [ ] **Step 2: Add pytest config to `pyproject.toml`**

Append to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
pythonpath = ["src"]
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_result.py`:

```python
import pytest

from desk.result import MetricResult, failed, ok, unavailable


def test_ok_carries_value_and_is_not_stale_by_default():
    r = ok(42.0)
    assert r.is_ok
    assert not r.is_unavailable
    assert not r.is_error
    assert r.value == 42.0
    assert r.stale is False


def test_ok_can_be_marked_stale_and_keeps_its_value():
    r = ok(42.0, stale=True)
    assert r.is_ok
    assert r.stale is True
    assert r.value == 42.0


def test_unavailable_carries_a_reason_and_has_no_value():
    r = unavailable("adapter has no aggressor-tagged ticks")
    assert r.is_unavailable
    assert not r.is_ok
    assert r.reason == "adapter has no aggressor-tagged ticks"
    with pytest.raises(ValueError, match="has no value"):
        _ = r.value


def test_unavailable_requires_a_nonempty_reason():
    with pytest.raises(ValueError, match="reason"):
        unavailable("")


def test_failed_carries_the_error_and_has_no_value():
    r = failed(ZeroDivisionError("division by zero"))
    assert r.is_error
    assert not r.is_ok
    assert isinstance(r.error, ZeroDivisionError)
    with pytest.raises(ValueError, match="has no value"):
        _ = r.value


def test_result_is_frozen():
    r = ok(1.0)
    with pytest.raises(Exception):
        r.stale = True  # type: ignore[misc]


def test_nan_is_rejected_as_a_value():
    # A NaN standing in for "not supported here" is the exact failure this
    # type exists to prevent.
    import math

    with pytest.raises(ValueError, match="NaN"):
        ok(math.nan)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/test_result.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.result'`

- [ ] **Step 5: Write the implementation**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/result.py`:


- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run pytest tests/test_result.py -v`
Expected: PASS, 7 passed

- [ ] **Step 7: Commit**

```bash
cd ~/trading-desk
git add pyproject.toml .python-version uv.lock src/desk/__init__.py src/desk/result.py tests/test_result.py
git commit -m "feat: three-state MetricResult, the degradation contract's return type"
```

---

### Task 2: Capability flags and the adapter protocol

**Files:**
- Create: `src/desk/capabilities.py`, `src/desk/adapters/__init__.py`, `src/desk/adapters/base.py`
- Test: `tests/test_capabilities.py`, `tests/test_adapter_contract.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `Capability` (str Enum with members `OHLCV`, `AGGRESSOR_TICKS`, `L2_DEPTH`, `FUNDAMENTALS`, `SESSION_GRID`, `EXPIRING_SYMBOLS`, `PARTICIPANT_OI`, `BROKER_PAPER_API`); `AdapterInfo` dataclass with fields `venue: str`, `capabilities: frozenset[Capability]`, `session_open: datetime.time | None`; `MarketAdapter` Protocol with `info() -> AdapterInfo` and `fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pandas.DataFrame`; and `adapter_contract(adapter)` which Task 5 reuses.

The capability set is deliberately written now, while NSE is three milestones away, so the interface cannot quietly become ccrypto-shaped. `AGGRESSOR_TICKS`, `SESSION_GRID`, `EXPIRING_SYMBOLS` and `PARTICIPANT_OI` exist for markets this milestone does not touch.

- [ ] **Step 1: Write the failing capability test**

Create `tests/test_capabilities.py`:

```python
from datetime import time

from desk.capabilities import AdapterInfo, Capability


def test_every_capability_a_later_market_needs_exists_today():
    # NSE lands in a later milestone but shapes the interface now, so these
    # names must exist before any adapter is written.
    names = {c.name for c in Capability}
    assert {
        "OHLCV",
        "AGGRESSOR_TICKS",
        "L2_DEPTH",
        "FUNDAMENTALS",
        "SESSION_GRID",
        "EXPIRING_SYMBOLS",
        "PARTICIPANT_OI",
        "BROKER_PAPER_API",
    } <= names


def test_adapter_info_holds_capabilities_as_an_immutable_set():
    info = AdapterInfo(
        venue="binance",
        capabilities=frozenset({Capability.OHLCV, Capability.L2_DEPTH}),
        session_open=None,
    )
    assert info.has(Capability.OHLCV)
    assert not info.has(Capability.AGGRESSOR_TICKS)
    assert isinstance(info.capabilities, frozenset)


def test_adapter_info_can_declare_a_session_grid_anchor():
    info = AdapterInfo(
        venue="nse",
        capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID}),
        session_open=time(9, 15),
    )
    assert info.session_open == time(9, 15)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.capabilities'`

- [ ] **Step 3: Implement capabilities**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/capabilities.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_capabilities.py -v`
Expected: PASS, 3 passed

- [ ] **Step 5: Write the shared adapter contract**

Create `tests/test_adapter_contract.py`:

```python
"""The contract every adapter must satisfy, in every milestone.

Task 5's crypto adapter imports adapter_contract and runs it. When the forex,
US and NSE adapters land in a later milestone they run the same function, which
is what stops the interface drifting toward whichever adapter was written first.
"""

from __future__ import annotations

import pandas as pd

from desk.capabilities import AdapterInfo, Capability

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def adapter_contract(adapter) -> None:
    info = adapter.info()
    assert isinstance(info, AdapterInfo)
    assert info.venue, "an adapter must name its venue"
    assert Capability.OHLCV in info.capabilities, "every adapter must provide OHLCV"

    frame = adapter.fetch_ohlcv(adapter.probe_symbol, "1h", 50)

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == REQUIRED_COLUMNS, (
        f"columns must be exactly {REQUIRED_COLUMNS}, got {list(frame.columns)}"
    )
    assert isinstance(frame.index, pd.DatetimeIndex), "index must be a DatetimeIndex"
    assert frame.index.tz is not None, "index must be timezone-aware"
    assert str(frame.index.tz) == "UTC", "index must be UTC"
    assert frame.index.is_monotonic_increasing, "index must be sorted ascending"
    assert not frame.index.has_duplicates, "index must have no duplicate timestamps"
    assert len(frame) > 0, "a contract probe must return at least one bar"
    assert not frame[REQUIRED_COLUMNS].isna().any().any(), (
        "an adapter must not emit NaN. A gap stays a gap: drop the bar instead "
        "of forward-filling it."
    )
    assert (frame["high"] >= frame["low"]).all(), "high must be >= low on every bar"
    assert (frame["high"] >= frame[["open", "close"]].max(axis=1)).all()
    assert (frame["low"] <= frame[["open", "close"]].min(axis=1)).all()


class FakeAdapter:
    """A minimal conforming adapter, so the contract is tested without a network."""

    probe_symbol = "FAKE/USD"

    def info(self) -> AdapterInfo:
        return AdapterInfo(venue="fake", capabilities=frozenset({Capability.OHLCV}))

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        idx = pd.date_range("2026-01-01", periods=limit, freq="1h", tz="UTC")
        base = 100.0
        rows = []
        for i in range(limit):
            o = base + i
            c = o + 0.5
            rows.append({"open": o, "high": c + 1, "low": o - 1, "close": c, "volume": 10.0})
        return pd.DataFrame(rows, index=idx)


def test_the_contract_accepts_a_conforming_adapter():
    adapter_contract(FakeAdapter())


def test_the_contract_rejects_a_forward_filled_gap():
    class FillsGaps(FakeAdapter):
        def fetch_ohlcv(self, symbol, timeframe, limit):
            frame = super().fetch_ohlcv(symbol, timeframe, limit)
            frame.iloc[3, frame.columns.get_loc("close")] = float("nan")
            return frame

    try:
        adapter_contract(FillsGaps())
    except AssertionError as exc:
        assert "must not emit NaN" in str(exc)
    else:
        raise AssertionError("the contract should have rejected a NaN close")


def test_the_contract_rejects_a_naive_index():
    class NaiveIndex(FakeAdapter):
        def fetch_ohlcv(self, symbol, timeframe, limit):
            frame = super().fetch_ohlcv(symbol, timeframe, limit)
            frame.index = frame.index.tz_localize(None)
            return frame

    try:
        adapter_contract(NaiveIndex())
    except AssertionError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("the contract should have rejected a naive index")
```

- [ ] **Step 6: Run it to verify it fails**

Run: `uv run pytest tests/test_adapter_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.adapters'`

- [ ] **Step 7: Implement the adapter protocol**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/adapters/__init__.py` (empty file) and `src/desk/adapters/base.py`:


- [ ] **Step 8: Run it to verify it passes**

Run: `uv run pytest tests/test_adapter_contract.py -v`
Expected: PASS, 3 passed

- [ ] **Step 9: Commit**

```bash
cd ~/trading-desk
git add src/desk/capabilities.py src/desk/adapters tests/test_capabilities.py tests/test_adapter_contract.py
git commit -m "feat: capability flags and the shared adapter contract

Capability members for aggressor ticks, session grids, expiring symbol
masters and participant OI exist before any adapter does, so the interface
cannot drift toward whichever market was wired first."
```

---

### Task 3: The OHLCV frame, with staleness as a state

**Files:**
- Create: `src/desk/store/__init__.py`, `src/desk/store/frame.py`
- Test: `tests/test_frame.py`

**Interfaces:**
- Consumes: `Capability`, `AdapterInfo` from Task 2.
- Produces: `OHLCVFrame` frozen dataclass with fields `data: pandas.DataFrame`, `venue: str`, `symbol: str`, `timeframe: str`, `as_of: datetime`, `capabilities: frozenset[Capability]`; methods `is_stale(now, tolerance_multiple=2.0) -> bool`, `timeframe_seconds() -> int`, `has(capability) -> bool`, `tail(n) -> OHLCVFrame`. Tasks 4, 6, 7, 8, 9 and 11 all take `OHLCVFrame`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_frame.py`:

```python
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from desk.capabilities import Capability
from desk.store.frame import OHLCVFrame


def make_frame(bars: int = 10, timeframe: str = "1h") -> OHLCVFrame:
    idx = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(bars)],
            "high": [101.0 + i for i in range(bars)],
            "low": [99.0 + i for i in range(bars)],
            "close": [100.5 + i for i in range(bars)],
            "volume": [10.0] * bars,
        },
        index=idx,
    )
    return OHLCVFrame(
        data=data,
        venue="fake",
        symbol="FAKE/USD",
        timeframe=timeframe,
        as_of=idx[-1].to_pydatetime(),
        capabilities=frozenset({Capability.OHLCV}),
    )


def test_timeframe_seconds_parses_the_common_units():
    assert make_frame(timeframe="1m").timeframe_seconds() == 60
    assert make_frame(timeframe="15m").timeframe_seconds() == 900
    assert make_frame(timeframe="4h").timeframe_seconds() == 14400
    assert make_frame(timeframe="1d").timeframe_seconds() == 86400
    assert make_frame(timeframe="1w").timeframe_seconds() == 604800


def test_an_unparseable_timeframe_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="timeframe"):
        make_frame(timeframe="fortnightly").timeframe_seconds()


def test_a_frame_is_fresh_within_two_bar_intervals():
    f = make_frame(timeframe="1h")
    assert not f.is_stale(now=f.as_of + timedelta(minutes=90))


def test_a_frame_is_stale_beyond_the_tolerance():
    f = make_frame(timeframe="1h")
    assert f.is_stale(now=f.as_of + timedelta(hours=3))


def test_the_staleness_tolerance_is_adjustable():
    f = make_frame(timeframe="1h")
    assert f.is_stale(now=f.as_of + timedelta(minutes=90), tolerance_multiple=1.0)


def test_capability_questions_are_answered_by_the_frame():
    f = make_frame()
    assert f.has(Capability.OHLCV)
    assert not f.has(Capability.AGGRESSOR_TICKS)


def test_tail_preserves_metadata_and_shortens_the_data():
    f = make_frame(bars=50)
    t = f.tail(10)
    assert len(t.data) == 10
    assert t.venue == f.venue
    assert t.symbol == f.symbol
    assert t.capabilities == f.capabilities
    assert t.as_of == f.as_of


def test_a_frame_rejects_a_naive_as_of():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {"open": [1.0] * 3, "high": [2.0] * 3, "low": [0.5] * 3, "close": [1.5] * 3, "volume": [1.0] * 3},
        index=idx,
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        OHLCVFrame(
            data=data,
            venue="fake",
            symbol="X",
            timeframe="1h",
            as_of=datetime(2026, 1, 1),
            capabilities=frozenset({Capability.OHLCV}),
        )


def test_a_frame_rejects_a_nan_in_the_price_columns():
    idx = pd.date_range("2026-01-01", periods=3, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {"open": [1.0] * 3, "high": [2.0] * 3, "low": [0.5] * 3, "close": [1.5, float("nan"), 1.5], "volume": [1.0] * 3},
        index=idx,
    )
    with pytest.raises(ValueError, match="NaN"):
        OHLCVFrame(
            data=data,
            venue="fake",
            symbol="X",
            timeframe="1h",
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            capabilities=frozenset({Capability.OHLCV}),
        )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_frame.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.store'`

- [ ] **Step 3: Implement the frame**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/store/__init__.py` (empty) and `src/desk/store/frame.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_frame.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
cd ~/trading-desk
git add src/desk/store tests/test_frame.py
git commit -m "feat: OHLCVFrame carrying as_of, capabilities and staleness"
```

---

### Task 4: Parquet cache and the warm registry

**Files:**
- Create: `src/desk/store/cache.py`
- Modify: `pyproject.toml` (add the `data/` path to `.gitignore` if absent)
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: `OHLCVFrame` from Task 3.
- Produces: `FrameStore(root: pathlib.Path)` with `put(frame) -> None`, `get(venue, symbol, timeframe) -> OHLCVFrame | None`, `warm_keys() -> list[tuple[str, str, str]]`, and `clear_warm() -> None`. Task 9's API reads through this.

The warm registry is what makes the authoring loop feel instant in a later milestone: the parquet read happens once, and recomputing a metric over an in-memory frame is sub-millisecond.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache.py`:

```python
from datetime import datetime, timezone

import pandas as pd
import pytest

from desk.capabilities import Capability
from desk.store.cache import FrameStore
from desk.store.frame import OHLCVFrame


def make_frame(bars: int = 20, symbol: str = "BTC/USDT") -> OHLCVFrame:
    idx = pd.date_range("2026-01-01", periods=bars, freq="1h", tz="UTC")
    data = pd.DataFrame(
        {
            "open": [100.0 + i for i in range(bars)],
            "high": [101.0 + i for i in range(bars)],
            "low": [99.0 + i for i in range(bars)],
            "close": [100.5 + i for i in range(bars)],
            "volume": [10.0] * bars,
        },
        index=idx,
    )
    return OHLCVFrame(
        data=data,
        venue="binance",
        symbol=symbol,
        timeframe="1h",
        as_of=datetime(2026, 1, 2, tzinfo=timezone.utc),
        capabilities=frozenset({Capability.OHLCV, Capability.L2_DEPTH}),
    )


def test_a_put_then_get_round_trips_the_data_and_the_metadata(tmp_path):
    store = FrameStore(root=tmp_path)
    original = make_frame()
    store.put(original)
    store.clear_warm()  # force the read to come off disk

    got = store.get("binance", "BTC/USDT", "1h")
    assert got is not None
    pd.testing.assert_frame_equal(got.data, original.data)
    assert got.venue == "binance"
    assert got.symbol == "BTC/USDT"
    assert got.timeframe == "1h"
    assert got.as_of == original.as_of
    assert got.capabilities == original.capabilities


def test_a_miss_returns_none_rather_than_raising(tmp_path):
    store = FrameStore(root=tmp_path)
    assert store.get("binance", "NOPE/USDT", "1h") is None


def test_the_warm_registry_serves_the_second_read_without_touching_disk(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame())

    first = store.get("binance", "BTC/USDT", "1h")
    assert ("binance", "BTC/USDT", "1h") in store.warm_keys()

    # Delete the parquet file; a warm hit must still succeed.
    for path in tmp_path.rglob("*.parquet"):
        path.unlink()

    second = store.get("binance", "BTC/USDT", "1h")
    assert second is not None
    pd.testing.assert_frame_equal(second.data, first.data)


def test_a_symbol_with_a_slash_does_not_create_a_nested_directory(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame(symbol="BTC/USDT"))
    files = list(tmp_path.rglob("*.parquet"))
    assert len(files) == 1
    assert "/" not in files[0].name
    # exactly one directory level: the venue
    assert files[0].parent.parent == tmp_path


def test_putting_the_same_key_twice_overwrites_rather_than_appending(tmp_path):
    store = FrameStore(root=tmp_path)
    store.put(make_frame(bars=20))
    store.put(make_frame(bars=5))
    store.clear_warm()
    got = store.get("binance", "BTC/USDT", "1h")
    assert got is not None
    assert len(got.data) == 5
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.store.cache'`

- [ ] **Step 3: Implement the store**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/store/cache.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_cache.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Ignore the data directory**

```bash
cd ~/trading-desk
grep -qx 'data/' .gitignore || echo 'data/' >> .gitignore
grep -qx '*.parquet' .gitignore || echo '*.parquet' >> .gitignore
cat .gitignore
```

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
git add src/desk/store/cache.py tests/test_cache.py .gitignore
git commit -m "feat: parquet frame store with a warm in-memory registry"
```

---

### Task 5: The crypto adapter

**Files:**
- Create: `src/desk/adapters/crypto.py`
- Test: `tests/test_crypto_adapter.py`

**Interfaces:**
- Consumes: `AdapterInfo`, `Capability` (Task 2), the `adapter_contract` function (Task 2), `OHLCVFrame` (Task 3).
- Produces: `CryptoAdapter(exchange_id: str = "binance", client=None)` implementing `MarketAdapter`, plus `fetch_frame(symbol, timeframe, limit) -> OHLCVFrame` which Task 9 calls.

Crypto declares `OHLCV`, `AGGRESSOR_TICKS` and `L2_DEPTH`: it is the only market in the project where the full book is free. It does not declare `SESSION_GRID` (24/7), `EXPIRING_SYMBOLS` (spot), `FUNDAMENTALS`, `PARTICIPANT_OI`, or `BROKER_PAPER_API`.

The injected `client` is what keeps this task's tests offline. Network calls in a unit test suite are flaky and slow, and this adapter's job is normalisation, which is exactly what a fake can exercise.

- [ ] **Step 1: Write the failing test**

Create `tests/test_crypto_adapter.py`:

```python
from datetime import timezone

import pytest

from desk.adapters.crypto import CryptoAdapter
from desk.capabilities import Capability
from tests.test_adapter_contract import adapter_contract


class FakeCcxt:
    """Mimics the ccxt return shape: [ms_timestamp, o, h, l, c, v]."""

    id = "binance"

    def __init__(self, rows=None, repeat_last=False):
        if rows is None:
            start = 1_767_225_600_000  # 2026-01-01T00:00:00Z in ms
            rows = [
                [start + i * 3_600_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
                for i in range(60)
            ]
        self.rows = rows
        self.repeat_last = repeat_last
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, limit=None):
        self.calls.append((symbol, timeframe, limit))
        rows = list(self.rows)
        if self.repeat_last:
            rows.append(list(rows[-1]))
        return rows[: limit or len(rows)] if not self.repeat_last else rows


def test_the_crypto_adapter_satisfies_the_shared_contract():
    adapter_contract(CryptoAdapter(client=FakeCcxt()))


def test_crypto_declares_the_book_and_withholds_what_it_does_not_have():
    info = CryptoAdapter(client=FakeCcxt()).info()
    assert info.has(Capability.OHLCV)
    assert info.has(Capability.AGGRESSOR_TICKS)
    assert info.has(Capability.L2_DEPTH)
    assert not info.has(Capability.SESSION_GRID)
    assert not info.has(Capability.FUNDAMENTALS)
    assert not info.has(Capability.PARTICIPANT_OI)
    assert not info.has(Capability.BROKER_PAPER_API)
    assert info.session_open is None


def test_millisecond_timestamps_become_a_utc_index():
    frame = CryptoAdapter(client=FakeCcxt()).fetch_ohlcv("BTC/USDT", "1h", 10)
    assert str(frame.index.tz) == "UTC"
    assert frame.index[0].year == 2026
    assert len(frame) == 10


def test_a_duplicate_timestamp_is_dropped_keeping_the_last():
    client = FakeCcxt(repeat_last=True)
    frame = CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 60)
    assert not frame.index.has_duplicates


def test_fetch_frame_wraps_the_data_with_capabilities_and_an_as_of():
    adapter = CryptoAdapter(client=FakeCcxt())
    frame = adapter.fetch_frame("BTC/USDT", "1h", 30)
    assert frame.venue == "binance"
    assert frame.symbol == "BTC/USDT"
    assert frame.timeframe == "1h"
    assert frame.as_of.tzinfo is not None
    assert frame.has(Capability.L2_DEPTH)
    assert len(frame.data) == 30


def test_an_empty_response_raises_rather_than_returning_an_empty_frame():
    with pytest.raises(ValueError, match="no bars"):
        CryptoAdapter(client=FakeCcxt(rows=[])).fetch_ohlcv("BTC/USDT", "1h", 10)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_crypto_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.adapters.crypto'`

- [ ] **Step 3: Implement the adapter**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/adapters/crypto.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_crypto_adapter.py -v`
Expected: PASS, 6 passed

- [ ] **Step 5: Verify once against the real exchange, by hand**

This is the only networked check in M1 and it is deliberately not part of the suite.

```bash
cd ~/trading-desk
uv run python -c "
from desk.adapters.crypto import CryptoAdapter
f = CryptoAdapter().fetch_frame('BTC/USDT', '4h', 200)
print(f.venue, f.symbol, f.timeframe, len(f.data))
print(f.data.tail(3))
print('index tz:', f.data.index.tz, '| monotonic:', f.data.index.is_monotonic_increasing)
"
```

Expected: 200 rows, `index tz: UTC`, `monotonic: True`, and three plausible recent candles. If the exchange is geo-blocked, retry with `CryptoAdapter('kraken')`; the adapter is exchange-agnostic.

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
git add src/desk/adapters/crypto.py tests/test_crypto_adapter.py
git commit -m "feat: ccxt crypto adapter, contract-tested offline"
```

---

### Task 6: L1 metrics (EMA, trend state, session VWAP)

**Files:**
- Create: `src/desk/metrics/__init__.py`, `src/desk/metrics/l1_trend.py`
- Test: `tests/fixtures.py`, `tests/test_metrics_l1.py`

**Interfaces:**
- Consumes: `OHLCVFrame` (Task 3), `MetricResult`/`ok`/`unavailable` (Task 1).
- Produces: `ema(frame, length) -> MetricResult` (value is a `pandas.Series`), `trend_state(frame, fast=20, slow=50) -> MetricResult` (value is a `pandas.Series` of `"up" | "down" | "flat"`), `session_vwap(frame) -> MetricResult`. Tasks 8, 9 and 11 call these through the registry.

EMA lengths follow the user's Pine script: 20 fast, 50 slow, 200 bias. No TA-Lib dependency in M1; EMA is three lines and the expected values are computed by hand in the test, which is a stronger check than agreeing with another library.

- [ ] **Step 1: Write the shared fixtures**

Create `tests/fixtures.py`:

```python
"""Hand-built frames with known features, so expected values are verifiable.

Every number these produce can be checked with a calculator. That matters more
than agreeing with another library, because a library can be wrong in the same
direction as the code under test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from desk.capabilities import Capability
from desk.store.frame import OHLCVFrame

CRYPTO_CAPS = frozenset({Capability.OHLCV, Capability.AGGRESSOR_TICKS, Capability.L2_DEPTH})
OHLCV_ONLY = frozenset({Capability.OHLCV})


def frame_from_rows(
    rows: list[dict[str, float]],
    *,
    timeframe: str = "1h",
    capabilities: frozenset[Capability] = CRYPTO_CAPS,
    start: str = "2026-01-01",
) -> OHLCVFrame:
    idx = pd.date_range(start, periods=len(rows), freq=timeframe.replace("m", "min"), tz="UTC")
    data = pd.DataFrame(rows, index=idx)
    for column in ("open", "high", "low", "close", "volume"):
        if column not in data.columns:
            raise ValueError(f"fixture rows must include {column}")
    return OHLCVFrame(
        data=data[["open", "high", "low", "close", "volume"]].astype("float64"),
        venue="fixture",
        symbol="FIX/USD",
        timeframe=timeframe,
        # as_of is now, not a fixed past date. A fixed date would make every
        # fixture frame stale, which would both mark unrelated metrics stale and
        # make the staleness test vacuous.
        as_of=datetime.now(timezone.utc),
        capabilities=capabilities,
    )


def flat_closes(closes: list[float], **kwargs) -> OHLCVFrame:
    """A frame whose only interesting column is close."""
    rows = [
        {"open": c, "high": c, "low": c, "close": c, "volume": 1.0} for c in closes
    ]
    return frame_from_rows(rows, **kwargs)


def bullish_engulfing_frame() -> OHLCVFrame:
    """Bar 1 is a down bar; bar 2 opens below its close and closes above its open.

    Matches the Pine definition exactly:
      close > open and close[1] < open[1] and close > open[1] and open < close[1]
    """
    return frame_from_rows(
        [
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 96.0, "volume": 1.0},
            {"open": 95.5, "high": 103.0, "low": 95.0, "close": 102.0, "volume": 1.0},
        ]
    )


def hammer_frame() -> OHLCVFrame:
    """body=1, lowerWick=4 (> 2x body), upperWick=0.2 (< 0.6x body), close > open."""
    return frame_from_rows(
        [
            {"open": 100.0, "high": 101.2, "low": 96.0, "close": 101.0, "volume": 1.0},
        ]
    )


def shooting_star_frame() -> OHLCVFrame:
    """body=1, upperWick=4 (> 2x body), lowerWick=0.2 (< 0.6x body), close < open."""
    return frame_from_rows(
        [
            {"open": 101.0, "high": 105.0, "low": 99.8, "close": 100.0, "volume": 1.0},
        ]
    )


def swing_frame() -> OHLCVFrame:
    """A single unambiguous pivot high at index 5 with lookback 2.

    Highs rise to index 5 then fall, so index 5 is strictly greater than the two
    bars either side of it. No other index qualifies.
    """
    highs = [10.0, 11.0, 12.0, 13.0, 14.0, 20.0, 14.5, 13.5, 12.5, 11.5, 10.5]
    rows = [
        {"open": h - 1, "high": h, "low": h - 2, "close": h - 0.5, "volume": 1.0}
        for h in highs
    ]
    return frame_from_rows(rows)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_metrics_l1.py`:

```python
import pandas as pd
import pytest

from desk.metrics.l1_trend import ema, session_vwap, trend_state
from tests.fixtures import flat_closes, frame_from_rows


def test_ema_first_value_equals_the_first_close():
    frame = flat_closes([10.0, 20.0, 30.0])
    result = ema(frame, length=3)
    assert result.is_ok
    assert result.value.iloc[0] == pytest.approx(10.0)


def test_ema_matches_a_hand_computed_series():
    # k = 2 / (3 + 1) = 0.5
    # e0 = 10
    # e1 = 20 * 0.5 + 10 * 0.5 = 15
    # e2 = 30 * 0.5 + 15 * 0.5 = 22.5
    frame = flat_closes([10.0, 20.0, 30.0])
    got = ema(frame, length=3).value
    assert got.iloc[0] == pytest.approx(10.0)
    assert got.iloc[1] == pytest.approx(15.0)
    assert got.iloc[2] == pytest.approx(22.5)


def test_ema_returns_a_series_indexed_like_the_frame():
    frame = flat_closes([1.0] * 30)
    got = ema(frame, length=10).value
    assert isinstance(got, pd.Series)
    assert got.index.equals(frame.data.index)


def test_ema_rejects_a_non_positive_length():
    frame = flat_closes([1.0] * 10)
    assert ema(frame, length=0).is_error
    assert ema(frame, length=-5).is_error


def test_ema_is_unavailable_when_there_are_fewer_bars_than_the_length():
    frame = flat_closes([1.0, 2.0, 3.0])
    result = ema(frame, length=20)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_a_metric_over_a_stale_frame_is_marked_stale():
    from datetime import datetime, timezone
    from dataclasses import replace

    frame = flat_closes([1.0 + i for i in range(30)])
    old = replace(frame, as_of=datetime(2020, 1, 1, tzinfo=timezone.utc))
    result = ema(old, length=10)
    assert result.is_ok
    assert result.stale is True


def test_trend_state_is_up_when_fast_is_above_slow():
    frame = flat_closes([float(i) for i in range(1, 80)])
    got = trend_state(frame, fast=20, slow=50).value
    assert got.iloc[-1] == "up"


def test_trend_state_is_down_when_fast_is_below_slow():
    frame = flat_closes([float(i) for i in range(80, 1, -1)])
    got = trend_state(frame, fast=20, slow=50).value
    assert got.iloc[-1] == "down"


def test_trend_state_is_flat_when_the_emas_are_equal():
    frame = flat_closes([50.0] * 80)
    got = trend_state(frame, fast=20, slow=50).value
    assert got.iloc[-1] == "flat"


def test_session_vwap_matches_a_hand_computed_value():
    # typical price = (h + l + c) / 3
    # bar 1: (12 + 8 + 10)/3 = 10, vol 100 -> 1000
    # bar 2: (22 + 18 + 20)/3 = 20, vol 300 -> 6000
    # vwap at bar 2 = 7000 / 400 = 17.5
    frame = frame_from_rows(
        [
            {"open": 9.0, "high": 12.0, "low": 8.0, "close": 10.0, "volume": 100.0},
            {"open": 19.0, "high": 22.0, "low": 18.0, "close": 20.0, "volume": 300.0},
        ]
    )
    got = session_vwap(frame).value
    assert got.iloc[0] == pytest.approx(10.0)
    assert got.iloc[1] == pytest.approx(17.5)


def test_session_vwap_resets_each_calendar_day():
    rows = []
    for _ in range(48):  # two full days at 1h
        rows.append({"open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1.0})
    rows[24] = {"open": 99.0, "high": 99.0, "low": 99.0, "close": 99.0, "volume": 1.0}
    frame = frame_from_rows(rows)
    got = session_vwap(frame).value
    # The first bar of day two is the only bar in its session so far, so the
    # VWAP equals its own typical price rather than carrying day one's average.
    assert got.iloc[24] == pytest.approx(99.0)


def test_session_vwap_is_unavailable_when_volume_is_all_zero():
    frame = frame_from_rows(
        [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0} for _ in range(5)]
    )
    result = session_vwap(frame)
    assert result.is_unavailable
    assert "volume" in result.reason
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_metrics_l1.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.metrics'`

- [ ] **Step 4: Implement the L1 metrics**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/metrics/__init__.py` (empty) and `src/desk/metrics/l1_trend.py`:


- [ ] **Step 5: Run it to verify it passes**

Run: `uv run pytest tests/test_metrics_l1.py -v`
Expected: PASS, 12 passed

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
git add src/desk/metrics tests/fixtures.py tests/test_metrics_l1.py
git commit -m "feat: L1 metrics (ema, trend_state, session_vwap) with hand-verified fixtures"
```

---

### Task 7: L4 price-action metrics, verbatim from the Pine script

**Files:**
- Create: `src/desk/metrics/l4_price_action.py`
- Test: `tests/test_metrics_l4.py`

**Interfaces:**
- Consumes: `OHLCVFrame` (Task 3), `MetricResult` helpers (Task 1).
- Produces: `bullish_engulfing(frame)`, `bearish_engulfing(frame)`, `hammer(frame)`, `shooting_star(frame)` (each returning a boolean `pandas.Series`), and `swing_pivots(frame, lookback=10)` returning a `MetricResult` whose value is a dict `{"highs": Series[float], "lows": Series[float]}` with NaN at non-pivot bars.

The `swing_pivots` value is the one place a NaN is legitimate: it means "this bar is not a pivot", which is information about the series rather than a missing measurement. The `MetricResult` wrapping it is still `ok`.

These formulas are copied from `reference/pine/Forex_Confluence_AutoTrade_Pro.pine`. Do not adjust the ratios.

- [ ] **Step 1: Write the failing test**

Create `tests/test_metrics_l4.py`:

```python
import numpy as np
import pytest

from desk.metrics.l4_price_action import (
    bearish_engulfing,
    bullish_engulfing,
    hammer,
    shooting_star,
    swing_pivots,
)
from tests.fixtures import (
    bullish_engulfing_frame,
    frame_from_rows,
    hammer_frame,
    shooting_star_frame,
    swing_frame,
)


def test_bullish_engulfing_fires_on_the_known_fixture():
    got = bullish_engulfing(bullish_engulfing_frame()).value
    assert got.iloc[0] == False  # noqa: E712  first bar has no predecessor
    assert got.iloc[1] == True   # noqa: E712


def test_bullish_engulfing_does_not_fire_when_the_prior_bar_was_up():
    frame = frame_from_rows(
        [
            {"open": 95.0, "high": 101.0, "low": 94.0, "close": 100.0, "volume": 1.0},
            {"open": 95.5, "high": 103.0, "low": 95.0, "close": 102.0, "volume": 1.0},
        ]
    )
    assert bullish_engulfing(frame).value.iloc[1] == False  # noqa: E712


def test_bearish_engulfing_is_the_mirror_of_bullish():
    frame = frame_from_rows(
        [
            {"open": 96.0, "high": 101.0, "low": 95.0, "close": 100.0, "volume": 1.0},
            {"open": 100.5, "high": 101.0, "low": 94.0, "close": 95.5, "volume": 1.0},
        ]
    )
    assert bearish_engulfing(frame).value.iloc[1] == True  # noqa: E712


def test_hammer_fires_on_the_known_ratios():
    # body = 1.0, lowerWick = 4.0 > 2 * body, upperWick = 0.2 < 0.6 * body
    assert hammer(hammer_frame()).value.iloc[0] == True  # noqa: E712


def test_hammer_does_not_fire_when_the_upper_wick_is_too_long():
    frame = frame_from_rows(
        [{"open": 100.0, "high": 102.0, "low": 96.0, "close": 101.0, "volume": 1.0}]
    )
    # upperWick = 1.0, which is not < 0.6 * body (0.6)
    assert hammer(frame).value.iloc[0] == False  # noqa: E712


def test_shooting_star_fires_on_the_known_ratios():
    assert shooting_star(shooting_star_frame()).value.iloc[0] == True  # noqa: E712


def test_a_doji_with_zero_range_fires_nothing_and_does_not_divide_by_zero():
    frame = frame_from_rows(
        [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1.0}]
    )
    assert hammer(frame).value.iloc[0] == False  # noqa: E712
    assert shooting_star(frame).value.iloc[0] == False  # noqa: E712


def test_swing_pivots_finds_the_single_known_pivot_high():
    result = swing_pivots(swing_frame(), lookback=2)
    assert result.is_ok
    highs = result.value["highs"]
    assert highs.notna().sum() == 1
    assert highs.dropna().iloc[0] == pytest.approx(20.0)
    assert highs.dropna().index[0] == swing_frame().data.index[5]


def test_swing_pivots_marks_non_pivot_bars_as_nan():
    highs = swing_pivots(swing_frame(), lookback=2).value["highs"]
    assert np.isnan(highs.iloc[0])
    assert np.isnan(highs.iloc[4])


def test_swing_pivots_is_unavailable_when_the_frame_is_shorter_than_the_window():
    frame = frame_from_rows(
        [{"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1.0}] * 4
    )
    result = swing_pivots(frame, lookback=10)
    assert result.is_unavailable
    assert "bars" in result.reason


def test_swing_pivots_rejects_a_non_positive_lookback():
    assert swing_pivots(swing_frame(), lookback=0).is_error
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_metrics_l4.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.metrics.l4_price_action'`

- [ ] **Step 3: Implement the L4 metrics**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/metrics/l4_price_action.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_metrics_l4.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
cd ~/trading-desk
git add src/desk/metrics/l4_price_action.py tests/test_metrics_l4.py
git commit -m "feat: L4 candle patterns and swing pivots, transcribed from the Pine source"
```

---

### Task 8: The registry and the no-lookahead property test

**Files:**
- Create: `src/desk/metrics/registry.py`
- Test: `tests/test_no_lookahead.py`

**Interfaces:**
- Consumes: every metric function from Tasks 6 and 7, `Capability` (Task 2).
- Produces: `REGISTRY: dict[str, MetricSpec]` where `MetricSpec` is a frozen dataclass with `fn`, `requires: frozenset[Capability]`, `returns: str` (one of `"series"`, `"series_bool"`, `"dict_of_series"`), and `defaults: dict`. Tasks 9 and 11 iterate this.

This is the highest-value test in M1. Lookahead is the most common and most expensive bug class in this domain, and this one mechanical test catches it in metrics nobody thought carefully about. It has already proven itself: it is the class of defect found in the user's own Pine script, where TP1 could be marked using the entry bar's own high.

- [ ] **Step 1: Write the registry**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/metrics/registry.py`:


- [ ] **Step 2: Write the failing property test**

Create `tests/test_no_lookahead.py`:

```python
"""No metric may depend on a bar that has not happened yet.

The check: compute the metric over the full frame, then recompute it over a
truncated prefix of that frame. Values at the bars both runs share must be
identical. If a metric reads future bars, truncating changes its past, and this
test fails.

swing_pivots is the one deliberate exception and it is marked as such: a pivot
is only confirmable once `lookback` bars have printed after it, which is
lookahead by construction. The exception is recorded here rather than silently
tolerated, and the test still enforces that the metric is centred as documented.
"""

from __future__ import annotations

import pandas as pd
import pytest

from desk.metrics.registry import REGISTRY
from desk.store.frame import OHLCVFrame
from tests.fixtures import frame_from_rows

CONFIRMS_LATE = {"swing_pivots"}


def realistic_frame(bars: int = 200):
    rows = []
    price = 100.0
    for i in range(bars):
        drift = 0.6 if i < 120 else -0.4
        o = price
        c = o + drift + ((i * 37) % 11 - 5) * 0.25
        rows.append(
            {
                "open": o,
                "high": max(o, c) + 1.0 + (i % 5) * 0.2,
                "low": min(o, c) - 1.0 - (i % 7) * 0.15,
                "close": c,
                "volume": 100.0 + (i % 13) * 10,
            }
        )
        price = c
    return frame_from_rows(rows)


def series_of(result, kind: str) -> list[pd.Series]:
    if kind == "dict_of_series":
        return list(result.value.values())
    return [result.value]


@pytest.mark.parametrize("name", sorted(REGISTRY))
@pytest.mark.parametrize("cut", [140, 170, 190])
def test_metric_output_on_shared_bars_is_unchanged_by_truncation(name, cut):
    spec = REGISTRY[name]
    full = realistic_frame(200)
    short = OHLCVFrame(
        data=full.data.iloc[:cut],
        venue=full.venue,
        symbol=full.symbol,
        timeframe=full.timeframe,
        as_of=full.as_of,
        capabilities=full.capabilities,
    )

    full_result = spec.fn(full, **spec.defaults)
    short_result = spec.fn(short, **spec.defaults)

    if not (full_result.is_ok and short_result.is_ok):
        pytest.skip(f"{name} is not computable on this fixture")

    # A metric that only confirms after N future bars cannot be compared on the
    # final N bars of the truncated run. Trim that tail before comparing.
    trim = spec.defaults.get("lookback", 0) if name in CONFIRMS_LATE else 0
    end = cut - trim

    for a, b in zip(series_of(full_result, spec.returns), series_of(short_result, spec.returns)):
        left = a.iloc[:end]
        right = b.iloc[:end]
        pd.testing.assert_series_equal(
            left, right, check_names=False,
            obj=f"{name}: truncating at {cut} changed its output before bar {end}",
        )


def test_the_lookahead_exception_list_is_not_silently_growing():
    # If a future milestone adds a metric that needs this exception, it must be
    # a deliberate edit to this test, reviewed, not an accident.
    assert CONFIRMS_LATE == {"swing_pivots"}


def test_every_registered_metric_is_covered_by_this_test():
    assert set(REGISTRY) == {
        "ema",
        "trend_state",
        "session_vwap",
        "bullish_engulfing",
        "bearish_engulfing",
        "hammer",
        "shooting_star",
        "swing_pivots",
    }
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_no_lookahead.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.metrics.registry'` if Step 1 was skipped. If Step 1 was done, expect PASS. Should any metric fail, that metric reads future bars and must be fixed; do not add it to `CONFIRMS_LATE` to make the test green.

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_no_lookahead.py -v`
Expected: PASS, 26 passed (8 metrics x 3 cuts, plus 2 guard tests)

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v`
Expected: PASS, all tests green.

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
git add src/desk/metrics/registry.py tests/test_no_lookahead.py
git commit -m "test: no-lookahead property test over the whole metric registry

Recomputes every metric on truncated frames and asserts the shared prefix is
unchanged. swing_pivots is the single recorded exception, since a pivot is
only confirmable once lookback bars print after it."
```

---

### Task 9: The localhost API, with the binding asserted

**Files:**
- Create: `src/desk/api/__init__.py`, `src/desk/api/app.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `FrameStore` (Task 4), `CryptoAdapter` (Task 5), `REGISTRY` (Task 8).
- Produces: `create_app(store, adapter) -> fastapi.FastAPI` with `GET /health`, `GET /ohlcv?symbol=&timeframe=&limit=`, `GET /metric/{name}?symbol=&timeframe=&limit=`, `GET /metrics` (the registry listing); plus `HOST = "127.0.0.1"` and `main()` for `uvicorn`. Task 10's TypeScript client calls these.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`:

```python
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from desk.api.app import HOST, create_app
from desk.store.cache import FrameStore
from tests.test_crypto_adapter import FakeCcxt
from desk.adapters.crypto import CryptoAdapter


@pytest.fixture()
def client(tmp_path):
    store = FrameStore(root=tmp_path)
    adapter = CryptoAdapter(client=FakeCcxt())
    return TestClient(create_app(store=store, adapter=adapter))


def test_the_api_binds_loopback_only():
    # The authoring loop POSTs code from a browser into a local Python process.
    # Widening this to 0.0.0.0 would expose that path to the network.
    assert HOST == "127.0.0.1"
    assert HOST != "0.0.0.0"


def test_the_app_installs_no_cors_middleware_at_all():
    # The browser never makes a cross-origin request: Vite proxies /api to the
    # engine, so the page and the data share an origin. That means no CORS
    # config exists to be loosened later into a wildcard.
    store = FrameStore(root="/tmp/desk-cors-check")
    app = create_app(store=store, adapter=CryptoAdapter(client=FakeCcxt()))
    installed = [m.cls.__name__ for m in app.user_middleware]
    assert "CORSMiddleware" not in installed, (
        f"unexpected CORS middleware: {installed}. The Vite proxy makes it "
        "unnecessary, and an unnecessary CORS config is one somebody widens."
    )


def test_health_reports_the_venue_and_capabilities(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["venue"] == "binance"
    assert "ohlcv" in body["capabilities"]
    assert body["host"] == "127.0.0.1"


def test_ohlcv_returns_bars_shaped_for_a_candlestick_chart(client):
    r = client.get("/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 20})
    assert r.status_code == 200
    body = r.json()
    assert len(body["bars"]) == 20
    first = body["bars"][0]
    assert set(first) == {"time", "open", "high", "low", "close", "volume"}
    assert isinstance(first["time"], int)  # unix seconds, what the chart wants


def test_a_metric_endpoint_returns_a_value_with_its_state(client):
    r = client.get("/metric/ema", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 60, "length": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ok"
    assert body["stale"] is False
    assert len(body["points"]) == 60
    assert set(body["points"][0]) == {"time", "value"}


def test_an_unavailable_metric_returns_200_with_its_reason(client):
    # 60 bars cannot support a 500-length EMA. That is unavailable, not an error.
    r = client.get("/metric/ema", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 60, "length": 500})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "unavailable"
    assert "bars" in body["reason"]
    assert body["points"] == []


def test_an_unknown_metric_is_a_404_naming_what_is_available(client):
    r = client.get("/metric/does_not_exist", params={"symbol": "BTC/USDT", "timeframe": "1h"})
    assert r.status_code == 404
    assert "ema" in r.json()["detail"]


def test_the_registry_listing_exposes_every_metric_and_its_requirements(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    names = {m["name"] for m in body["metrics"]}
    assert "swing_pivots" in names
    assert len(names) == 8
    ema = next(m for m in body["metrics"] if m["name"] == "ema")
    assert ema["requires"] == ["ohlcv"]
    assert ema["defaults"] == {"length": 20}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'desk.api'`

- [ ] **Step 3: Implement the API**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `src/desk/api/__init__.py` (empty) and `src/desk/api/app.py`:


- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Start it and check by hand**

```bash
cd ~/trading-desk
uv run python -m desk.api.app &
sleep 3
curl -s localhost:8787/health | python3 -m json.tool
curl -s "localhost:8787/metric/ema?symbol=BTC/USDT&timeframe=4h&limit=50&length=20" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['state'], d['stale'], len(d['points']))"
curl -s "localhost:8787/metric/ema?length=9999" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['state'], '|', d['reason'])"
```

Expected: health shows `binance` and the three crypto capabilities; the EMA call prints `ok False 50`; the 9999-length call prints `unavailable | needs at least 9999 bars...`. Leave the server running for Task 10.

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
git add src/desk/api tests/test_api.py
git commit -m "feat: loopback-bound read API serving series and registry metadata

The binding is asserted in a test, because a later milestone POSTs indicator
source from a browser into this process."
```

---

### Task 10: The chart shell

**Files:**
- Create: `web/package.json`, `web/index.html`, `web/vite.config.ts`, `web/src/api.ts`, `web/src/chart.ts`, `web/src/main.ts`
- Modify: `.gitignore` (add `node_modules/` and `web/dist/`)

**Interfaces:**
- Consumes: the API from Task 9, and `design/tokens.css` from the committed design system.
- Produces: a dev server on `localhost:5173` rendering candles with EMA 20, 50 and 200 overlaid, plus a metrics panel showing each metric's state.

This is the milestone's visible deliverable. It is deliberately thin: it fetches precomputed series and draws them. It computes nothing.

- [ ] **Step 1: Scaffold the frontend**

```bash
cd ~/trading-desk
mkdir -p web/src
cd web
cat > package.json <<'JSON'
{
  "name": "desk-web",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "devDependencies": {
    "typescript": "^5.7.0",
    "vite": "^6.0.0"
  }
}
JSON
pnpm install
pnpm add @openalgo/charts
```

If `@openalgo/charts` is not published to npm under that name, resolve the actual package name from the repository before continuing:

```bash
curl -s https://api.github.com/repos/marketcalls/openalgo-charts/contents/package.json \
  | python3 -c "import json,sys,base64; print(json.loads(base64.b64decode(json.load(sys.stdin)['content']))['name'])"
```

Then `pnpm add <that-name>`. If the package is unpublished, fall back to `pnpm add lightweight-charts` and note the substitution in the commit message: the chart engine choice is recorded as Moderate reversibility in the Spec 1 ledger precisely so this swap is cheap.

- [ ] **Step 2: Write the typed API client**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `web/src/api.ts`:


- [ ] **Step 3: Write the chart module**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `web/src/chart.ts`:


- [ ] **Step 4: Write the page and entry point**

**Your turn.** Write this yourself. What you have to work from:

- the failing test above, which specifies the behaviour exactly
- the `Interfaces:` block at the top of this task, which gives you the names and
  types the neighbouring tasks expect
- the file path and the one-line responsibility from the File Structure section

Do not open the full plan for this step until your test passes. When it does,
diff yours against mine and we talk about the differences, which is where the
learning actually is.

Stuck? Ask, and say which level you want:
`nudge` (a question) · `narrow` (which concept) · `shape` (the signature and
structure, no bodies) · `show` (the code, and we review why).

Create `web/index.html`:


Create `web/src/main.ts`:


Create `web/vite.config.ts`:


- [ ] **Step 5: Run it and look at it**

```bash
cd ~/trading-desk
# the engine must be running from Task 9 step 5
curl -s localhost:8787/health > /dev/null && echo "engine up" || echo "START THE ENGINE FIRST"
cd web && pnpm dev
```

Open `http://localhost:5173`. Expected: candles in Slate and Ember green and red, three EMA lines (amber 50, blue 20, dim 200), and a right panel listing all eight metrics. `swing_pivots` shows a value; nothing shows `unavailable` yet, because crypto provides every capability M1's metrics require.

- [ ] **Step 6: Commit**

```bash
cd ~/trading-desk
grep -qx 'node_modules/' .gitignore || echo 'node_modules/' >> .gitignore
grep -qx 'web/dist/' .gitignore || echo 'web/dist/' >> .gitignore
git add web .gitignore
git commit -m "feat: M1 chart shell rendering candles and EMA overlays from the engine"
```

---

### Task 11: Prove the degradation contract end to end

**Files:**
- Test: `tests/test_degradation.py`

**Interfaces:**
- Consumes: `REGISTRY` (Task 8), `OHLCVFrame` (Task 3), `create_app` (Task 9).
- Produces: nothing. This task adds no production code; it proves the contract the whole engine is built around actually holds.

Until now nothing has exercised a capability mismatch, because crypto provides everything M1 needs. This task introduces a deliberately impoverished adapter so the `Unavailable` path is tested before any market that actually needs it arrives.

- [ ] **Step 1: Write the test**

Create `tests/test_degradation.py`:

```python
"""A metric asked to run on an adapter that cannot feed it must refuse.

Crypto provides every capability M1's metrics need, so without a deliberately
impoverished adapter this path would ship untested and first be exercised by
NSE three milestones from now.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from desk.api.app import create_app
from desk.capabilities import AdapterInfo, Capability
from desk.metrics.registry import REGISTRY, MetricSpec
from desk.result import ok, unavailable
from desk.store.cache import FrameStore
from desk.store.frame import OHLCVFrame
from tests.fixtures import frame_from_rows


def nse_like_frame() -> OHLCVFrame:
    """OHLCV and a session grid, but no aggressor ticks and no L2 depth."""
    rows = [
        {"open": 100.0 + i, "high": 101.0 + i, "low": 99.0 + i, "close": 100.5 + i, "volume": 10.0}
        for i in range(60)
    ]
    frame = frame_from_rows(rows, capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID}))
    return replace(frame, venue="nse")


class PoorAdapter:
    """Declares only what NSE actually has."""

    probe_symbol = "RELIANCE"

    def info(self) -> AdapterInfo:
        from datetime import time

        return AdapterInfo(
            venue="nse",
            capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID}),
            session_open=time(9, 15),
        )

    def fetch_frame(self, symbol: str, timeframe: str, limit: int) -> OHLCVFrame:
        return nse_like_frame()

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        return nse_like_frame().data


def test_every_m1_metric_runs_on_an_ohlcv_only_adapter():
    # None of M1's eight metrics need anything beyond OHLCV, so all eight must
    # work on the impoverished adapter. If one fails here, its declared
    # requirements in the registry are wrong.
    frame = nse_like_frame()
    for name, spec in REGISTRY.items():
        missing = [c for c in spec.requires if not frame.has(c)]
        assert not missing, f"{name} declares {missing}, which M1 metrics should not need"
        result = spec.fn(frame, **spec.defaults)
        assert not result.is_error, f"{name} errored on an OHLCV-only frame: {result.error}"


def test_the_api_refuses_a_metric_whose_capability_is_absent():
    # Register a synthetic metric needing aggressor ticks, the way CVD will in a
    # later milestone, and confirm the API refuses it rather than computing one.
    def fake_cvd(frame, **_):
        return ok(frame.data["close"] * 0)  # never reached

    REGISTRY["fake_cvd"] = MetricSpec(
        fake_cvd, frozenset({Capability.AGGRESSOR_TICKS}), "series", {}
    )
    try:
        store = FrameStore(root="/tmp/desk-degradation-test")
        client = TestClient(create_app(store=store, adapter=PoorAdapter()))
        response = client.get(
            "/metric/fake_cvd", params={"symbol": "RELIANCE", "timeframe": "1h", "limit": 50}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "unavailable"
        assert "aggressor_ticks" in body["reason"]
        assert body["points"] == []
    finally:
        REGISTRY.pop("fake_cvd", None)


def test_no_metric_ever_returns_nan_as_a_value():
    frame = nse_like_frame()
    for name, spec in REGISTRY.items():
        result = spec.fn(frame, **spec.defaults)
        if not result.is_ok:
            continue
        values = result.value.values() if spec.returns == "dict_of_series" else [result.value]
        for series in values:
            if name == "swing_pivots":
                continue  # NaN here means "not a pivot", documented in Task 7
            assert not series.isna().any(), f"{name} emitted NaN; use unavailable() instead"


def test_unavailable_requires_a_reason_so_the_ui_can_explain_itself():
    r = unavailable("adapter has no aggressor-tagged ticks")
    assert r.reason
    with pytest.raises(ValueError):
        unavailable("   ")
```

- [ ] **Step 2: Run it to verify it passes**

Run: `uv run pytest tests/test_degradation.py -v`
Expected: PASS, 4 passed. If `test_every_m1_metric_runs_on_an_ohlcv_only_adapter` fails, a metric's `requires` in the registry overstates what it needs; fix the registry, not the test.

- [ ] **Step 3: Run the whole suite and record the count**

Run: `uv run pytest -q`
Expected: all green. Record the total in the commit message.

- [ ] **Step 4: Commit**

```bash
cd ~/trading-desk
git add tests/test_degradation.py
git commit -m "test: degradation contract proven against an OHLCV-only adapter

Crypto provides every capability M1 needs, so the Unavailable path would
otherwise first be exercised by NSE three milestones out."
```

---

## M1 Done When

- [ ] `uv run pytest -q` is green.
- [ ] `uv run python -m desk.api.app` serves `/health`, `/ohlcv`, `/metrics` and `/metric/{name}` on `127.0.0.1:8787`.
- [ ] `cd web && pnpm dev` shows candles, three EMA overlays, and all eight metrics with their states.
- [ ] Asking for an EMA longer than the frame returns `unavailable` with a reason, not an error and not a number.
- [ ] No `risk/` module exists, no broker credential is referenced anywhere, and no code path can submit an order.

## Deliberately Deferred to M2

Recorded here so they are visible rather than forgotten:

- **TA-Lib and pandas-ta cross-checks.** M1's eight metrics are hand-verified, which is a stronger check. The golden-value suite begins in M2 when the metric count justifies `brew install ta-lib`.
- **The real OpenAlgo Charts engine.** Task 10 draws candles on a canvas so M1 has a visible deliverable without blocking on the package name. Swapping the engine is recorded as Moderate reversibility in the Spec 1 ledger.
- **The Monaco authoring loop and its WebSocket.** The API is read-only in M1; nothing POSTs code yet, which is why the loopback binding test matters before that lands rather than after.
- **Supply and demand zones, imbalance, BOS, value area, Fibonacci**, and the two Pine strategy ports.
- **The six provisional decisions** in the specs' open sections. M1 running is the right moment to re-examine the Python-core split and the adapter interface against Angel One's and Fyers' actual docs.
