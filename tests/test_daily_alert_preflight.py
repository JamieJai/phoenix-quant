"""Network-free checks for the 21:00 daily alert data-collection contract."""
import os

import telegram_daily_2100


def test_preflight_matches_2100_timer_and_keeps_cache_enabled(monkeypatch):
    keys = (
        "PHOENIX_DAILY_HOUR",
        "PHOENIX_INTRADAY_FEATURE_CACHE",
        "PHOENIX_DAILY_INTRADAY_OVERLAY",
    )
    saved = {key: os.environ.pop(key, None) for key in keys}
    monkeypatch.setattr(telegram_daily_2100, "load_env_file", lambda _: None)
    try:
        result = telegram_daily_2100.preflight()
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    assert result["status"] == "HEALTHY"
    assert result["effective_daily_hour_kst"] == 21
    assert result["intraday_feature_cache_enabled"] is True
    assert result["telegram_send_attempted"] is False
    assert result["market_data_requested"] is False
