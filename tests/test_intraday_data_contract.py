from phoenix_core.intraday_data_contract import (
    IntradayBar,
    IntradayMarketSnapshot,
    EventRiskSnapshot,
    KeyLevelSnapshot,
    contract_to_dict,
    validate_timestamp,
)


def test_contracts_are_point_in_time_and_serializable():
    bar = IntradayBar("2026-07-22T01:00:00Z", 1, 2, 0.5, 1.5, 1000)
    snap = IntradayMarketSnapshot("MU", bar.timestamp, bars=(bar,), rvol_tod=1.4)
    event = EventRiskSnapshot(bar.timestamp, earnings_days=None)
    level = KeyLevelSnapshot(100.0, strength=2.0, sources=("pivot",))
    assert validate_timestamp(bar.timestamp)
    assert contract_to_dict(snap)["bars"][0]["close"] == 1.5
    assert contract_to_dict(event)["earnings_days"] is None
    assert contract_to_dict(level)["sources"] == ("pivot",)


def test_non_utc_timestamp_is_rejected():
    assert not validate_timestamp("2026-07-22T01:00:00")
