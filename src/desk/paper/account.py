"""Accounts and their persistence. SQLite, one file, single user, no auth.

The state here is not a convenience cache. It is the account, and losing a field
of it changes what the risk engine decides on the next bar. Two fields matter
more than the rest and are the reason this module exists rather than a JSON dump:

`day_start` and `month_start` are the baselines the daily and monthly loss limits
measure against. If a restart re-seeds them from current equity, then restarting
the process after a bad morning resets the daily loss limit, and the kill switch
becomes advisory. A limit that a restart clears is not a limit, which is the same
rule that keeps risk out of agent prompts.

`last_bar` is what makes bar processing idempotent. A live poller re-fetches
overlapping windows by design, and a simulator that processes the same bar twice
charges commission twice and resolves a stop twice.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from desk.paper.simulator import Fill, OpenPosition
from desk.strategy.base import Side

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    name            TEXT PRIMARY KEY,
    venue           TEXT NOT NULL,
    starting_equity REAL NOT NULL,
    equity          REAL NOT NULL,
    day_start       REAL NOT NULL,
    month_start     REAL NOT NULL,
    current_day     TEXT,
    current_month   TEXT,
    last_bar        TEXT,
    position        TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account         TEXT NOT NULL REFERENCES accounts(name) ON DELETE CASCADE,
    symbol          TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    entry_index     TEXT NOT NULL,
    exit_index      TEXT NOT NULL,
    side            TEXT NOT NULL,
    units           REAL NOT NULL,
    entry           REAL NOT NULL,
    exit            REAL NOT NULL,
    stop            REAL NOT NULL,
    target          REAL NOT NULL,
    outcome         TEXT NOT NULL,
    gross           REAL NOT NULL,
    costs           REAL NOT NULL,
    mae             REAL NOT NULL,
    mfe             REAL NOT NULL,
    equity_after    REAL NOT NULL,
    lag_seconds     REAL,
    recorded_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refusals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account         TEXT NOT NULL REFERENCES accounts(name) ON DELETE CASCADE,
    symbol          TEXT NOT NULL,
    bar_index       TEXT NOT NULL,
    reason          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS journal_account ON journal(account, exit_index);
CREATE INDEX IF NOT EXISTS refusals_account ON refusals(account, bar_index);
"""

MAX_ACCOUNTS = 5
"""Spec'd ceiling. Not a performance limit: this is a single-user desk, and an
account list long enough to scroll is a list whose risk you are not tracking."""


@dataclass(frozen=True, slots=True)
class AccountRecord:
    name: str
    venue: str
    starting_equity: float
    equity: float
    day_start: float
    month_start: float
    current_day: date | None
    current_month: tuple[int, int] | None
    last_bar: pd.Timestamp | None
    position: OpenPosition | None


def _dump_position(position: OpenPosition | None) -> str | None:
    if position is None:
        return None
    return json.dumps(
        {
            "entry_index": position.entry_index.isoformat(),
            "side": position.side.name,
            "units": position.units,
            "entry": position.entry,
            "stop": position.stop,
            "target": position.target,
            "entry_commission": position.entry_commission,
            "mae": position.mae,
            "mfe": position.mfe,
        }
    )


def _load_position(raw: str | None) -> OpenPosition | None:
    if not raw:
        return None
    d = json.loads(raw)
    return OpenPosition(
        entry_index=pd.Timestamp(d["entry_index"]),
        side=Side[d["side"]],
        units=d["units"],
        entry=d["entry"],
        stop=d["stop"],
        target=d["target"],
        entry_commission=d["entry_commission"],
        mae=d["mae"],
        mfe=d["mfe"],
    )


class AccountStore:
    """SQLite-backed account storage.

    Writes are committed per call rather than batched. A paper desk processes a
    handful of bars a minute at most, so there is nothing to gain from batching
    and a crash between a fill and its commit would lose a trade that the equity
    already reflects.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> AccountStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def create(self, name: str, *, venue: str, starting_equity: float) -> AccountRecord:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        existing = self.names()
        if name in existing:
            raise ValueError(f"account {name!r} already exists")
        if len(existing) >= MAX_ACCOUNTS:
            raise ValueError(
                f"{MAX_ACCOUNTS} accounts is the ceiling; delete one rather than "
                "raising it, because a list long enough to scroll is a list whose "
                "risk you are not tracking"
            )
        self.conn.execute(
            "INSERT INTO accounts (name, venue, starting_equity, equity, day_start,"
            " month_start, current_day, current_month, last_bar, position, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?)",
            (
                name, venue, starting_equity, starting_equity, starting_equity,
                starting_equity, datetime.now(timezone.utc).isoformat(),
            ),
        )
        loaded = self.load(name)
        assert loaded is not None
        return loaded

    def names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM accounts ORDER BY name").fetchall()
        return [r[0] for r in rows]

    def load(self, name: str) -> AccountRecord | None:
        row = self.conn.execute(
            "SELECT name, venue, starting_equity, equity, day_start, month_start,"
            " current_day, current_month, last_bar, position"
            " FROM accounts WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        month = None
        if row[7]:
            year, mon = row[7].split("-")
            month = (int(year), int(mon))
        return AccountRecord(
            name=row[0], venue=row[1], starting_equity=row[2], equity=row[3],
            day_start=row[4], month_start=row[5],
            current_day=date.fromisoformat(row[6]) if row[6] else None,
            current_month=month,
            last_bar=pd.Timestamp(row[8]) if row[8] else None,
            position=_load_position(row[9]),
        )

    def save(self, record: AccountRecord) -> None:
        month = (
            f"{record.current_month[0]}-{record.current_month[1]:02d}"
            if record.current_month
            else None
        )
        updated = self.conn.execute(
            "UPDATE accounts SET equity = ?, day_start = ?, month_start = ?,"
            " current_day = ?, current_month = ?, last_bar = ?, position = ?"
            " WHERE name = ?",
            (
                record.equity, record.day_start, record.month_start,
                record.current_day.isoformat() if record.current_day else None,
                month,
                record.last_bar.isoformat() if record.last_bar is not None else None,
                _dump_position(record.position),
                record.name,
            ),
        ).rowcount
        if not updated:
            raise ValueError(f"account {record.name!r} does not exist; nothing saved")

    def delete(self, name: str) -> None:
        self.conn.execute("DELETE FROM accounts WHERE name = ?", (name,))

    def record_fill(
        self,
        account: str,
        fill: Fill,
        *,
        symbol: str,
        timeframe: str,
        equity_after: float,
        lag_seconds: float | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO journal (account, symbol, timeframe, entry_index, exit_index,"
            " side, units, entry, exit, stop, target, outcome, gross, costs, mae, mfe,"
            " equity_after, lag_seconds, recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                account, symbol, timeframe,
                fill.entry_index.isoformat(), fill.exit_index.isoformat(),
                fill.side.name, fill.units, fill.entry, fill.exit, fill.stop,
                fill.target, fill.outcome, fill.gross, fill.costs, fill.mae, fill.mfe,
                equity_after, lag_seconds, datetime.now(timezone.utc).isoformat(),
            ),
        )

    def record_refusal(
        self, account: str, *, symbol: str, bar_index: pd.Timestamp, reason: str
    ) -> None:
        self.conn.execute(
            "INSERT INTO refusals (account, symbol, bar_index, reason) VALUES (?,?,?,?)",
            (account, symbol, bar_index.isoformat(), reason),
        )

    def journal(self, account: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM journal WHERE account = ? ORDER BY exit_index, id",
            self.conn, params=(account,),
        )

    def refusal_log(self, account: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM refusals WHERE account = ? ORDER BY bar_index, id",
            self.conn, params=(account,),
        )
