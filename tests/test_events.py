import pandas as pd

from chart_analyzer.config import EventStrategyConfig
from chart_analyzer.events import detect_events_for_candles, evaluate_event_strategy, required_indicators


def strategy(operator="AND"):
    return EventStrategyConfig(
        name="position_entry",
        timeframe="daily",
        operator=operator,
        conditions={
            "macd_cross_over": {},
            "ema50_above_ema200": {},
            "rsi_between": {"min": 30, "max": 70},
            "volume_price_up": {"min_relative_volume": 0.9, "direction": "up"},
        },
    )


def cross_under_strategy():
    return EventStrategyConfig(
        name="position_exit",
        timeframe="daily",
        operator="OR",
        conditions={
            "macd_cross_under": {},
        },
    )


def candle(timestamp, macd, signal, ema50=120, ema200=100, rsi=55, relative_volume=1.1, direction="up"):
    return {
        "ticker": "AAPL",
        "timeframe": "daily",
        "timestamp": pd.Timestamp(timestamp, tz="UTC").to_pydatetime(),
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 100.0,
        "indicators": {
            "macd": {"macd": macd, "signal": signal},
            "ema_50": {"ema": ema50},
            "ema_200": {"ema": ema200},
            "rsi": {"rsi": rsi},
            "volume_price_analysis": {
                "relative_volume": relative_volume,
                "direction": direction,
            },
        },
    }


def test_required_indicators_for_strategy():
    assert required_indicators(strategy()) == [
        "macd",
        "ema_50",
        "ema_200",
        "rsi",
        "volume_price_analysis",
    ]


def test_evaluate_event_strategy_detects_and_entry():
    previous = candle("2024-01-01", macd=0.5, signal=0.7)
    current = candle("2024-01-02", macd=0.8, signal=0.7)

    event = evaluate_event_strategy(current, previous, strategy())

    assert event["triggered"] is True
    assert event["conditions"] == {
        "macd_cross_over": True,
        "ema50_above_ema200": True,
        "rsi_between": True,
        "volume_price_up": True,
    }


def test_evaluate_event_strategy_detects_macd_cross_under():
    previous = candle("2024-01-01", macd=0.8, signal=0.7)
    current = candle("2024-01-02", macd=0.6, signal=0.7)

    event = evaluate_event_strategy(current, previous, cross_under_strategy())

    assert event["triggered"] is True
    assert event["conditions"] == {"macd_cross_under": True}


def test_evaluate_event_strategy_supports_or_operator():
    previous = candle("2024-01-01", macd=0.8, signal=0.7)
    current = candle(
        "2024-01-02",
        macd=0.6,
        signal=0.7,
        ema50=90,
        ema200=100,
        rsi=80,
        relative_volume=0.5,
        direction="down",
    )
    current["indicators"]["rsi"]["rsi"] = 55

    event = evaluate_event_strategy(current, previous, strategy(operator="OR"))

    assert event["triggered"] is True
    assert event["conditions"]["rsi_between"] is True
    assert event["conditions"]["macd_cross_over"] is False


def test_detect_events_for_candles_returns_only_triggered_events():
    candles = [
        candle("2024-01-01", macd=0.5, signal=0.7),
        candle("2024-01-02", macd=0.8, signal=0.7),
        candle("2024-01-03", macd=0.9, signal=0.7),
    ]

    events = detect_events_for_candles(candles, strategy())

    assert len(events) == 1
    assert events[0]["timestamp"] == candles[1]["timestamp"]
