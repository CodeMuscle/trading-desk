import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from desk.adapters.crypto import CryptoAdapter
from desk.api.app import HOST, create_app
from desk.store.cache import FrameStore
from tests.test_crypto_adapter import FakeCcxt


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


def test_the_app_installs_no_cors_middleware_at_all(tmp_path):
    # The browser never makes a cross-origin request: Vite proxies /api to the
    # engine, so the page and the data share an origin. That means no CORS
    # config exists to be loosened later into a wildcard.
    store = FrameStore(root=tmp_path)
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
    assert isinstance(first["time"], int)


def test_a_metric_endpoint_returns_a_value_with_its_state(client):
    r = client.get("/metric/ema", params={"symbol": "BTC/USDT", "timeframe": "1h",
                                          "limit": 60, "length": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "ok"
    assert len(body["points"]) == 60
    assert set(body["points"][0]) == {"time", "value"}


def test_an_unavailable_metric_returns_200_with_its_reason(client):
    r = client.get("/metric/ema", params={"symbol": "BTC/USDT", "timeframe": "1h",
                                          "limit": 60, "length": 500})
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
    from desk.metrics.registry import REGISTRY

    names = {m["name"] for m in body["metrics"]}
    # Compared against the registry rather than a literal, so adding a metric
    # does not break an unrelated test every milestone.
    assert names == set(REGISTRY)
    assert "swing_pivots" in names
    ema = next(m for m in body["metrics"] if m["name"] == "ema")
    assert ema["requires"] == ["ohlcv"]
    assert ema["defaults"] == {"length": 20}


def test_a_stale_cached_frame_is_refetched_rather_than_served(tmp_path):
    """Regression: the chart served two-day-old candles under a live header.

    The cache was consulted for existence and bar count but never for age, so a
    frame written once was returned for as long as it held enough bars. The
    engine was honest about it, marking all eighteen metrics stale, and the UI
    drew that as a slightly dimmer colour. The fix belongs here rather than in
    the UI: a read API should not hand back data it knows is expired.
    """
    import dataclasses
    from datetime import datetime, timedelta, timezone

    adapter = CryptoAdapter(client=FakeCcxt())
    fetches = {"n": 0}
    real = adapter.fetch_frame

    def counting(*args, **kwargs):
        fetches["n"] += 1
        return real(*args, **kwargs)

    adapter.fetch_frame = counting  # type: ignore[method-assign]

    fresh = real("BTC/USDT", "1h", 60)
    stale = dataclasses.replace(
        fresh,
        as_of=datetime.now(timezone.utc)
        - timedelta(seconds=fresh.timeframe_seconds() * 10),
    )

    store = FrameStore(root=tmp_path / "frames")
    store.put(stale)

    client = TestClient(create_app(store=store, adapter=adapter))
    response = client.get(
        "/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 50}
    )
    assert response.status_code == 200
    # The cached frame already holds more bars than the request needs, so only
    # the age check can explain the refetch.
    assert fetches["n"] == 1


def test_a_fresh_cached_frame_is_served_without_refetching(tmp_path):
    """The other half: the cache has to still be a cache."""
    adapter = CryptoAdapter(client=FakeCcxt())
    fetches = {"n": 0}
    real = adapter.fetch_frame

    def counting(*args, **kwargs):
        fetches["n"] += 1
        return real(*args, **kwargs)

    adapter.fetch_frame = counting  # type: ignore[method-assign]

    store = FrameStore(root=tmp_path / "frames")
    store.put(real("BTC/USDT", "1h", 60))

    client = TestClient(create_app(store=store, adapter=adapter))
    client.get("/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 50})
    assert fetches["n"] == 0


@pytest.fixture()
def desk(tmp_path):
    """An app wired to a real account store with one advanced account."""
    import dataclasses

    from desk.backtest.costs import CostModel
    from desk.paper.account import AccountStore
    from desk.paper.engine import PaperEngine
    from desk.strategy.base import Intent, Side

    adapter = CryptoAdapter(client=FakeCcxt())
    frame = adapter.fetch_frame("BTC/USDT", "1h", 60)
    accounts = AccountStore(tmp_path / "desk.sqlite")
    accounts.create("swing", venue="binance", starting_equity=50_000.0)

    closes = frame.data["close"]
    intents = [
        Intent(
            index=closes.index[i], side=Side.LONG, entry=float(closes.iloc[i]),
            stop=float(closes.iloc[i]) * 0.99, target=float(closes.iloc[i]) * 1.01,
        )
        for i in range(20, len(closes), 6)
    ]
    PaperEngine(accounts, "swing", costs=CostModel()).advance(
        frame, intents, max_staleness=None, backfill=True
    )

    store = FrameStore(root=tmp_path / "frames")
    client = TestClient(create_app(store, adapter, accounts))
    return client, accounts, dataclasses


def test_the_api_exposes_no_write_route_at_all(desk):
    """Read only is structural, not a convention.

    No POST, PUT, PATCH or DELETE exists, so there is no route that advances an
    account, sizes a position or changes a limit. Advancing happens through the
    CLI, where the actor is a person or their scheduler rather than anything
    that reached the page.
    """
    client, _accounts, _d = desk
    app = client.app
    writes = [
        (route.path, sorted(route.methods - {"HEAD", "OPTIONS"}))
        for route in app.routes
        if getattr(route, "methods", None)
        and (route.methods - {"GET", "HEAD", "OPTIONS"})
    ]
    assert writes == [], f"unexpected write routes: {writes}"


def test_accounts_lists_equity_and_whether_a_position_is_open(desk):
    client, _a, _d = desk
    body = client.get("/accounts").json()
    assert len(body["accounts"]) == 1
    account = body["accounts"][0]
    assert account["name"] == "swing"
    assert account["starting_equity"] == 50_000.0
    assert "return_pct" in account
    assert isinstance(account["position_open"], bool)


def test_an_unknown_account_404s_and_names_the_known_ones(desk):
    client, _a, _d = desk
    response = client.get("/account/ghost")
    assert response.status_code == 404
    assert "swing" in response.json()["detail"]


def test_the_journal_reports_r_multiples_not_just_currency(desk):
    """R is the unit a trader reasons in. Currency alone hides whether a loss
    was the planned one or a stop that did not hold."""
    client, _a, _d = desk
    body = client.get("/journal/swing").json()
    assert body["summary"]["count"] > 0
    for trade in body["trades"]:
        assert "r_multiple" in trade
        assert trade["outcome"] in {"stop", "target", "end_of_data"}
    assert body["summary"]["win_rate"] is not None
    assert body["summary"]["expectancy_r"] is not None


def test_an_empty_journal_summarises_as_none_rather_than_zero(desk, tmp_path):
    """A win rate of 0.0 on no trades is a claim. None is the absence of one."""
    client, accounts, _d = desk
    accounts.create("fresh", venue="binance", starting_equity=1_000.0)
    body = client.get("/journal/fresh").json()
    assert body["summary"]["count"] == 0
    assert body["summary"]["win_rate"] is None
    assert body["summary"]["expectancy_r"] is None


def test_refusals_are_their_own_endpoint(desk):
    """A strategy whose signals were mostly refused did not perform badly, it
    barely traded, and a UI showing only fills cannot tell those apart."""
    client, _a, _d = desk
    body = client.get("/refusals/swing").json()
    assert body["account"] == "swing"
    assert isinstance(body["refusals"], list)


def test_an_unlisted_symbol_is_a_404_naming_the_symbol_not_a_bare_500():
    """A symbol the venue does not list is the caller's mistake.

    An uncaught ccxt error produces 500 with an empty body, which says only
    that something went wrong somewhere in the engine. The status and the
    message should say which side the problem is on.
    """
    import ccxt

    class Unlisting(FakeCcxt):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            raise ccxt.BadSymbol(f"binance does not have market symbol {symbol}")

    client = TestClient(
        create_app(FrameStore(root=Path(tempfile.mkdtemp())), CryptoAdapter(client=Unlisting()))
    )
    response = client.get("/ohlcv", params={"symbol": "NOPE/USDT", "timeframe": "4h"})
    assert response.status_code == 404
    assert "NOPE/USDT" in response.json()["detail"]


def test_a_venue_outage_is_a_502_not_a_500():
    """The venue is upstream of this process, so its failure is a gateway
    failure. 502 tells a caller to retry rather than to file a bug."""
    import ccxt

    class Down(FakeCcxt):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            raise ccxt.NetworkError("connection reset")

    client = TestClient(
        create_app(FrameStore(root=Path(tempfile.mkdtemp())), CryptoAdapter(client=Down()))
    )
    response = client.get("/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "4h"})
    assert response.status_code == 502
    assert "NetworkError" in response.json()["detail"]


def test_a_slightly_stale_cache_is_topped_up_rather_than_refetched(tmp_path):
    """Full history on 4h is twenty thousand bars and twelve seconds to fetch.

    Refetching all of it when one bar closes spends twelve seconds to learn one
    bar. The top-up asks for a page, not a decade, and the test asserts on the
    size of the request rather than on wall-clock time.
    """
    import dataclasses
    from datetime import datetime, timedelta, timezone

    from desk.api.app import TOP_UP_BARS

    fake = FakeCcxt()
    adapter = CryptoAdapter(client=fake)
    cached = adapter.fetch_frame("BTC/USDT", "1h", 60)

    store = FrameStore(root=tmp_path / "frames")
    store.put(
        dataclasses.replace(
            cached,
            as_of=datetime.now(timezone.utc)
            - timedelta(seconds=cached.timeframe_seconds() * 4),
        )
    )
    fake.calls.clear()

    client = TestClient(create_app(store, adapter))
    response = client.get(
        "/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 50}
    )
    assert response.status_code == 200
    assert fake.calls, "nothing was fetched, so the stale cache was served"
    requested = [limit for _symbol, _tf, limit in fake.calls]
    assert all(
        limit is None or limit <= TOP_UP_BARS for limit in requested
    ), f"a full refetch was issued: {requested}"


def test_a_topped_up_bar_keeps_the_newly_fetched_version(tmp_path):
    """A bar still forming when it was cached has since been corrected.

    Keeping the cached copy of an overlapping timestamp pins the provisional
    high and low of a bar that has since closed, which is the same defect the
    paper engine holds its forming bar back to avoid.
    """
    import dataclasses
    from datetime import datetime, timedelta, timezone

    fake = FakeCcxt()
    adapter = CryptoAdapter(client=fake)
    cached = adapter.fetch_frame("BTC/USDT", "1h", 60)

    provisional = cached.data.copy()
    provisional.iloc[-1, provisional.columns.get_loc("high")] = 1.0
    store = FrameStore(root=tmp_path / "frames")
    store.put(
        dataclasses.replace(
            cached,
            data=provisional,
            as_of=datetime.now(timezone.utc)
            - timedelta(seconds=cached.timeframe_seconds() * 4),
        )
    )

    client = TestClient(create_app(store, adapter))
    body = client.get(
        "/ohlcv", params={"symbol": "BTC/USDT", "timeframe": "1h", "limit": 50}
    ).json()
    assert body["bars"][-1]["high"] != 1.0


def test_asking_for_more_bars_than_exist_is_not_a_permanent_cache_miss(tmp_path):
    """Regression: the metrics panel took sixty-seven seconds to fill.

    "Everything you have" is spelled as a number far larger than the listing,
    so `len(cached) >= limit` was false on every request and each one refetched
    the whole history. Twenty metrics meant twenty full downloads per page load.
    A fetch that comes back short is the venue saying it has no more, and that
    ceiling is remembered.
    """
    fake = FakeCcxt()  # supplies 60 bars
    adapter = CryptoAdapter(client=fake)
    store = FrameStore(root=tmp_path / "frames")
    client = TestClient(create_app(store, adapter))

    params = {"symbol": "BTC/USDT", "timeframe": "1h", "limit": 60000}
    first = client.get("/ohlcv", params=params)
    assert first.status_code == 200
    after_first = len(fake.calls)
    assert after_first > 0

    for _ in range(3):
        assert client.get("/ohlcv", params=params).status_code == 200

    assert len(fake.calls) == after_first, (
        "the venue was asked again for history it had already said was complete"
    )


def test_the_tail_counts_points_that_exist_not_bars(client):
    """Regression: sparse metrics showed a dash under tail=1.

    A pivot series is mostly empty, so trimming to the last bar before the NaN
    filter returns nothing at all, and the panel renders a dash for a metric
    with a perfectly good reading twenty bars back.
    """
    full = client.get(
        "/metric/swing_pivots", params={"symbol": "BTC/USDT", "timeframe": "1h"}
    ).json()
    if full["state"] != "ok":
        pytest.skip(f"swing_pivots unavailable on the fixture: {full.get('reason')}")

    tailed = client.get(
        "/metric/swing_pivots",
        params={"symbol": "BTC/USDT", "timeframe": "1h", "tail": 1},
    ).json()

    for key, points in full["series"].items():
        if not points:
            continue
        assert tailed["series"][key], f"{key} lost its last reading to the tail"
        assert tailed["series"][key][-1] == points[-1]
