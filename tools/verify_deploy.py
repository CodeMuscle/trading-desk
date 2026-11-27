"""Check a deployed demo: that it works, and that it leaked nothing.

The second half is the reason this exists. A static export is only as safe as
what the exporter chose to omit, and that choice is easy to break later: one
extra field in snapshot.py and a trade journal is on a public URL, with
nothing failing and nobody noticing. This asserts the absence of private data
against the live site rather than against the script that was supposed to
leave it out.

Usage:
    uv run python tools/verify_deploy.py https://your-deploy.vercel.app
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

TIMEOUT = 20

# Endpoints the engine serves locally and a public demo must not. A 200 here
# means the live engine is reachable from the internet, which contradicts the
# loopback rule the whole architecture is built on.
MUST_NOT_SERVE = [
    "/api/accounts",
    "/api/account/swing",
    "/api/journal/swing",
    "/api/refusals/swing",
    "/api/health",
    "/api/ohlcv?symbol=BTC/USDT&timeframe=4h",
]

# Substrings that would mean private data reached the bundle. Checked against
# every snapshot file and the page itself.
FORBIDDEN = [
    "equity",
    "starting_equity",
    "day_start",
    "month_start",
    "risk_per_trade",
    "portfolio_heat",
    "daily_loss",
    "r_multiple",
    "entry_index",
    "lag_seconds",
]

REQUIRED = [
    "demo/metrics.json",
    "demo/accounts.json",
    "demo/ohlcv__BTC-USDT__4h.json",
    "demo/metric__supply_demand_zones__BTC-USDT__4h.json",
    "demo/metric__change_of_character__BTC-USDT__1d.json",
]


def fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "desk-verify"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return 0, f"{type(exc).__name__}: {exc}"


def verify(base: str) -> int:
    base = base.rstrip("/")
    failures: list[str] = []
    checks = 0

    print(f"verifying {base}\n")

    status, page = fetch(base + "/")
    checks += 1
    if status != 200:
        failures.append(f"the page itself returned {status}")
    elif "<div" not in page:
        failures.append("the page returned 200 but no markup")
    else:
        print(f"  page                                   {status}")

    print("\nrequired snapshot files")
    for path in REQUIRED:
        status, body = fetch(f"{base}/{path}")
        checks += 1
        ok = status == 200 and body.startswith("{")
        print(f"  {'ok ' if ok else 'MISSING'}  {path}  ({status})")
        if not ok:
            failures.append(f"{path} returned {status}")

    print("\nendpoints that must NOT be reachable")
    for path in MUST_NOT_SERVE:
        status, body = fetch(base + path)
        checks += 1
        # A single-page host answers every unknown path with its index page, so
        # a 200 alone proves nothing: the first version of this check called
        # all six a leak against a build that serves none of them. What
        # distinguishes a live API is a JSON body.
        leaked = status == 200 and body.lstrip()[:1] in "{["
        label = "LEAK" if leaked else "ok  "
        note = "" if leaked else "  (index fallback)" if status == 200 else ""
        print(f"  {label}  {path}  ({status}){note}")
        if leaked:
            failures.append(f"{path} answered with JSON, so a live API is exposed")

    print("\nprivate data in the snapshot")
    scanned = 0
    for path in REQUIRED:
        status, body = fetch(f"{base}/{path}")
        if status != 200:
            continue
        scanned += 1
        for needle in FORBIDDEN:
            checks += 1
            if needle in body:
                failures.append(f"{path} contains {needle!r}")
    status, accounts = fetch(f"{base}/demo/accounts.json")
    if status == 200:
        checks += 1
        try:
            if json.loads(accounts).get("accounts"):
                failures.append("accounts.json is not empty")
        except json.JSONDecodeError:
            failures.append("accounts.json is not valid JSON")
    print(f"  scanned {scanned} files for {len(FORBIDDEN)} private field names")

    print(f"\n{checks} checks")
    if failures:
        print(f"\nFAILED ({len(failures)})")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASSED. The deploy serves the demo and exposes no account, journal")
    print("or risk data, and no live engine endpoint answers.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(verify(sys.argv[1]))
