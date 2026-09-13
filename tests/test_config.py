import pytest

from chart_analyzer.config import ConfigError, parse_config


def valid_config():
    return {
        "mongodb": {"uri": "mongodb://localhost:27017", "database": "trading_automation"},
        "tickers": ["aapl"],
        "timeframes": [
            {
                "name": "daily",
                "interval": "1d",
                "period": "1y",
                "indicators": ["rsi"],
            }
        ],
        "indicators": {"rsi": {"window": 14}},
    }


def test_parse_config_normalizes_tickers_and_timeframes():
    config = parse_config(valid_config())

    assert config.tickers == ["AAPL"]
    assert config.timeframes[0].name == "daily"
    assert config.timeframes[0].indicators == ["rsi"]


def test_parse_config_requires_indicator_settings():
    raw = valid_config()
    raw["indicators"] = {}

    with pytest.raises(ConfigError, match="Missing settings"):
        parse_config(raw)


def test_parse_config_rejects_unknown_indicator():
    raw = valid_config()
    raw["timeframes"][0]["indicators"] = ["unknown"]

    with pytest.raises(ConfigError, match="Unsupported indicator"):
        parse_config(raw)


def test_parse_config_accepts_moving_average_bollinger_and_volume_at_price_indicators():
    raw = valid_config()
    raw["timeframes"][0]["indicators"] = [
        "ema_50",
        "ema_200",
        "volume_sma",
        "bollinger_bands",
        "volume_at_price",
        "price_stddev",
    ]
    raw["indicators"] = {
        "ema_50": {"window": 50},
        "ema_200": {"window": 200},
        "volume_sma": {"window": 20},
        "bollinger_bands": {"window": 20, "standard_deviations": 2},
        "volume_at_price": {"lookback": 60, "price_bins": 24},
        "price_stddev": {"periods": [20, 60]},
    }

    config = parse_config(raw)

    assert config.timeframes[0].indicators == [
        "ema_50",
        "ema_200",
        "volume_sma",
        "bollinger_bands",
        "volume_at_price",
        "price_stddev",
    ]


def test_parse_config_accepts_event_strategy():
    raw = valid_config()
    raw["events"] = {
        "position_entry": {
            "timeframe": "daily",
            "operator": "AND",
            "conditions": {
                "macd_cross_over": {},
                "macd_cross_under": {"enabled": False},
                "ema50_above_ema200": {},
                "rsi_between": {"min": 30, "max": 70},
                "volume_price_up": {"min_relative_volume": 0.9, "direction": "up"},
            },
        }
    }

    config = parse_config(raw)

    assert len(config.events) == 1
    assert config.events[0].name == "position_entry"
    assert config.events[0].operator == "AND"
    assert config.events[0].timeframe == "daily"
    assert "macd_cross_under" not in config.events[0].conditions


def test_parse_config_accepts_service_interval():
    raw = valid_config()
    raw["service"] = {
        "poll_interval_seconds": 60,
        "event_detection_interval_seconds": 120,
        "log_file": "logs/test.log",
    }

    config = parse_config(raw)

    assert config.service.poll_interval_seconds == 60
    assert config.service.event_detection_interval_seconds == 120
    assert config.service.log_file == "logs/test.log"


def test_parse_config_rejects_invalid_service_interval():
    raw = valid_config()
    raw["service"] = {"poll_interval_seconds": 0}

    with pytest.raises(ConfigError, match="service.poll_interval_seconds"):
        parse_config(raw)


def test_parse_config_allows_mongodb_environment_overrides(monkeypatch):
    monkeypatch.setenv("CHART_ANALYZER_MONGODB_URI", "mongodb://mongo:27017")
    monkeypatch.setenv("CHART_ANALYZER_MONGODB_DATABASE", "env_trading")
    monkeypatch.setenv("CHART_ANALYZER_MONGODB_SERVER_SELECTION_TIMEOUT_MS", "1234")

    config = parse_config(valid_config())

    assert config.mongodb.uri == "mongodb://mongo:27017"
    assert config.mongodb.database == "env_trading"
    assert config.mongodb.server_selection_timeout_ms == 1234


def test_parse_config_rejects_invalid_mongodb_timeout_environment(monkeypatch):
    monkeypatch.setenv("CHART_ANALYZER_MONGODB_SERVER_SELECTION_TIMEOUT_MS", "invalid")

    with pytest.raises(ConfigError, match="CHART_ANALYZER_MONGODB_SERVER_SELECTION_TIMEOUT_MS"):
        parse_config(valid_config())
