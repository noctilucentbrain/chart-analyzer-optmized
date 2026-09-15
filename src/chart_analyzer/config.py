from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


SUPPORTED_INDICATORS = {
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

SUPPORTED_EVENT_CONDITIONS = {
    "macd_cross_over",
    "macd_cross_under",
    "ema50_above_ema200",
    "rsi_between",
    "volume_price_up",
}

SUPPORTED_EVENT_OPERATORS = {"AND", "OR"}

MONGODB_URI_ENV = "CHART_ANALYZER_MONGODB_URI"
MONGODB_DATABASE_ENV = "CHART_ANALYZER_MONGODB_DATABASE"
MONGODB_SERVER_SELECTION_TIMEOUT_MS_ENV = "CHART_ANALYZER_MONGODB_SERVER_SELECTION_TIMEOUT_MS"


@dataclass(frozen=True)
class MongoConfig:
    uri: str
    database: str
    server_selection_timeout_ms: int = 5000


@dataclass(frozen=True)
class TimeframeConfig:
    name: str
    interval: str
    period: str
    indicators: list[str]


@dataclass(frozen=True)
class EventStrategyConfig:
    name: str
    operator: str
    conditions: dict[str, dict[str, Any]]
    timeframe: str | None = None


@dataclass(frozen=True)
class ServiceConfig:
    download_batch_size: int = 20
    download_workers: int = 4
    download_timeout_seconds: float = 10
    poll_interval_seconds: float = 300
    event_detection_interval_seconds: float | None = None
    log_file: str = "logs/chart-analyzer.log"


@dataclass(frozen=True)
class DailyEvaluationConfig:
    enabled: bool = True
    default_calendar: str = "NYSE"
    ticker_calendars: dict[str, str] = field(default_factory=dict)
    minutes_before_close: int = 60
    max_lateness_minutes: int = 10
    event_ttl_minutes: int = 10
    stop_entries_minutes_before_close: int = 15
    max_data_age_seconds: int = 300
    finalization_delay_minutes: int = 15


@dataclass(frozen=True)
class AppConfig:
    mongodb: MongoConfig
    tickers: list[str]
    timeframes: list[TimeframeConfig]
    indicators: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: list[EventStrategyConfig] = field(default_factory=list)
    service: ServiceConfig = field(default_factory=ServiceConfig)
    daily_evaluation: DailyEvaluationConfig = field(default_factory=DailyEvaluationConfig)


class ConfigError(ValueError):
    """Raised when the YAML configuration is invalid."""


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}
    return parse_config(raw)


def parse_config(raw: dict[str, Any]) -> AppConfig:
    if not isinstance(raw, dict):
        raise ConfigError("Config must be a YAML mapping.")

    mongodb = _parse_mongodb(raw.get("mongodb"))
    tickers = _parse_tickers(raw.get("tickers"))
    timeframes = _parse_timeframes(raw.get("timeframes"))
    indicators = raw.get("indicators") or {}
    events = _parse_events(raw.get("events") or {})
    service = _parse_service(raw.get("service") or {})

    if not isinstance(indicators, dict):
        raise ConfigError("indicators must be a mapping.")

    configured = {indicator for timeframe in timeframes for indicator in timeframe.indicators}
    missing_settings = configured.difference(indicators)
    unknown_settings = set(indicators).difference(SUPPORTED_INDICATORS)
    if missing_settings:
        names = ", ".join(sorted(missing_settings))
        raise ConfigError(f"Missing settings for indicators: {names}.")
    if unknown_settings:
        names = ", ".join(sorted(unknown_settings))
        raise ConfigError(f"Unknown indicator settings: {names}.")

    return AppConfig(
        mongodb=mongodb,
        tickers=tickers,
        timeframes=timeframes,
        indicators=indicators,
        events=events,
        service=service,
        daily_evaluation=_parse_daily_evaluation(raw.get("daily_evaluation", {})),
    )


def _parse_mongodb(value: Any) -> MongoConfig:
    if not isinstance(value, dict):
        raise ConfigError("mongodb must be configured.")

    uri = os.getenv(MONGODB_URI_ENV) or _required_string(value, "uri", "mongodb")
    database = os.getenv(MONGODB_DATABASE_ENV) or _required_string(value, "database", "mongodb")
    timeout = os.getenv(MONGODB_SERVER_SELECTION_TIMEOUT_MS_ENV) or value.get(
        "server_selection_timeout_ms",
        MongoConfig.server_selection_timeout_ms,
    )
    try:
        timeout = int(timeout)
    except (TypeError, ValueError) as error:
        raise ConfigError(
            "mongodb.server_selection_timeout_ms or "
            f"{MONGODB_SERVER_SELECTION_TIMEOUT_MS_ENV} must be a positive integer."
        ) from error
    if timeout <= 0:
        raise ConfigError(
            "mongodb.server_selection_timeout_ms or "
            f"{MONGODB_SERVER_SELECTION_TIMEOUT_MS_ENV} must be a positive integer."
        )
    return MongoConfig(uri=uri, database=database, server_selection_timeout_ms=timeout)


def _parse_tickers(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ConfigError("tickers must be a non-empty list.")

    tickers = []
    for ticker in value:
        if not isinstance(ticker, str) or not ticker.strip():
            raise ConfigError("tickers entries must be non-empty strings.")
        tickers.append(ticker.strip().upper())
    return list(dict.fromkeys(tickers))


def _parse_timeframes(value: Any) -> list[TimeframeConfig]:
    if not isinstance(value, list) or not value:
        raise ConfigError("timeframes must be a non-empty list.")

    parsed = []
    names = set()
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError("timeframe entries must be mappings.")

        name = _required_string(item, "name", "timeframe").lower()
        if name in names:
            raise ConfigError(f"Duplicate timeframe name: {name}.")
        names.add(name)

        indicators = item.get("indicators")
        if not isinstance(indicators, list) or not indicators:
            raise ConfigError(f"timeframe {name} must include indicators.")

        normalized_indicators = []
        for indicator in indicators:
            if not isinstance(indicator, str):
                raise ConfigError(f"timeframe {name} has a non-string indicator.")
            normalized = indicator.strip().lower()
            if normalized not in SUPPORTED_INDICATORS:
                raise ConfigError(f"Unsupported indicator: {normalized}.")
            normalized_indicators.append(normalized)

        parsed.append(
            TimeframeConfig(
                name=name,
                interval=_required_string(item, "interval", f"timeframe {name}"),
                period=_required_string(item, "period", f"timeframe {name}"),
                indicators=normalized_indicators,
            )
        )

    return parsed


def _parse_events(value: Any) -> list[EventStrategyConfig]:
    if not value:
        return []
    if not isinstance(value, dict):
        raise ConfigError("events must be a mapping.")

    strategies = []
    for name, raw_strategy in value.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("event strategy names must be non-empty strings.")
        if not isinstance(raw_strategy, dict):
            raise ConfigError(f"event strategy {name} must be a mapping.")

        operator = str(raw_strategy.get("operator", "AND")).upper()
        if operator not in SUPPORTED_EVENT_OPERATORS:
            raise ConfigError(f"event strategy {name} operator must be AND or OR.")

        raw_conditions = raw_strategy.get("conditions")
        if not isinstance(raw_conditions, dict) or not raw_conditions:
            raise ConfigError(f"event strategy {name} must include conditions.")

        conditions: dict[str, dict[str, Any]] = {}
        for condition_name, condition_settings in raw_conditions.items():
            normalized = str(condition_name).strip().lower()
            if normalized not in SUPPORTED_EVENT_CONDITIONS:
                raise ConfigError(f"Unsupported event condition: {normalized}.")
            if condition_settings is None:
                condition_settings = {}
            if not isinstance(condition_settings, dict):
                raise ConfigError(f"event condition {normalized} settings must be a mapping.")
            if condition_settings.get("enabled", True):
                conditions[normalized] = condition_settings

        if not conditions:
            raise ConfigError(f"event strategy {name} must enable at least one condition.")

        timeframe = raw_strategy.get("timeframe")
        if timeframe is not None and (not isinstance(timeframe, str) or not timeframe.strip()):
            raise ConfigError(f"event strategy {name}.timeframe must be a non-empty string.")

        strategies.append(
            EventStrategyConfig(
                name=name.strip(),
                operator=operator,
                timeframe=timeframe.strip().lower() if isinstance(timeframe, str) else None,
                conditions=conditions,
            )
        )

    return strategies


def _parse_service(value: Any) -> ServiceConfig:
    if not value:
        return ServiceConfig()
    if not isinstance(value, dict):
        raise ConfigError("service must be a mapping.")

    interval = value.get("poll_interval_seconds", ServiceConfig.poll_interval_seconds)
    try:
        interval = float(interval)
    except (TypeError, ValueError) as error:
        raise ConfigError("service.poll_interval_seconds must be a positive number.") from error

    if interval <= 0:
        raise ConfigError("service.poll_interval_seconds must be a positive number.")

    raw_event_interval = value.get("event_detection_interval_seconds")
    event_interval = None
    if raw_event_interval is not None:
        try:
            event_interval = float(raw_event_interval)
        except (TypeError, ValueError) as error:
            raise ConfigError("service.event_detection_interval_seconds must be a positive number.") from error
        if event_interval <= 0:
            raise ConfigError("service.event_detection_interval_seconds must be a positive number.")

    log_file = value.get("log_file", ServiceConfig.log_file)
    if not isinstance(log_file, str) or not log_file.strip():
        raise ConfigError("service.log_file must be a non-empty string.")

    performance = {}
    for key, maximum in (("download_batch_size", 200), ("download_workers", 32)):
        number = value.get(key, getattr(ServiceConfig, key))
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= maximum:
            raise ConfigError(f"service.{key} must be an integer between 1 and {maximum}.")
        performance[key] = number
    timeout = value.get("download_timeout_seconds", ServiceConfig.download_timeout_seconds)
    import math
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("service.download_timeout_seconds must be a positive finite number.")
    return ServiceConfig(
        **performance,
        download_timeout_seconds=float(timeout),
        poll_interval_seconds=interval,
        event_detection_interval_seconds=event_interval,
        log_file=log_file.strip(),
    )


def _required_string(mapping: dict[str, Any], key: str, owner: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{owner}.{key} must be a non-empty string.")
    return value.strip()


def _parse_daily_evaluation(value: Any) -> DailyEvaluationConfig:
    import pandas_market_calendars as mcal
    if not isinstance(value, dict):
        raise ConfigError("daily_evaluation must be a mapping.")
    unknown = set(value) - set(DailyEvaluationConfig.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"Unknown daily_evaluation settings: {sorted(unknown)}")
    enabled = value.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("daily_evaluation.enabled must be a boolean.")
    numbers = {}
    for key in ("minutes_before_close", "max_lateness_minutes", "event_ttl_minutes",
                "stop_entries_minutes_before_close", "max_data_age_seconds", "finalization_delay_minutes"):
        n = value.get(key, getattr(DailyEvaluationConfig, key))
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
            raise ConfigError(f"daily_evaluation.{key} must be a positive integer.")
        numbers[key] = n
    if numbers['minutes_before_close'] <= numbers['stop_entries_minutes_before_close'] + numbers['max_lateness_minutes']:
        raise ConfigError("Daily evaluation window must end before the entry cutoff.")
    default = value.get('default_calendar', 'NYSE')
    overrides = value.get('ticker_calendars', {})
    if not isinstance(overrides, dict) or any(not isinstance(k, str) or not k.strip() for k in overrides):
        raise ConfigError("daily_evaluation.ticker_calendars must map tickers to calendars.")
    for calendar in [default, *overrides.values()]:
        if not isinstance(calendar, str) or calendar not in mcal.get_calendar_names():
            raise ConfigError(f"Unknown exchange calendar: {calendar}")
    return DailyEvaluationConfig(enabled=enabled, default_calendar=default,
        ticker_calendars={k.strip().upper(): v for k, v in overrides.items()}, **numbers)
