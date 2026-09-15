"""Once-per-session evaluations; mutable daily history and immutable decisions are separate."""
import hashlib
import json
import logging
from dataclasses import asdict

import pandas as pd
from pymongo.errors import DuplicateKeyError

from chart_analyzer.cleaning import clean_ohlcv
from chart_analyzer.events import evaluate_event_strategy
from chart_analyzer.indicators import compute_indicators
from chart_analyzer.sessions import session_plan, utc, utc_now
from chart_analyzer.storage import candle_documents, aggregate_candle_with_indicators, indicator_documents

LOGGER = logging.getLogger(__name__)


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


def evaluation_id(ticker, timeframe, session, strategy):
    return identity(ticker, timeframe, session, strategy)


def snapshot_candle(minute_raw, ticker, timeframe, session, settings, now):
    """Require a fresh, complete regular-session minute series; never use pre/post-market."""
    minutes, _ = clean_ohlcv(minute_raw, ticker, timeframe, '1m')
    now = utc(now)
    if minutes.empty:
        return None
    minutes = minutes.loc[(minutes.timestamp >= session.open) &
                          (minutes.timestamp < session.close) & (minutes.timestamp <= now)]
    if minutes.empty or minutes.timestamp.duplicated().any():
        return None
    source_at = minutes.timestamp.iloc[-1]
    if now - source_at > pd.Timedelta(seconds=settings.max_data_age_seconds):
        return None
    if minutes.timestamp.iloc[0] != session.open or (minutes.timestamp.diff().dropna() > pd.Timedelta(minutes=1)).any():
        return None
    row = {'ticker': ticker, 'timeframe': timeframe, 'timestamp': pd.Timestamp(session.date, tz='UTC'),
           'open': float(minutes.open.iloc[0]), 'high': float(minutes.high.max()),
           'low': float(minutes.low.min()), 'close': float(minutes.close.iloc[-1]),
           'volume': float(minutes.volume.sum())}
    row['ohlc4'] = (row['open'] + row['high'] + row['low'] + row['close']) / 4
    row['hlcc4'] = (row['high'] + row['low'] + 2 * row['close']) / 4
    return row, source_at


def persist_decision(storage, document):
    # Includes false decisions. One atomic insert is both the checkpoint and event,
    # avoiding a checkpoint/event dual-write gap after a crash. Never update it.
    try:
        storage.database.session_evaluations.insert_one(document)
        return True
    except DuplicateKeyError:
        return False


def run_daily_timeframe(config, storage, collector, timeframe, clock=utc_now):
    from chart_analyzer.app import IngestionSummary
    settings = config.daily_evaluation
    strategies = [s for s in config.events if s.timeframe in (None, timeframe.name)]
    evaluations = storage.database.session_evaluations
    finalizations = storage.database.session_finalizations
    jobs = []
    for ticker in config.tickers:
        session, closed = session_plan(ticker, settings, clock())
        pending = []
        if session and session.due(clock()):
            pending = [s for s in strategies if evaluations.find_one(
                {'_id': evaluation_id(ticker, timeframe.name, session.date, s.name)}, {'_id': 1}) is None]
        checkpoint = finalizations.find_one({'_id': identity(ticker, timeframe.name)})
        needs_final = closed and (not checkpoint or checkpoint['session'] < closed)
        if pending or needs_final:
            jobs.append((ticker, session, closed, pending, bool(needs_final)))
    # Trading deadline work takes precedence over historical maintenance.
    jobs.sort(key=lambda j: (not bool(j[3]), j[1].deadline if j[3] else pd.Timestamp.max.tz_localize('UTC')))
    summaries = []
    size = config.service.download_batch_size
    for offset in range(0, len(jobs), size):
        batch = jobs[offset:offset + size]
        tickers = [j[0] for j in batch]
        history = collector.fetch_many(tickers, timeframe.interval, timeframe.period,
            workers=config.service.download_workers, timeout=config.service.download_timeout_seconds)
        due = [j[0] for j in batch if j[3] and j[1].due(clock())]
        minute_frames = collector.fetch_many(due, '1m', '1d',
            workers=config.service.download_workers, timeout=config.service.download_timeout_seconds) if due else {}
        fetched_at = utc(clock())
        for ticker, session, closed, pending, needs_final in batch:
            raw = history[ticker]
            if raw.empty:
                LOGGER.warning('Daily history unavailable ticker=%s; retry within window', ticker)
                continue
            candles, report = clean_ohlcv(raw, ticker, timeframe.name, timeframe.interval)
            candles['timestamp'] = candles.timestamp.dt.normalize()
            candles = candles.drop_duplicates('timestamp', keep='last')
            closed_history = candles.loc[candles.timestamp <= pd.Timestamp(closed, tz='UTC')].copy() if closed else candles.iloc[:0]
            candle_count = indicator_count = event_count = 0
            # Finalize newly closed rows once per session, refreshing recent context
            # without rewriting all years of indicator documents on every poll.
            if needs_final and not closed_history.empty and closed_history.timestamp.iloc[-1] == pd.Timestamp(closed, tz='UTC'):
                checkpoint = finalizations.find_one({'_id': identity(ticker, timeframe.name)})
                output = closed_history
                if checkpoint:
                    changed = closed_history.index[closed_history.timestamp >= pd.Timestamp(checkpoint['session'], tz='UTC')]
                    if len(changed):
                        # Centered pivots may revise a few earlier rows when the
                        # final candle arrives. Keep all input for EMA/RSI warm-up.
                        padding = int(config.indicators.get('support_resistance', {}).get('pivot_window', 3))
                        start = max(0, closed_history.index.get_loc(changed[0]) - padding)
                        output = closed_history.iloc[start:]
                values = compute_indicators(closed_history, timeframe.indicators, config.indicators, output_index=output.index)
                indicator_count += storage.upsert_indicators(values).upserted
                candle_count += storage.upsert_candles(output).upserted
                finalizations.update_one({'_id': identity(ticker, timeframe.name)},
                    {'$max': {'session': closed}, '$set': {'updated_at': utc(clock()).to_pydatetime()}}, upsert=True)
            if pending and ticker in minute_frames and session.due(clock()):
                result = snapshot_candle(minute_frames[ticker], ticker, timeframe.name, session, settings, clock())
                if result is None or closed_history.empty or closed_history.timestamp.iloc[-1] != pd.Timestamp(closed, tz='UTC'):
                    LOGGER.warning('Daily evaluation skipped: stale/incomplete data ticker=%s session=%s', ticker, session.date)
                else:
                    current, source_at = result
                    combined = pd.concat([closed_history, pd.DataFrame([current])], ignore_index=True)
                    selected = combined.tail(2)
                    values = compute_indicators(combined, timeframe.indicators, config.indicators, output_index=selected.index)
                    indicator_count += storage.upsert_indicators(values).upserted
                    candle_count += storage.upsert_candles(pd.DataFrame([current])).upserted
                    docs = candle_documents(selected)
                    inds = indicator_documents(values)
                    previous, latest = [aggregate_candle_with_indicators(c, [i for i in inds if i['timestamp'] == c['timestamp']]) for c in docs]
                    for strategy in pending:
                        evaluated_at = utc(clock())
                        # Recheck after IO/CPU work; never publish an overdue decision.
                        if not session.due(evaluated_at) or evaluated_at - source_at > pd.Timedelta(seconds=settings.max_data_age_seconds):
                            LOGGER.warning('Daily evaluation deadline/freshness missed ticker=%s', ticker)
                            break
                        event = evaluate_event_strategy(latest, previous, strategy)
                        event_id = evaluation_id(ticker, timeframe.name, session.date, strategy.name)
                        settings_snapshot = {'strategy': asdict(strategy), 'indicators': config.indicators,
                                             'daily_evaluation': asdict(settings)}
                        event.update(_id=event_id, event_id=event_id, evaluation_mode='before_session_close',
                            session=session.date, calendar=session.calendar,
                            scheduled_at=session.scheduled_at.to_pydatetime(),
                            evaluated_at=evaluated_at.to_pydatetime(), source_data_at=source_at.to_pydatetime(),
                            fetched_at=fetched_at.to_pydatetime(), session_close=session.close.to_pydatetime(),
                            expires_at=min(evaluated_at + pd.Timedelta(minutes=settings.event_ttl_minutes), session.entry_cutoff).to_pydatetime(),
                            reference_price=latest['close'], reference_price_type='latest_regular_session_minute_close',
                            candle_snapshot=latest, previous_candle_snapshot=previous,
                            strategy_version=identity(settings_snapshot), settings_snapshot=settings_snapshot)
                        inserted = persist_decision(storage, event)
                        event_count += int(inserted and event['triggered'])
                        LOGGER.info('Daily decision ticker=%s strategy=%s session=%s triggered=%s inserted=%s',
                            ticker, strategy.name, session.date, event['triggered'], inserted)
            summaries.append(IngestionSummary(ticker, timeframe.name, report.downloaded_rows,
                report.cleaned_rows, candle_count, indicator_count, event_count))
    return summaries
