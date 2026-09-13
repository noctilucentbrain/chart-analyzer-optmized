import pandas as pd

from chart_analyzer.indicators import compute_indicators


def candles():
    dates = pd.date_range("2024-01-01", periods=40, freq="D", tz="UTC")
    close = pd.Series(range(100, 140), dtype="float")
    return pd.DataFrame(
        {
            "ticker": "AAPL",
            "timeframe": "daily",
            "timestamp": dates,
            "open": close - 1,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": [100 + index * 10 for index in range(40)],
        }
    )


def test_compute_indicators_returns_expected_indicator_records():
    result = compute_indicators(
        candles(),
        [
            "macd",
            "rsi",
            "ema_50",
            "ema_200",
            "volume_sma",
            "bollinger_bands",
            "volume_at_price",
            "price_stddev",
            "support_resistance",
            "volume_price_analysis",
        ],
        {
            "macd": {"fast": 12, "slow": 26, "signal": 9},
            "rsi": {"window": 14},
            "ema_50": {"window": 50},
            "ema_200": {"window": 200},
            "volume_sma": {"window": 5},
            "bollinger_bands": {"window": 5, "standard_deviations": 2},
            "volume_at_price": {"lookback": 10, "price_bins": 4},
            "price_stddev": {"periods": [5, 20]},
            "support_resistance": {"lookback": 10, "pivot_window": 2},
            "volume_price_analysis": {"volume_ma_window": 5},
        },
    )

    assert set(result["indicator"]) == {
        "macd",
        "rsi",
        "ema_50",
        "ema_200",
        "volume_sma",
        "bollinger_bands",
        "volume_at_price",
        "price_stddev",
        "support_resistance",
        "volume_price_analysis",
    }
    assert len(result) == 400

    macd_values = result[result["indicator"] == "macd"].iloc[-1]["values"]
    assert set(macd_values) == {"macd", "signal", "histogram", "histogram_direction"}
    assert macd_values["histogram_direction"] in {"rising", "falling", "flat", "unknown"}

    ema_values = result[result["indicator"] == "ema_50"].iloc[-1]["values"]
    assert set(ema_values) == {"ema", "window"}

    volume_sma_values = result[result["indicator"] == "volume_sma"].iloc[-1]["values"]
    assert set(volume_sma_values) == {"sma", "window"}

    bollinger_values = result[result["indicator"] == "bollinger_bands"].iloc[-1]["values"]
    assert set(bollinger_values) == {
        "middle",
        "upper",
        "lower",
        "bandwidth",
        "window",
        "standard_deviations",
    }

    volume_at_price_values = result[result["indicator"] == "volume_at_price"].iloc[-1]["values"]
    assert set(volume_at_price_values) == {
        "lookback",
        "price_bins",
        "point_of_control_price",
        "point_of_control_volume",
        "total_volume",
        "bins",
    }
    assert len(volume_at_price_values["bins"]) == 4
    assert volume_at_price_values["lookback"] == 10

    price_stddev_values = result[result["indicator"] == "price_stddev"].iloc[-1]["values"]
    assert set(price_stddev_values) == {"periods"}
    assert set(price_stddev_values["periods"]) == {"5", "20"}
    assert set(price_stddev_values["periods"]["5"]) == {"ohlc4_stddev", "hlcc4_stddev"}

    vpa_values = result[result["indicator"] == "volume_price_analysis"].iloc[-1]["values"]
    assert set(vpa_values) == {
        "volume_ma",
        "relative_volume",
        "price_change",
        "spread",
        "direction",
        "classification",
    }
