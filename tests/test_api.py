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
