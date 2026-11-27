"""Freeze a slice of the live engine into static JSON for a public demo.

The desk's API binds loopback and always will: a later milestone POSTs
indicator source into this Python process, and that path must never be
network-reachable. A hosted page therefore cannot call the engine, so a demo
has to carry its data with it.

What this deliberately does NOT export: accounts, equity, the trade journal,
the refusal log, and the risk limits. Those are the private half of the desk,
and a public URL is exactly the wrong place for a record of what someone traded
and how much they risked. The demo shows the chart and the metrics; the paper
desk renders its empty state, which is honest rather than hidden.

Run with the engine stopped or running, it does not matter: this talks to the
adapter directly rather than over HTTP.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from desk.adapters.crypto import CryptoAdapter
from desk.metrics.registry import REGISTRY

OUT = Path("web/public/demo")
SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAMES = ["1h", "4h", "1d"]
BARS = 1500
"""Smaller than the desk's own default. 20,000 bars per symbol per timeframe is
a 60MB payload for a page meant to load over the public internet; 1500 is eight
months on 4h, which is enough to show what the desk does."""


def _clean(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _points(series, tail: int | None = None) -> list[dict[str, Any]]:
    out = []
    for ts, value in series.items():
        if isinstance(value, float) and math.isnan(value):
            continue
        out.append(
            {
                "time": int(ts.timestamp()),
                "value": bool(value) if isinstance(value, bool) else _clean(value),
            }
        )
    return out[-tail:] if tail else out


def snapshot() -> None:
    adapter = CryptoAdapter()
    OUT.mkdir(parents=True, exist_ok=True)

    registry = [
        {
            "name": name,
            "requires": sorted(c.value for c in spec.requires),
            "returns": spec.returns,
            "defaults": spec.defaults,
        }
        for name, spec in sorted(REGISTRY.items())
    ]
    (OUT / "metrics.json").write_text(json.dumps({"metrics": registry}))
    (OUT / "accounts.json").write_text(json.dumps({"accounts": []}))

    for symbol in SYMBOLS:
        for timeframe in TIMEFRAMES:
            frame = adapter.fetch_frame(symbol, timeframe, BARS)
            slug = f"{symbol.replace('/', '-')}__{timeframe}"
            data = frame.data

            (OUT / f"ohlcv__{slug}.json").write_text(
                json.dumps(
                    {
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
                )
            )

            for name, spec in REGISTRY.items():
                result = spec.fn(frame, **spec.defaults)
                body: dict[str, Any] = {"name": name, "stale": False, "points": []}
                if not result.is_ok:
                    body["state"] = result.state.value
                    body["reason"] = result.reason or str(result.error)
                else:
                    body["state"] = "ok"
                    if spec.returns == "scalars":
                        body["scalars"] = {k: _clean(v) for k, v in result.value.items()}
                    elif spec.returns == "profile":
                        body["profile"] = [
                            {"price": float(p), "volume": float(v)}
                            for p, v in zip(
                                result.value["price"], result.value["volume"]
                            )
                        ]
                        body["poc"] = _clean(result.meta.get("poc"))
                    elif spec.returns == "zones":
                        body["zones"] = [
                            {
                                "kind": z.kind, "top": z.top, "bottom": z.bottom,
                                "start": int(z.start_index.timestamp()),
                                "end": int(z.end_index.timestamp()),
                                "departure_atr": z.departure_atr,
                                "base_bars": z.base_bars, "tested": z.tested,
                                "age_bars": z.age_bars,
                            }
                            for z in result.value
                        ]
                        body["fresh"] = result.meta.get("fresh")
                    elif spec.returns == "dict_of_series":
                        body["series"] = {
                            k: _points(v, 1) for k, v in result.value.items()
                        }
                    else:
                        body["points"] = _points(result.value, 1)
                (OUT / f"metric__{name}__{slug}.json").write_text(json.dumps(body))

            # The overlays are drawn as lines, so they need the whole series
            # rather than a single reading.
            for length in (20, 50, 200):
                result = REGISTRY["ema"].fn(frame, length=length)
                (OUT / f"ema{length}__{slug}.json").write_text(
                    json.dumps(
                        {"name": "ema", "state": "ok", "stale": False,
                         "points": _points(result.value)}
                    )
                )
            vwap = REGISTRY["session_vwap"].fn(frame)
            (OUT / f"vwap__{slug}.json").write_text(
                json.dumps(
                    {"name": "session_vwap", "state": "ok", "stale": False,
                     "points": _points(vwap.value)}
                )
            )
            print(f"  {symbol} {timeframe}: {len(data)} bars")

    total = sum(f.stat().st_size for f in OUT.glob("*.json"))
    print(f"\n{len(list(OUT.glob('*.json')))} files, {total / 1_000_000:.1f} MB")
    print("No accounts, journal, refusals or risk limits were exported.")


if __name__ == "__main__":
    snapshot()
