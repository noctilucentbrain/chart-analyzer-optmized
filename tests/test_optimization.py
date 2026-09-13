from dataclasses import replace
from unittest.mock import Mock

import pandas as pd
import pytest

from chart_analyzer.indicators import compute_indicators, INDICATORS
from chart_analyzer.collector import YFinanceCollector
from chart_analyzer.config import EventStrategyConfig, ConfigError, _parse_service
from chart_analyzer.storage import MongoStorage
from test_storage import FakeCollection, FakeDatabase, candle_doc, indicator_doc


def sample_candles(count=120):
    frame = pd.DataFrame({
        'ticker': ['AAPL'] * count, 'timeframe': ['daily'] * count,
        'timestamp': pd.date_range('2024-01-01', periods=count, tz='UTC'),
        'open': [100 + i % 11 for i in range(count)],
        'high': [103 + i % 11 for i in range(count)],
        'low': [98 + i % 11 for i in range(count)],
        'close': [101 + i % 13 for i in range(count)],
        'volume': [1000 + i * 7 for i in range(count)],
    })
    frame.index = pd.Index(range(10, count * 2 + 10, 2))
    return frame


@pytest.mark.parametrize('name', list(INDICATORS))
@pytest.mark.parametrize('positions', [[-1], [-5, -3, -1], []])
def test_selected_indicators_equal_full_history(name, positions):
    candles = sample_candles()
    full = compute_indicators(candles, [name], {})
    selected = candles.iloc[positions]
    actual = compute_indicators(candles, [name], {}, output_index=selected.index)
    expected = full.loc[full.timestamp.isin(selected.timestamp)].reset_index(drop=True)
    if expected.empty:
        assert actual.empty
    else:
        pd.testing.assert_frame_equal(actual, expected)


def test_context_is_reset_after_failure():
    candles = sample_candles()
    with pytest.raises(ValueError):
        compute_indicators(candles, ['volume_at_price'], {'volume_at_price': {'lookback': 0}}, output_index=candles.tail(1).index)
    assert len(compute_indicators(candles, ['ema_50'], {})) == len(candles)


def test_download_batch_deduplicates_and_handles_missing_symbol(monkeypatch):
    frame = pd.concat({'AAPL': pd.DataFrame({'Close': [1., 2.]}),
                       'MSFT': pd.DataFrame({'Close': [float('nan'), float('nan')]})}, axis=1)
    download = Mock(return_value=frame)
    monkeypatch.setattr('chart_analyzer.collector.yf.download', download)
    result = YFinanceCollector().fetch_many(['aapl', ' AAPL ', 'MSFT', 'BAD'], '1d', '1y', workers=3)
    assert result['AAPL'].Close.tolist() == [1., 2.]
    assert result['MSFT'].empty and result['BAD'].empty
    assert download.call_args.kwargs['tickers'] == ['AAPL', 'MSFT', 'BAD']
    assert download.call_args.kwargs['threads'] == 3


@pytest.mark.parametrize('key,value', [('download_workers', 0), ('download_workers', True),
    ('download_batch_size', 201), ('download_batch_size', 1.5), ('download_timeout_seconds', float('nan'))])
def test_invalid_performance_config(key, value):
    with pytest.raises(ConfigError):
        _parse_service({key: value})


def test_incremental_events_preserve_crossover_and_bound_indicator_reads():
    timestamps = pd.date_range('2024-01-01', periods=100, tz='UTC').to_pydatetime().tolist()
    storage = MongoStorage.__new__(MongoStorage)
    storage.candles = FakeCollection([candle_doc(t) for t in timestamps])
    indicators = FakeCollection([indicator_doc(t, 'macd', {'macd': i % 2, 'signal': .5}) for i,t in enumerate(timestamps)])
    storage.database = FakeDatabase({'indicator_macd': indicators})
    strategy = EventStrategyConfig('cross', 'AND', {'macd_cross_over': {}}, 'daily')
    expected = [e for e in storage.detect_events(strategy, ticker='AAPL') if e['timestamp'] > timestamps[-3]]
    original_find = indicators.find
    queries = []
    def find(query, projection):
        queries.append(query)
        return original_find(query, projection)
    indicators.find = find
    actual = storage.detect_events(strategy, ticker='AAPL', after=timestamps[-3])
    assert actual == expected and actual
    assert len(queries) == 1
    assert len(queries[0]['$or']) == 3
    assert storage.detect_events(strategy, ticker='AAPL', after=timestamps[-1]) == []


def test_run_latest_batches_tickers_and_skips_empty(monkeypatch):
    from test_app import config
    from chart_analyzer.app import run_latest
    cfg = config()
    cfg = replace(cfg, tickers=['AAPL', 'MSFT', 'BAD'], service=replace(cfg.service, download_batch_size=2))
    collector = Mock()
    collector.fetch_many.side_effect = [dict(AAPL=pd.DataFrame({'x':[1]}), MSFT=pd.DataFrame({'x':[2]})), {'BAD':pd.DataFrame()}]
    storage = Mock()
    process = Mock(side_effect=['a', 'm'])
    monkeypatch.setattr('chart_analyzer.app.YFinanceCollector', lambda: collector)
    monkeypatch.setattr('chart_analyzer.app.MongoStorage', lambda *a, **kw: storage)
    monkeypatch.setattr('chart_analyzer.app._process_latest_ticker_timeframe', process)
    assert run_latest(cfg) == ['a','m']
    assert [c.args[0] for c in collector.fetch_many.call_args_list] == [['AAPL','MSFT'], ['BAD']]
    storage.close.assert_called_once()


def test_bulk_write_is_bounded():
    from chart_analyzer.storage import _bulk_upsert
    collection = Mock()
    collection.bulk_write.return_value = Mock(matched_count=0, modified_count=0, upserted_ids={})
    _bulk_upsert(collection, [{'id': i} for i in range(2501)], ['id'])
    assert [len(c.args[0]) for c in collection.bulk_write.call_args_list] == [1000, 1000, 501]


def test_indicator_index_created_once_per_storage_client(monkeypatch):
    client = Mock()
    database = Mock()
    collection = Mock()
    client.__getitem__ = Mock(return_value=database)
    database.__getitem__ = Mock(return_value=collection)
    monkeypatch.setattr('chart_analyzer.storage.MongoClient', lambda *a, **kw: client)
    storage = MongoStorage('mongodb://localhost', 'test')
    storage._indicator_collection('rsi')
    storage._indicator_collection('rsi')
    collection.create_index.assert_called_once()


def test_event_boundary_handles_mongo_naive_dates_and_aware_input():
    timestamps = pd.date_range('2024-01-01', periods=2).to_pydatetime().tolist()
    storage = MongoStorage.__new__(MongoStorage)
    import mongomock
    storage.database = mongomock.MongoClient().db
    storage.candles = storage.database.candles
    storage.candles.insert_many([candle_doc(t) for t in timestamps])
    storage.database.indicator_macd.insert_many([
        indicator_doc(t, 'macd', {'macd': i, 'signal': .5}) for i, t in enumerate(timestamps)])
    strategy = EventStrategyConfig('cross', 'AND', {'macd_cross_over': {}}, 'daily')
    assert len(storage.detect_events(strategy, ticker='AAPL', after='2024-01-01T00:00:00Z')) == 1
