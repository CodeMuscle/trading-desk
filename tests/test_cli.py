"""CLI tests. Exit codes matter more than output here.

The desk runs on demand, likely from cron, and cron's only channel for "this
failed" is the exit status. A command that prints an error and exits 0 is a
scheduled job that fails silently, which is the failure mode the daemon was
rejected for in the first place.
"""

from __future__ import annotations

import pytest

from desk.cli import main


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "desk.sqlite")


def test_create_then_list(db, capsys):
    assert main(["--db", db, "create", "swing", "--equity", "25000"]) == 0
    assert "created swing" in capsys.readouterr().out

    assert main(["--db", db, "accounts"]) == 0
    out = capsys.readouterr().out
    assert "swing" in out
    assert "25,000.00" in out


def test_listing_with_no_accounts_explains_how_to_make_one(db, capsys):
    assert main(["--db", db, "accounts"]) == 0
    assert "create one with" in capsys.readouterr().out


def test_a_duplicate_account_exits_nonzero(db, capsys):
    main(["--db", db, "create", "swing"])
    capsys.readouterr()
    assert main(["--db", db, "create", "swing"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_advancing_an_unknown_account_exits_nonzero_without_fetching(db, capsys):
    """Checked before the network call, so a typo does not spend a fetch."""
    assert main(["--db", db, "advance", "ghost"]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_a_fetch_failure_exits_nonzero(db, capsys, monkeypatch):
    main(["--db", db, "create", "swing"])
    capsys.readouterr()

    def boom(self, *a, **k):
        raise ConnectionError("venue unreachable")

    monkeypatch.setattr("desk.cli.CryptoAdapter.fetch_frame", boom)
    assert main(["--db", db, "advance", "swing"]) == 2
    err = capsys.readouterr().err
    assert "fetch failed" in err
    assert "venue unreachable" in err


def test_journal_on_an_unknown_account_exits_nonzero(db, capsys):
    assert main(["--db", db, "journal", "ghost"]) == 1
    assert "does not exist" in capsys.readouterr().err


def test_an_empty_journal_is_not_an_error(db, capsys):
    main(["--db", db, "create", "swing"])
    capsys.readouterr()
    assert main(["--db", db, "journal", "swing"]) == 0
    assert "journal is empty" in capsys.readouterr().out


def test_the_candle_gate_is_off_unless_asked_for(db):
    """Walk-forward preferred it off on 4h and 1d, so the default follows the
    evidence rather than the original Pine script."""
    from desk.cli import build_parser

    parsed = build_parser().parse_args(["advance", "swing"])
    assert parsed.use_candle is False
    assert build_parser().parse_args(["advance", "swing", "--use-candle"]).use_candle


def test_a_subcommand_is_required(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code != 0
