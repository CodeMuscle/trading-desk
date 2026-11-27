"""On-demand CLI. The paper desk advances when invoked, never on its own.

Chosen over a daemon deliberately. The strategy belongs on 4h and 1d, where a bar
closes six times a day at most, so a process resident 24/7 to act six times is the
wrong shape. The failure mode decides it though: a daemon that dies quietly leaves
a gap in the journal that reads as "no signals", and after the fact that is
indistinguishable from a quiet market. An invocation that fails prints a non-zero
exit status at the moment it fails, to a scheduler that can be made to shout.

Bar processing is idempotent and the forming bar is held back, so running this
more often than the timeframe is free and running it late loses nothing. Both
facts are what make on-demand safe rather than merely simpler: cron every fifteen
minutes against a 4h chart does the right thing without configuration.

There is no order path here and no broker credential is read. The only writes are
to the local SQLite file.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from desk.adapters.crypto import CryptoAdapter
from desk.backtest.costs import CostModel
from desk.paper.account import AccountStore
from desk.paper.engine import PaperEngine
from desk.strategy.confluence import run as confluence

DEFAULT_DB = Path.home() / ".trading-desk" / "desk.sqlite"
WARMUP_BARS = 400
"""Enough history for the slowest gate in the strategy, which is a 200-period EMA,
plus room for ATR to seed. Fetching only the unprocessed bars would restart every
indicator at the window edge and silently change the signals."""


def _store(path: str | None) -> AccountStore:
    return AccountStore(Path(path) if path else DEFAULT_DB)


def cmd_accounts(args: argparse.Namespace) -> int:
    with _store(args.db) as store:
        names = store.names()
        if not names:
            print("no accounts. create one with: desk create <name> --equity 100000")
            return 0
        print(f"{'account':<16} {'venue':<12} {'equity':>14} {'open':>6} {'last bar':<26}")
        for name in names:
            record = store.load(name)
            assert record is not None
            last = record.last_bar.isoformat() if record.last_bar is not None else "-"
            print(
                f"{record.name:<16} {record.venue:<12} {record.equity:>14,.2f} "
                f"{'yes' if record.position else 'no':>6} {last:<26}"
            )
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    with _store(args.db) as store:
        try:
            record = store.create(
                args.name, venue=args.venue, starting_equity=args.equity
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"created {record.name} on {record.venue} at {record.equity:,.2f}")
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    with _store(args.db) as store:
        if store.load(args.account) is None:
            print(f"error: account {args.account!r} does not exist", file=sys.stderr)
            return 1

        try:
            frame = CryptoAdapter().fetch_frame(args.symbol, args.timeframe, WARMUP_BARS)
        except Exception as exc:  # noqa: BLE001 - the venue can fail any number of ways
            print(f"error: fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        signal = confluence(frame, use_candle=args.use_candle)
        if not signal.is_ok:
            print(f"no signal: {signal.reason or signal.error}", file=sys.stderr)
            return 3

        engine = PaperEngine(store, args.account, costs=CostModel())
        result = engine.advance(
            frame, signal.value.intents, backfill=args.backfill
        )
        if not result.is_ok:
            print(f"error: {result.error}", file=sys.stderr)
            return 4

        value = result.value
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(
            f"{now}  {args.account}  {args.symbol} {args.timeframe}  "
            f"applied {value.bars_processed}  skipped {value.bars_skipped}  "
            f"forming {value.bars_forming}  equity {value.equity:,.2f}  "
            f"open {'yes' if value.position_open else 'no'}"
        )
        if value.bars_seeded:
            print(
                f"  baseline set at the newest closed bar; {value.bars_seeded} bars "
                "of history were NOT traded, so this journal holds forward results "
                "only. Pass --backfill to replay history instead."
            )
        for fill in value.fills:
            net = fill.gross - fill.costs
            print(
                f"  FILL  {fill.side.name:<5} {fill.outcome:<12} "
                f"entry {fill.entry:,.2f} exit {fill.exit:,.2f} net {net:+,.2f}"
            )
        for timestamp, reason in value.refusals:
            print(f"  REFUSED {timestamp.isoformat()}  {reason}")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    with _store(args.db) as store:
        if store.load(args.account) is None:
            print(f"error: account {args.account!r} does not exist", file=sys.stderr)
            return 1
        journal = store.journal(args.account)
        if journal.empty:
            print("journal is empty")
            return 0
        journal["net"] = journal["gross"] - journal["costs"]
        columns = [
            "exit_index", "symbol", "side", "outcome", "entry", "exit",
            "net", "mae", "mfe", "equity_after", "lag_seconds",
        ]
        print(journal[columns].tail(args.limit).to_string(index=False))
        print()
        print(
            f"{len(journal)} trades  net {journal['net'].sum():+,.2f}  "
            f"win rate {(journal['net'] > 0).mean():.1%}  "
            f"refused {len(store.refusal_log(args.account))}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desk",
        description="Local paper trading desk. On demand: nothing runs in the "
        "background, and there is no order path.",
    )
    parser.add_argument("--db", help=f"SQLite path (default {DEFAULT_DB})")
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("accounts", help="list accounts").set_defaults(func=cmd_accounts)

    create = subs.add_parser("create", help="create an account")
    create.add_argument("name")
    create.add_argument("--venue", default="binance")
    create.add_argument("--equity", type=float, default=100_000.0)
    create.set_defaults(func=cmd_create)

    advance = subs.add_parser(
        "advance",
        help="fetch, compute signals, and apply every closed bar not yet seen",
    )
    advance.add_argument("account")
    advance.add_argument("--symbol", default="BTC/USDT")
    advance.add_argument("--timeframe", default="4h")
    advance.add_argument(
        "--backfill", action="store_true",
        help="on a new account, replay the fetched history instead of only "
        "setting the forward baseline. Writes a backtest into the paper journal, "
        "so the result is no longer selection-free.",
    )
    advance.add_argument(
        "--use-candle", action="store_true",
        help="keep the candle-pattern gate. Walk-forward preferred it off on 4h "
        "and 1d, so it is off by default.",
    )
    advance.set_defaults(func=cmd_advance)

    journal = subs.add_parser("journal", help="show the trade journal")
    journal.add_argument("account")
    journal.add_argument("--limit", type=int, default=20)
    journal.set_defaults(func=cmd_journal)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
