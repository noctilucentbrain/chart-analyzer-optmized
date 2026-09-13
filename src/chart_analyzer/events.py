from __future__ import annotations

from typing import Any

from chart_analyzer.config import EventStrategyConfig


CONDITION_INDICATORS = {
    "macd_cross_over": ["macd"],
    "macd_cross_under": ["macd"],
    "ema50_above_ema200": ["ema_50", "ema_200"],
    "rsi_between": ["rsi"],
    "volume_price_up": ["volume_price_analysis"],
}


def required_indicators(strategy: EventStrategyConfig) -> list[str]:
    indicators = []
    for condition in strategy.conditions:
        for indicator in CONDITION_INDICATORS[condition]:
            if indicator not in indicators:
                indicators.append(indicator)
    return indicators


def evaluate_event_strategy(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    strategy: EventStrategyConfig,
) -> dict[str, Any]:
    condition_results = {
        condition: CONDITION_EVALUATORS[condition](
            current,
            previous,
            settings,
        )
        for condition, settings in strategy.conditions.items()
    }
    values = list(condition_results.values())
    triggered = all(values) if strategy.operator == "AND" else any(values)

    return {
        "strategy": strategy.name,
        "operator": strategy.operator,
        "ticker": current["ticker"],
        "timeframe": current["timeframe"],
        "timestamp": current["timestamp"],
        "triggered": triggered,
        "conditions": condition_results,
    }


def detect_events_for_candles(
    candles: list[dict[str, Any]],
    strategy: EventStrategyConfig,
) -> list[dict[str, Any]]:
    events = []
    previous_by_series: dict[tuple[str, str], dict[str, Any]] = {}

    for candle in sorted(candles, key=lambda item: (item["ticker"], item["timeframe"], item["timestamp"])):
        series_key = (candle["ticker"], candle["timeframe"])
        previous = previous_by_series.get(series_key)
        event = evaluate_event_strategy(candle, previous, strategy)
        if event["triggered"]:
            events.append(event)
        previous_by_series[series_key] = candle

    return events


def _macd_cross_over(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    settings: dict[str, Any],
) -> bool:
    if previous is None:
        return False

    current_macd = _indicator_values(current, "macd")
    previous_macd = _indicator_values(previous, "macd")
    return (
        _number(previous_macd.get("macd")) <= _number(previous_macd.get("signal"))
        and _number(current_macd.get("macd")) > _number(current_macd.get("signal"))
    )


def _macd_cross_under(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    settings: dict[str, Any],
) -> bool:
    if previous is None:
        return False

    current_macd = _indicator_values(current, "macd")
    previous_macd = _indicator_values(previous, "macd")
    return (
        _number(previous_macd.get("macd")) >= _number(previous_macd.get("signal"))
        and _number(current_macd.get("macd")) < _number(current_macd.get("signal"))
    )


def _ema50_above_ema200(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    settings: dict[str, Any],
) -> bool:
    ema_50 = _indicator_values(current, "ema_50")
    ema_200 = _indicator_values(current, "ema_200")
    return _number(ema_50.get("ema")) > _number(ema_200.get("ema"))


def _rsi_between(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    settings: dict[str, Any],
) -> bool:
    minimum = float(settings.get("min", 30))
    maximum = float(settings.get("max", 70))
    value = _number(_indicator_values(current, "rsi").get("rsi"))
    return minimum <= value <= maximum


def _volume_price_up(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    settings: dict[str, Any],
) -> bool:
    minimum_relative_volume = float(settings.get("min_relative_volume", 0.9))
    expected_direction = str(settings.get("direction", "up")).lower()
    values = _indicator_values(current, "volume_price_analysis")
    return (
        _number(values.get("relative_volume")) > minimum_relative_volume
        and str(values.get("direction", "")).lower() == expected_direction
    )


def _indicator_values(candle: dict[str, Any], indicator: str) -> dict[str, Any]:
    return candle.get("indicators", {}).get(indicator, {})


def _number(value: Any) -> float:
    if value is None:
        return float("nan")
    return float(value)


CONDITION_EVALUATORS = {
    "macd_cross_over": _macd_cross_over,
    "macd_cross_under": _macd_cross_under,
    "ema50_above_ema200": _ema50_above_ema200,
    "rsi_between": _rsi_between,
    "volume_price_up": _volume_price_up,
}
