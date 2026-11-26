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
