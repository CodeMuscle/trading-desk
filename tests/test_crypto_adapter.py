import pytest

from desk.adapters.crypto import CryptoAdapter
from desk.capabilities import Capability
from tests.test_adapter_contract import adapter_contract


class FakeCcxt:
    """Mimics the ccxt return shape: [ms_timestamp, o, h, l, c, v]."""

    id = "binance"

    def __init__(self, rows=None, repeat_last=False):
        if rows is None:
            # Anchored to the real clock, not to a fixed date. The adapter
            # computes its window by counting bars back from now, so a fixture
            # pinned to January drifts an hour further out of range every hour
            # and eventually falls entirely outside the window it is meant to
            # satisfy. This is the second fixture in this suite to have had
            # that bug.
            from datetime import datetime, timezone

            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            start = now_ms - 60 * 3_600_000
            rows = [
                [start + i * 3_600_000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
                for i in range(60)
            ]
        self.rows = rows
        self.repeat_last = repeat_last
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        # `since` mirrors the real ccxt signature. The pagination path passes it
        # whenever more bars are wanted than fit in one page, and a fake that
        # omits it fails with a TypeError that looks like a bug in the adapter.
        self.calls.append((symbol, timeframe, limit))
        rows = [r for r in self.rows if since is None or r[0] >= since]
        if self.repeat_last and rows:
            rows.append(list(rows[-1]))
            return rows
        return rows[: limit or len(rows)]


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


# --------------------------------------------------------------- pagination

class CappedCcxt:
    """An exchange that silently caps every call at 1000 bars, as real ones do.

    The silence is the point. Asking for 5000 does not error; you get 1000 and
    a backtest that quietly covers a fraction of the intended window.
    """

    id = "capped"
    STEP_MS = 3_600_000  # 1h

    def __init__(self, history_bars: int = 5000, cap: int = 1000):
        self.cap = cap
        # Anchored to the real clock, because the adapter computes its start
        # window from datetime.now(). A hardcoded "now" here drifts a bar per
        # hour against it and silently truncates the window under test.
        from datetime import datetime, timezone

        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.start = now - history_bars * self.STEP_MS
        self.history = [
            [self.start + i * self.STEP_MS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
            for i in range(history_bars)
        ]
        self.calls: list[tuple] = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        rows = self.history
        if since is not None:
            rows = [r for r in rows if r[0] >= since]
        return rows[: min(limit or self.cap, self.cap)]


def test_a_single_page_request_is_still_one_call():
    client = CappedCcxt()
    CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 500)
    assert len(client.calls) == 1


def test_more_than_the_page_size_is_paged_rather_than_silently_truncated():
    client = CappedCcxt(history_bars=5000)
    frame = CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 3000)
    assert len(client.calls) > 1, "a 3000-bar request must page"
    assert len(frame) == 3000, f"got {len(frame)} bars, not the 3000 requested"


def test_paging_produces_a_clean_sorted_index_with_no_duplicates():
    client = CappedCcxt(history_bars=5000)
    frame = CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 2500)
    assert frame.index.is_monotonic_increasing
    assert not frame.index.has_duplicates
    assert str(frame.index.tz) == "UTC"


def test_paging_stops_when_the_history_runs_out_rather_than_looping():
    client = CappedCcxt(history_bars=1500)
    frame = CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 10_000)
    assert len(frame) <= 1500
    assert len(client.calls) < 10, "a short page must end the walk"


def test_an_exchange_that_rewinds_does_not_loop_forever():
    class Rewinds(CappedCcxt):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            self.calls.append((symbol, timeframe, since, limit))
            # Always returns the same first page, never advancing.
            return self.history[: self.cap]

    client = Rewinds(history_bars=5000)
    CryptoAdapter(client=client).fetch_ohlcv("BTC/USDT", "1h", 4000)
    assert len(client.calls) <= 61, "the runaway guard must bound the walk"


def test_an_unparseable_timeframe_raises_before_any_network_call():
    from desk.adapters.crypto import timeframe_ms

    with pytest.raises(ValueError, match="timeframe"):
        timeframe_ms("fortnightly")


def test_timeframe_ms_matches_the_frame_parser():
    from desk.adapters.crypto import timeframe_ms

    assert timeframe_ms("1m") == 60_000
    assert timeframe_ms("4h") == 14_400_000
    assert timeframe_ms("1d") == 86_400_000


def test_asking_for_more_history_than_exists_does_not_send_a_negative_since():
    """Regression: a request for everything produced an illegal timestamp.

    The window start is computed by counting bars back from now, so asking for
    more bars than the venue has makes it negative. Binance does not read that
    as a very early time; it rejects the request outright with "Illegal
    characters found in parameter 'startTime'". Asking for 60,000 daily bars is
    nine times the history that exists, which is the ordinary way a caller says
    "everything you have".
    """
    seen: list[int | None] = []

    class Recording(FakeCcxt):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            seen.append(since)
            return super().fetch_ohlcv(symbol, timeframe, since=since, limit=limit)

    adapter = CryptoAdapter(client=Recording())
    adapter.fetch_frame("BTC/USDT", "1d", 60_000)

    assert seen, "no page was requested"
    for since in seen:
        assert since is None or since >= 0, f"negative since: {since}"
