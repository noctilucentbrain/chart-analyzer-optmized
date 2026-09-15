from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from threading import Event

import pandas as pd
from pymongo.errors import ServerSelectionTimeoutError

from chart_analyzer.cleaning import clean_ohlcv
from chart_analyzer.collector import YFinanceCollector
from chart_analyzer.config import AppConfig, TimeframeConfig
from chart_analyzer.indicators import compute_indicators
from chart_analyzer.storage import MongoStorage


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionSummary:
    ticker: str
    timeframe: str
    downloaded: int
    cleaned: int
    candles_upserted: int
    indicators_upserted: int
    events_upserted: int = 0


def run(config: AppConfig) -> None:
    collector = YFinanceCollector()
    storage = MongoStorage(
        config.mongodb.uri,
        config.mongodb.database,
        server_selection_timeout_ms=config.mongodb.server_selection_timeout_ms,
    )
    try:
        storage.ensure_indexes()
        for timeframe in config.timeframes:
            size = config.service.download_batch_size
            for offset in range(0, len(config.tickers), size):
                batch = config.tickers[offset:offset + size]
                frames = collector.fetch_many(batch, timeframe.interval, timeframe.period,
                                              workers=config.service.download_workers,
                                              timeout=config.service.download_timeout_seconds)
                for ticker in batch:
                    raw = frames[ticker]
                    if raw.empty:
                        LOGGER.warning("No initialization data ticker=%s timeframe=%s", ticker, timeframe.name)
                        continue
                    candles, report = clean_ohlcv(raw, ticker, timeframe.name, timeframe.interval)
                    closed = None
                    if config.daily_evaluation.enabled and timeframe.interval == '1d':
                        from chart_analyzer.sessions import session_plan, utc_now
                        _, closed = session_plan(ticker, config.daily_evaluation, utc_now())
                        candles['timestamp'] = candles.timestamp.dt.normalize()
                        candles = candles.loc[candles.timestamp <= pd.Timestamp(closed, tz='UTC')] if closed else candles.iloc[:0]
                        if candles.empty:
                            continue
                    indicators = compute_indicators(candles, timeframe.indicators, config.indicators)
                    storage.upsert_indicators(indicators)
                    storage.upsert_candles(candles)
                    if closed and candles.timestamp.iloc[-1] == pd.Timestamp(closed, tz='UTC'):
                        from chart_analyzer.daily import identity
                        storage.database.session_finalizations.update_one({'_id': identity(ticker, timeframe.name)},
                            {'$max': {'session': closed}}, upsert=True)
                    LOGGER.info("Initialized ticker=%s timeframe=%s rows=%d gaps=%d",
                                ticker, timeframe.name, len(candles), len(report.gaps))
    finally:
        storage.close()


def initialize_database(config: AppConfig) -> None:
    run(config)
    detect_configured_events(config)


def run_latest(config: AppConfig) -> list[IngestionSummary]:
    collector = YFinanceCollector()
    storage = MongoStorage(
        config.mongodb.uri,
        config.mongodb.database,
        server_selection_timeout_ms=config.mongodb.server_selection_timeout_ms,
    )
    summaries = []
    started = time.perf_counter()
    try:
        storage.ensure_indexes()
        for timeframe in sorted(config.timeframes, key=lambda item: item.interval != '1d'):
            if config.daily_evaluation.enabled and timeframe.interval == '1d':
                from chart_analyzer.daily import run_daily_timeframe
                summaries.extend(run_daily_timeframe(config, storage, collector, timeframe))
                continue
            size = config.service.download_batch_size
            for offset in range(0, len(config.tickers), size):
                batch = config.tickers[offset:offset + size]
                frames = collector.fetch_many(batch, timeframe.interval, timeframe.period,
                                              workers=config.service.download_workers,
                                              timeout=config.service.download_timeout_seconds)
                for ticker in batch:
                    if frames[ticker].empty:
                        LOGGER.warning("No data ticker=%s timeframe=%s; retry next poll", ticker, timeframe.name)
                        continue
                    summaries.append(_process_latest_ticker_timeframe(
                        config, storage, collector, ticker, timeframe, raw=frames[ticker]))
    finally:
        storage.close()
        LOGGER.info("Ingestion cycle seconds=%.3f series_processed=%d", time.perf_counter() - started, len(summaries))
    return summaries


def detect_configured_events(config: AppConfig) -> None:
    if not config.events:
        LOGGER.info("No event strategies configured")
        return

    storage = MongoStorage(
        config.mongodb.uri,
        config.mongodb.database,
        server_selection_timeout_ms=config.mongodb.server_selection_timeout_ms,
    )
    try:
        storage.ensure_indexes()
        for strategy in config.events:
            for timeframe in config.timeframes:
                if strategy.timeframe not in (None, timeframe.name):
                    continue
                if config.daily_evaluation.enabled and timeframe.interval == '1d':
                    LOGGER.info("Historical daily event generation disabled for scheduled strategy=%s", strategy.name)
                    continue
                for ticker in config.tickers:
                    storage.detect_events(strategy, ticker=ticker, timeframe=timeframe.name, save=True)
    finally:
        storage.close()


def _process_latest_ticker_timeframe(
    config: AppConfig,
    storage: MongoStorage,
    collector: YFinanceCollector,
    ticker: str,
    timeframe: TimeframeConfig,
    raw: pd.DataFrame | None = None,
) -> IngestionSummary:
    LOGGER.info(
        "Processing latest ticker=%s timeframe=%s interval=%s period=%s",
        ticker,
        timeframe.name,
        timeframe.interval,
        timeframe.period,
    )
    if raw is None:
        raw = collector.fetch(ticker, timeframe.interval, timeframe.period)
    candles, report = clean_ohlcv(raw, ticker, timeframe.name, timeframe.interval)
    latest_stored = storage.get_latest_candle_timestamp(ticker, timeframe.name)
    new_candles = _newer_candles(candles, latest_stored)

    LOGGER.info(
        "Cleaned latest ticker=%s timeframe=%s downloaded=%s cleaned=%s new=%s",
        ticker,
        timeframe.name,
        report.downloaded_rows,
        report.cleaned_rows,
        len(new_candles),
    )

    if new_candles.empty:
        return IngestionSummary(
            ticker=ticker,
            timeframe=timeframe.name,
            downloaded=report.downloaded_rows,
            cleaned=report.cleaned_rows,
            candles_upserted=0,
            indicators_upserted=0,
            events_upserted=0,
        )

    indicator_frame = compute_indicators(candles, timeframe.indicators, config.indicators, output_index=new_candles.index)
    new_indicators = _matching_indicator_rows(indicator_frame, new_candles)

    indicator_result = storage.upsert_indicators(new_indicators)
    candle_result = storage.upsert_candles(new_candles)
    event_count = _detect_latest_events(config, storage, ticker, timeframe, latest_stored)

    LOGGER.info(
        "Mongo latest ticker=%s timeframe=%s candles_upserted=%s indicators_upserted=%s events_upserted=%s",
        ticker,
        timeframe.name,
        candle_result.upserted,
        indicator_result.upserted,
        event_count,
    )
    return IngestionSummary(
        ticker=ticker,
        timeframe=timeframe.name,
        downloaded=report.downloaded_rows,
        cleaned=report.cleaned_rows,
        candles_upserted=candle_result.upserted,
        indicators_upserted=indicator_result.upserted,
        events_upserted=event_count,
    )


def _detect_latest_events(
    config: AppConfig,
    storage: MongoStorage,
    ticker: str,
    timeframe: TimeframeConfig,
    after: object,
) -> int:
    count = 0
    for strategy in config.events:
        if strategy.timeframe is not None and strategy.timeframe != timeframe.name:
            continue
        events = storage.detect_events(
            strategy,
            ticker=ticker,
            timeframe=timeframe.name,
            after=after,
            save=True,
        )
        count += len(events)
    return count


def _newer_candles(candles: pd.DataFrame, latest_stored: object) -> pd.DataFrame:
    if candles.empty:
        return candles
    if latest_stored is None:
        return candles.tail(1).copy()
    latest_timestamp = pd.Timestamp(latest_stored)
    if latest_timestamp.tzinfo is None:
        latest_timestamp = latest_timestamp.tz_localize("UTC")
    else:
        latest_timestamp = latest_timestamp.tz_convert("UTC")
    return candles.loc[candles["timestamp"] > latest_timestamp].copy()


def _matching_indicator_rows(indicators: pd.DataFrame, candles: pd.DataFrame) -> pd.DataFrame:
    if indicators.empty or candles.empty:
        return indicators.iloc[0:0].copy()
    timestamps = set(candles["timestamp"])
    return indicators.loc[indicators["timestamp"].isin(timestamps)].copy()


def run_service(
    config: AppConfig,
    interval_seconds: float | None = None,
    event_interval_seconds: float | None = None,
    max_runs: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    stop_event: Event | None = None,
) -> None:
    interval = interval_seconds or config.service.poll_interval_seconds
    event_interval = event_interval_seconds or config.service.event_detection_interval_seconds or interval
    if interval <= 0:
        raise ValueError("Service interval must be a positive number.")
    if config.daily_evaluation.enabled and interval > config.daily_evaluation.max_lateness_minutes * 60:
        raise ValueError("Polling interval must not exceed the daily evaluation lateness window.")
    if event_interval <= 0:
        raise ValueError("Service event interval must be a positive number.")

    LOGGER.info(
        "Starting chart analyzer service interval_seconds=%s event_interval_seconds=%s",
        interval,
        event_interval,
    )
    runs = 0
    while max_runs is None or runs < max_runs:
        if stop_event is not None and stop_event.is_set():
            LOGGER.info("Stopping chart analyzer service")
            break

        cycle_started = time.monotonic()
        try:
            run_latest(config)
        except ServerSelectionTimeoutError as error:
            LOGGER.error("Chart analyzer service cannot reach MongoDB: %s", error)
        except Exception:
            LOGGER.exception("Chart analyzer service run failed")

        runs += 1

        if max_runs is not None and runs >= max_runs:
            break

        pause = max(0, interval - (time.monotonic() - cycle_started)) if config.daily_evaluation.enabled else interval
        LOGGER.info("Chart analyzer service sleeping interval_seconds=%s", pause)
        try:
            if stop_event is not None:
                if stop_event.wait(pause):
                    LOGGER.info("Stopping chart analyzer service")
                    break
            else:
                sleep(pause)
        except KeyboardInterrupt:
            LOGGER.info("Stopping chart analyzer service")
            break
