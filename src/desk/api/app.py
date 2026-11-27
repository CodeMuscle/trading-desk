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
from desk.cli import DEFAULT_DB
from desk.paper.account import AccountRecord, AccountStore
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


def create_app(
    store: FrameStore,
    adapter: CryptoAdapter,
    accounts: AccountStore | None = None,
) -> FastAPI:
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

    # ---- paper desk, read only ----
    #
    # Read only is structural here, not a convention. There is no POST, PUT or
    # DELETE on this app at all, so no route exists that advances an account,
    # sizes a position or changes a limit. Advancing happens through the CLI,
    # where the actor is a person or their scheduler rather than anything that
    # reached the page. The chatbot milestone will consume these same endpoints,
    # and the tools it gets are the ones that exist.

    def _require(name: str) -> AccountRecord:
        if accounts is None:
            raise HTTPException(status_code=503, detail="no account store configured")
        record = accounts.load(name)
        if record is None:
            known = ", ".join(accounts.names()) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"unknown account {name!r}. Known accounts: {known}",
            )
        return record

    @app.get("/accounts")
    def account_list() -> dict[str, Any]:
        if accounts is None:
            return {"accounts": []}
        out = []
        for name in accounts.names():
            record = accounts.load(name)
            if record is None:
                continue
            out.append(
                {
                    "name": record.name,
                    "venue": record.venue,
                    "equity": record.equity,
                    "starting_equity": record.starting_equity,
                    "return_pct": (record.equity / record.starting_equity - 1.0) * 100.0,
                    "position_open": record.position is not None,
                    "last_bar": record.last_bar.isoformat()
                    if record.last_bar is not None
                    else None,
                }
            )
        return {"accounts": out}

    @app.get("/account/{name}")
    def account_detail(name: str) -> dict[str, Any]:
        record = _require(name)
        position = None
        if record.position is not None:
            held = record.position
            position = {
                "entry_index": held.entry_index.isoformat(),
                "side": held.side.name,
                "units": held.units,
                "entry": held.entry,
                "stop": held.stop,
                "target": held.target,
                "mae": held.mae,
                "mfe": held.mfe,
            }
        return {
            "name": record.name,
            "venue": record.venue,
            "equity": record.equity,
            "starting_equity": record.starting_equity,
            "return_pct": (record.equity / record.starting_equity - 1.0) * 100.0,
            "day_start": record.day_start,
            "month_start": record.month_start,
            "last_bar": record.last_bar.isoformat()
            if record.last_bar is not None
            else None,
            "position": position,
        }

    @app.get("/journal/{name}")
    def journal(name: str, limit: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
        _require(name)
        assert accounts is not None
        frame = accounts.journal(name).tail(limit)
        trades = []
        for row in frame.to_dict("records"):
            net = float(row["gross"]) - float(row["costs"])
            risk = abs(float(row["entry"]) - float(row["stop"])) * float(row["units"])
            trades.append(
                {
                    "entry_index": row["entry_index"],
                    "exit_index": row["exit_index"],
                    "symbol": row["symbol"],
                    "side": row["side"],
                    "outcome": row["outcome"],
                    "entry": float(row["entry"]),
                    "exit": float(row["exit"]),
                    "stop": float(row["stop"]),
                    "target": float(row["target"]),
                    "net": net,
                    # R is the unit a trader reasons in. Currency hides whether a
                    # loss was the planned one or a stop that did not hold.
                    "r_multiple": net / risk if risk else 0.0,
                    "mae": float(row["mae"]),
                    "mfe": float(row["mfe"]),
                    "equity_after": float(row["equity_after"]),
                    "lag_seconds": row["lag_seconds"],
                }
            )
        wins = [t for t in trades if t["net"] > 0]
        return {
            "account": name,
            "trades": trades,
            "summary": {
                "count": len(trades),
                "net": sum(t["net"] for t in trades),
                "win_rate": len(wins) / len(trades) if trades else None,
                "expectancy_r": (
                    sum(t["r_multiple"] for t in trades) / len(trades)
                    if trades
                    else None
                ),
            },
        }

    @app.get("/refusals/{name}")
    def refusals(name: str, limit: int = Query(200, ge=1, le=5000)) -> dict[str, Any]:
        """Why the desk did not trade.

        Its own endpoint rather than a field on the journal, because a strategy
        whose signals were mostly refused did not perform badly, it barely
        traded, and a UI that shows only fills cannot tell those apart.
        """
        _require(name)
        assert accounts is not None
        frame = accounts.refusal_log(name).tail(limit)
        return {
            "account": name,
            "refusals": [
                {"bar_index": row["bar_index"], "symbol": row["symbol"],
                 "reason": row["reason"]}
                for row in frame.to_dict("records")
            ],
        }

    return app


def main() -> None:
    import uvicorn

    store = FrameStore(root="data/frames")
    adapter = CryptoAdapter()
    # Same database the CLI writes, so the page shows the account the scheduler
    # is advancing rather than a second copy of it.
    accounts = AccountStore(DEFAULT_DB)
    uvicorn.run(create_app(store, adapter, accounts), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
