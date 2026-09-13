from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import pandas as pd


_OUTPUT_INDEX: ContextVar[pd.Index | None] = ContextVar("indicator_output_index", default=None)


IndicatorFunction = Callable[[pd.DataFrame, dict[str, Any]], pd.DataFrame]


def compute_indicators(
    candles: pd.DataFrame,
    indicators: list[str],
    settings: dict[str, dict[str, Any]],
    output_index: pd.Index | None = None,
) -> pd.DataFrame:
    # Keep the entire input for EMA/RSI warm-up, but materialize only requested rows.
    token = _OUTPUT_INDEX.set(output_index)
    try:
        frames = []
        for indicator in indicators:
            result = INDICATORS[indicator](candles, settings.get(indicator, {}))
            if not result.empty:
                frames.append(result)
        if not frames:
            return pd.DataFrame(columns=["ticker", "timeframe", "timestamp", "indicator", "values"])
        return pd.concat(frames, ignore_index=True)
    finally:
        _OUTPUT_INDEX.reset(token)


def macd(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    fast = int(settings.get("fast", 12))
    slow = int(settings.get("slow", 26))
    signal_window = int(settings.get("signal", 9))

    close = candles["close"]
    macd_line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    signal_line = macd_line.ewm(span=signal_window, adjust=False).mean()
    histogram = macd_line - signal_line
    histogram_direction = _value_direction(histogram)

    return _records(
        candles,
        "macd",
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
            "histogram_direction": histogram_direction,
        },
    )


def rsi(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    window = int(settings.get("window", 14))
    delta = candles["close"].diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    average_loss = losses.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    relative_strength = average_gain / average_loss.replace(0, pd.NA)
    values = 100 - (100 / (1 + relative_strength))
    values = values.fillna(100).where(average_loss != 0, 100)
    values = values.where(average_gain != 0, 0)
    return _records(candles, "rsi", {"rsi": values})


def ema_50(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    window = int(settings.get("window", 50))
    values = candles["close"].ewm(span=window, adjust=False).mean()
    return _records(candles, "ema_50", {"ema": values, "window": _constant(candles, window)})


def ema_200(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    window = int(settings.get("window", 200))
    values = candles["close"].ewm(span=window, adjust=False).mean()
    return _records(candles, "ema_200", {"ema": values, "window": _constant(candles, window)})


def volume_sma(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    window = int(settings.get("window", 20))
    values = candles["volume"].rolling(window, min_periods=1).mean()
    return _records(candles, "volume_sma", {"sma": values, "window": _constant(candles, window)})


def bollinger_bands(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    window = int(settings.get("window", 20))
    standard_deviations = float(settings.get("standard_deviations", 2))
    middle = candles["close"].rolling(window, min_periods=1).mean()
    rolling_std = candles["close"].rolling(window, min_periods=1).std(ddof=0)
    upper = middle + (rolling_std * standard_deviations)
    lower = middle - (rolling_std * standard_deviations)
    bandwidth = (upper - lower) / middle.replace(0, pd.NA)

    return _records(
        candles,
        "bollinger_bands",
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "window": _constant(candles, window),
            "standard_deviations": _constant(candles, standard_deviations),
        },
    )


def volume_at_price(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    lookback = int(settings.get("lookback", 60))
    price_bins = int(settings.get("price_bins", 24))
    if lookback < 1:
        raise ValueError("volume_at_price.lookback must be at least 1.")
    if price_bins < 1:
        raise ValueError("volume_at_price.price_bins must be at least 1.")

    profiles = []
    point_of_control_prices = []
    point_of_control_volumes = []
    total_volumes = []

    typical_price = (candles["high"] + candles["low"] + candles["close"]) / 3
    selected = _OUTPUT_INDEX.get()
    positions = range(len(candles)) if selected is None else [i for i, label in enumerate(candles.index) if label in selected]
    for index in positions:
        start = max(0, index - lookback + 1)
        window_prices = typical_price.iloc[start : index + 1]
        window_volumes = candles["volume"].iloc[start : index + 1]
        profile = _volume_profile(window_prices, window_volumes, price_bins)
        profiles.append(profile["bins"])
        point_of_control_prices.append(profile["point_of_control_price"])
        point_of_control_volumes.append(profile["point_of_control_volume"])
        total_volumes.append(profile["total_volume"])

    candles = candles.iloc[list(positions)]
    return _records(
        candles,
        "volume_at_price",
        {
            "lookback": _constant(candles, lookback),
            "price_bins": _constant(candles, price_bins),
            "point_of_control_price": pd.Series(point_of_control_prices, index=candles.index),
            "point_of_control_volume": pd.Series(point_of_control_volumes, index=candles.index),
            "total_volume": pd.Series(total_volumes, index=candles.index),
            "bins": pd.Series(profiles, index=candles.index),
        },
    )


def price_stddev(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    periods = settings.get("periods", [20])
    if not isinstance(periods, list) or not periods:
        raise ValueError("price_stddev.periods must be a non-empty list.")

    normalized_periods = [int(period) for period in periods]
    if any(period < 1 for period in normalized_periods):
        raise ValueError("price_stddev.periods values must be at least 1.")

    ohlc4 = _price_series(candles, "ohlc4")
    hlcc4 = _price_series(candles, "hlcc4")
    ohlc4_stddev = {
        period: ohlc4.rolling(period, min_periods=1).std(ddof=0)
        for period in normalized_periods
    }
    hlcc4_stddev = {
        period: hlcc4.rolling(period, min_periods=1).std(ddof=0)
        for period in normalized_periods
    }
    values = []
    selected = _OUTPUT_INDEX.get()
    output_candles = candles if selected is None else candles.loc[candles.index.isin(selected)]
    for index in output_candles.index:
        period_values = {}
        for period in normalized_periods:
            period_values[str(period)] = {
                "ohlc4_stddev": _clean_value(ohlc4_stddev[period].loc[index]),
                "hlcc4_stddev": _clean_value(hlcc4_stddev[period].loc[index]),
            }
        values.append(period_values)

    candles = output_candles
    return _records(
        candles,
        "price_stddev",
        {
            "periods": pd.Series(values, index=candles.index),
        },
    )


def support_resistance(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    lookback = int(settings.get("lookback", 60))
    pivot_window = int(settings.get("pivot_window", 3))
    highs = candles["high"]
    lows = candles["low"]

    pivot_high = highs == highs.rolling(pivot_window * 2 + 1, center=True).max()
    pivot_low = lows == lows.rolling(pivot_window * 2 + 1, center=True).min()
    supports = lows.where(pivot_low)
    resistances = highs.where(pivot_high)

    nearest_support = supports.rolling(lookback, min_periods=1).max()
    nearest_resistance = resistances.rolling(lookback, min_periods=1).min()

    return _records(
        candles,
        "support_resistance",
        {
            "support": nearest_support,
            "resistance": nearest_resistance,
        },
    )


def volume_price_analysis(candles: pd.DataFrame, settings: dict[str, Any]) -> pd.DataFrame:
    volume_ma_window = int(settings.get("volume_ma_window", 20))
    volume_ma = candles["volume"].rolling(volume_ma_window, min_periods=1).mean()
    relative_volume = candles["volume"] / volume_ma.replace(0, pd.NA)
    price_change = candles["close"].diff()
    spread = candles["high"] - candles["low"]
    direction = pd.Series("flat", index=candles.index)
    direction.loc[candles["close"] > candles["open"]] = "up"
    direction.loc[candles["close"] < candles["open"]] = "down"
    classification = [
        _classify_volume_price(row_close, row_open, row_relative_volume)
        for row_close, row_open, row_relative_volume in zip(
            candles["close"], candles["open"], relative_volume, strict=True
        )
    ]

    return _records(
        candles,
        "volume_price_analysis",
        {
            "volume_ma": volume_ma,
            "relative_volume": relative_volume,
            "price_change": price_change,
            "spread": spread,
            "direction": direction,
            "classification": pd.Series(classification, index=candles.index),
        },
    )


def _records(candles: pd.DataFrame, indicator: str, values: dict[str, Any]) -> pd.DataFrame:
    selected = _OUTPUT_INDEX.get()
    index = candles.index if selected is None else candles.index[candles.index.isin(selected)]
    frame = candles.loc[index, ["ticker", "timeframe", "timestamp"]].copy()
    frame["indicator"] = indicator
    value_frame = pd.DataFrame(values, index=candles.index)
    frame["values"] = [
        {key: _clean_value(value) for key, value in row.items()}
        for row in value_frame.loc[index].to_dict(orient="records")
    ]
    return frame


def _constant(candles: pd.DataFrame, value: Any) -> pd.Series:
    return pd.Series([value] * len(candles), index=candles.index)


def _price_series(candles: pd.DataFrame, name: str) -> pd.Series:
    if name in candles:
        return candles[name]
    if name == "ohlc4":
        return (candles["open"] + candles["high"] + candles["low"] + candles["close"]) / 4
    if name == "hlcc4":
        return (candles["high"] + candles["low"] + candles["close"] + candles["close"]) / 4
    raise KeyError(name)


def _value_direction(values: pd.Series) -> pd.Series:
    difference = values.diff()
    return difference.apply(_direction_from_difference)


def _direction_from_difference(value: Any) -> str:
    if pd.isna(value):
        return "unknown"
    if value > 0:
        return "rising"
    if value < 0:
        return "falling"
    return "flat"


def _clean_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_value(item) for key, item in value.items()}
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _volume_profile(
    prices: pd.Series,
    volumes: pd.Series,
    price_bins: int,
) -> dict[str, Any]:
    minimum = float(prices.min())
    maximum = float(prices.max())
    total_volume = float(volumes.sum())

    if minimum == maximum:
        return {
            "bins": [
                {
                    "low": minimum,
                    "high": maximum,
                    "midpoint": minimum,
                    "volume": total_volume,
                }
            ],
            "point_of_control_price": minimum,
            "point_of_control_volume": total_volume,
            "total_volume": total_volume,
        }

    bin_width = (maximum - minimum) / price_bins
    bins = [
        {
            "low": minimum + (bin_width * bin_index),
            "high": minimum + (bin_width * (bin_index + 1)),
            "midpoint": minimum + (bin_width * (bin_index + 0.5)),
            "volume": 0.0,
        }
        for bin_index in range(price_bins)
    ]

    for price, volume in zip(prices, volumes, strict=True):
        bin_index = min(int((float(price) - minimum) / bin_width), price_bins - 1)
        bins[bin_index]["volume"] += float(volume)

    point_of_control = max(bins, key=lambda item: item["volume"])
    return {
        "bins": bins,
        "point_of_control_price": point_of_control["midpoint"],
        "point_of_control_volume": point_of_control["volume"],
        "total_volume": total_volume,
    }


def _candle_direction(row: pd.Series) -> str:
    if row["close"] > row["open"]:
        return "up"
    if row["close"] < row["open"]:
        return "down"
    return "flat"


def _classify_volume_price(close: float, open_: float, relative_volume: float | None) -> str:
    if pd.isna(relative_volume) or relative_volume < 1.5:
        return "normal_volume"
    if close > open_:
        return "high_volume_up"
    if close < open_:
        return "high_volume_down"
    return "high_volume_flat"


INDICATORS: dict[str, IndicatorFunction] = {
    "macd": macd,
    "rsi": rsi,
    "ema_50": ema_50,
    "ema_200": ema_200,
    "volume_sma": volume_sma,
    "bollinger_bands": bollinger_bands,
    "volume_at_price": volume_at_price,
    "price_stddev": price_stddev,
    "support_resistance": support_resistance,
    "volume_price_analysis": volume_price_analysis,
}
