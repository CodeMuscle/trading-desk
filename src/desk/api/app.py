"""Localhost read API. Serves series and registry metadata. Computes nothing.

Bound to loopback deliberately and permanently: a later milestone POSTs
indicator source from a browser editor into this process, and that path must
never be reachable from the network.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Query

from desk.adapters.crypto import CryptoAdapter
from desk.metrics.registry import REGISTRY
from desk.store.cache import FrameStore

HOST = "127.0.0.1"
PORT = 8787


def _points(series) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ts, value in series.items():
        if isinstance(value, float) and math.isnan(value):
            continue  # a non-pivot bar carries no point
        if isinstance(value, (bool, np.bool_)):
            value = bool(value)
        elif isinstance(value, str):
            pass
        else:
            value = float(value)
        out.append({"time": int(ts.timestamp()), "value": value})
    return out


def create_app(store: FrameStore, adapter: CryptoAdapter) -> FastAPI:
    app = FastAPI(title="Trading desk engine", version="0.1.0")

    def load(symbol: str, timeframe: str, limit: int):
        """Serve from cache, but only while the cache is current.

        The staleness check is the point. Without it a frame written once is
        returned for as long as it holds enough bars, so the chart serves prices
        from whenever the cache happened to be filled and keeps doing so
        indefinitely. That is exactly what was happening: a desk showing
        two-day-old candles under a live-looking header, with the engine
        correctly reporting stale on all eighteen metrics into a UI that drew it
        as a slightly dimmer colour.
        """
        frame = store.get(adapter.info().venue, symbol, timeframe)
        if (
            frame is None
            or len(frame.data) < limit
            or frame.is_stale(datetime.now(timezone.utc))
        ):
            frame = adapter.fetch_frame(symbol, timeframe, limit)
            store.put(frame)
        return frame

    @app.get("/health")
    def health() -> dict[str, Any]:
        info = adapter.info()
        return {
            "status": "ok",
            "host": HOST,
            "venue": info.venue,
            "capabilities": sorted(c.value for c in info.capabilities),
        }

    @app.get("/metrics")
    def metrics() -> dict[str, Any]:
        return {
            "metrics": [
                {
                    "name": name,
                    "requires": sorted(c.value for c in spec.requires),
                    "returns": spec.returns,
                    "defaults": spec.defaults,
                }
                for name, spec in sorted(REGISTRY.items())
            ]
        }

    @app.get("/ohlcv")
    def ohlcv(
        symbol: str = Query("BTC/USDT"),
        timeframe: str = Query("4h"),
        limit: int = Query(300, ge=1, le=6000),
    ) -> dict[str, Any]:
        frame = load(symbol, timeframe, limit)
        data = frame.data.tail(limit)
        return {
            "venue": frame.venue,
            "symbol": frame.symbol,
            "timeframe": frame.timeframe,
            "bars": [
                {
                    "time": int(ts.timestamp()),
                    "open": float(row.open),
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": float(row.volume),
                }
                for ts, row in data.iterrows()
            ],
        }

    @app.get("/metric/{name}")
    def metric(
        name: str,
        symbol: str = Query("BTC/USDT"),
        timeframe: str = Query("4h"),
        limit: int = Query(300, ge=1, le=6000),
        length: int | None = None,
        fast: int | None = None,
        slow: int | None = None,
        lookback: int | None = None,
    ) -> dict[str, Any]:
        spec = REGISTRY.get(name)
        if spec is None:
            raise HTTPException(
                status_code=404,
                detail=f"unknown metric {name!r}. Available: {', '.join(sorted(REGISTRY))}",
            )

        frame = load(symbol, timeframe, limit)

        missing = sorted(c.value for c in spec.requires if not frame.has(c))
        if missing:
            return {
                "name": name,
                "state": "unavailable",
                "reason": f"{frame.venue} does not provide: {', '.join(missing)}",
                "stale": False,
                "points": [],
            }

        kwargs = dict(spec.defaults)
        for key, supplied in (
            ("length", length), ("fast", fast), ("slow", slow), ("lookback", lookback)
        ):
            if supplied is not None and key in kwargs:
                kwargs[key] = supplied

        result = spec.fn(frame.tail(limit), **kwargs)

        if result.is_unavailable:
            return {"name": name, "state": "unavailable", "reason": result.reason,
                    "stale": False, "points": []}
        if result.is_error:
            return {"name": name, "state": "error", "reason": str(result.error),
                    "stale": False, "points": []}

        if spec.returns == "scalars":
            return {"name": name, "state": "ok", "stale": result.stale,
                    "scalars": result.value, "points": []}
        if spec.returns == "profile":
            return {"name": name, "state": "ok", "stale": result.stale,
                    "profile": [
                        {"price": float(p), "volume": float(v)}
                        for p, v in zip(result.value["price"], result.value["volume"])
                    ],
                    "poc": result.meta.get("poc"), "points": []}
        if spec.returns == "dict_of_series":
            return {"name": name, "state": "ok", "stale": result.stale,
                    "series": {k: _points(v) for k, v in result.value.items()},
                    "points": []}
        return {"name": name, "state": "ok", "stale": result.stale,
                "points": _points(result.value)}

    return app


def main() -> None:
    import uvicorn

    store = FrameStore(root="data/frames")
    adapter = CryptoAdapter()
    uvicorn.run(create_app(store, adapter), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
