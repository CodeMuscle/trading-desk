from datetime import time

from desk.capabilities import AdapterInfo, Capability


def test_every_capability_a_later_market_needs_exists_today():
    names = {c.name for c in Capability}
    assert {
        "OHLCV", "AGGRESSOR_TICKS", "L2_DEPTH", "FUNDAMENTALS",
        "SESSION_GRID", "EXPIRING_SYMBOLS", "PARTICIPANT_OI", "BROKER_PAPER_API",
    } <= names


def test_adapter_info_holds_capabilities_as_an_immutable_set():
    info = AdapterInfo(
        venue="binance",
        capabilities=frozenset({Capability.OHLCV, Capability.L2_DEPTH}),
        session_open=None,
    )
    assert info.has(Capability.OHLCV)
    assert not info.has(Capability.AGGRESSOR_TICKS)
    assert isinstance(info.capabilities, frozenset)


def test_adapter_info_can_declare_a_session_grid_anchor():
    info = AdapterInfo(
        venue="nse",
        capabilities=frozenset({Capability.OHLCV, Capability.SESSION_GRID}),
        session_open=time(9, 15),
    )
    assert info.session_open == time(9, 15)
