from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

import mongomock
import pandas as pd
import pytest

from chart_analyzer.config import (AppConfig, MongoConfig, TimeframeConfig, EventStrategyConfig,
    DailyEvaluationConfig, _parse_daily_evaluation, ConfigError)
from chart_analyzer.daily import run_daily_timeframe, snapshot_candle, persist_decision
from chart_analyzer.sessions import session_plan
from chart_analyzer.storage import MongoStorage, UpsertResult, candle_documents, indicator_documents


def config():
    return AppConfig(MongoConfig('mongodb://unused', 'test'), ['AAPL'],
        [TimeframeConfig('daily', '1d', '3y', ['ema_50', 'ema_200'])],
        indicators={'ema_50': {'window': 2}, 'ema_200': {'window': 3}},
        events=[EventStrategyConfig('entry', 'AND', {'ema50_above_ema200': {}}, 'daily')])


class Storage(MongoStorage):
    def __init__(self, database=None):
        self.database = database if database is not None else mongomock.MongoClient().db
        self.candles = self.database.candles
        self.events = self.database.events

    def upsert_candles(self, frame):
        return self.save(self.candles, candle_documents(frame))

    def upsert_indicators(self, frame):
        docs = indicator_documents(frame)
        for doc in docs:
            self.save(self.database['indicator_' + doc['indicator']], [doc])
        return UpsertResult(0, 0, len(docs))

    def save(self, collection, docs):
        for doc in docs:
            key = {k: doc[k] for k in ('ticker', 'timeframe', 'timestamp')}
            collection.update_one(key, {'$set': doc}, upsert=True)
        return UpsertResult(0, 0, len(docs))


def frames():
    history = pd.DataFrame({'Open': [100., 110., 1.], 'High': [102., 112., 1.],
        'Low': [99., 109., 1.], 'Close': [101., 111., 1.], 'Volume': [100., 100., 1.]},
        index=pd.to_datetime(['2026-09-10', '2026-09-11', '2026-09-14']))
    minutes = pd.DataFrame({'Open': 120., 'High': 122., 'Low': 119., 'Close': 121., 'Volume': 100.},
        index=pd.date_range('2026-09-14T13:30Z', '2026-09-14T19:00Z', freq='min'))
    return history, minutes


def collector(history, minutes):
    obj = Mock()
    obj.fetch_many.side_effect = lambda tickers, interval, period, **kw: {t: history.copy() if interval == '1d' else minutes.copy() for t in tickers}
    return obj


@pytest.mark.parametrize('day,expected', [('2026-09-14','19:00'), ('2026-01-05','20:00'), ('2026-11-27','17:00')])
def test_close_relative_schedule_dst_and_early_close(day, expected):
    session, _ = session_plan('AAPL', DailyEvaluationConfig(), day+'T12:00Z')
    assert session.scheduled_at == pd.Timestamp(f'{day}T{expected}Z')
    assert session.due(session.scheduled_at)
    assert not session.due(session.scheduled_at-pd.Timedelta(seconds=1))
    assert not session.due(session.deadline+pd.Timedelta(seconds=1))
    assert not session.due(session.close)


@pytest.mark.parametrize('day', ['2026-09-13', '2026-12-25'])
def test_weekend_and_holiday_do_not_evaluate(day):
    assert session_plan('AAPL', DailyEvaluationConfig(), day+'T19:00Z')[0] is None


def test_ticker_calendar_override():
    session, _ = session_plan('VOD.L', DailyEvaluationConfig(ticker_calendars={'VOD.L':'LSE'}), '2026-09-14T12:00Z')
    assert session.calendar == 'LSE'
    assert session.scheduled_at == pd.Timestamp('2026-09-14T14:30Z')


@pytest.mark.parametrize('value', [{'enabled':'yes'}, {'minutes_before_close':False}, {'max_data_age_seconds':0},
    {'default_calendar':'invalid'}, {'ticker_calendars':[]}, {'minutes_before_close':20}, {'unknown':1}])
def test_bad_config_rejected(value):
    with pytest.raises(ConfigError):
        _parse_daily_evaluation(value)


def test_stale_incomplete_and_premarket_minute_data():
    _, minutes = frames()
    settings = DailyEvaluationConfig()
    session, _ = session_plan('AAPL', settings, '2026-09-14T19:00Z')
    assert snapshot_candle(minutes.iloc[:-10], 'AAPL','daily',session,settings,'2026-09-14T19:00Z') is None
    assert snapshot_candle(minutes.drop(minutes.index[30]), 'AAPL','daily',session,settings,'2026-09-14T19:00Z') is None
    before = minutes.iloc[:1].copy(); before.index = pd.to_datetime(['2026-09-14T12:00Z']); before['High'] = 1000.
    row, _ = snapshot_candle(pd.concat([before,minutes]),'AAPL','daily',session,settings,'2026-09-14T19:00Z')
    assert row['high'] == 122.


def test_refresh_existing_daily_candle_and_emit_once_across_restart():
    cfg = config(); storage = Storage(); history, minutes = frames(); fetcher = collector(history,minutes)
    storage.candles.insert_one({'ticker':'AAPL','timeframe':'daily','timestamp':pd.Timestamp('2026-09-14').to_pydatetime(),'close':1.})
    run_daily_timeframe(cfg,storage,fetcher,cfg.timeframes[0],clock=lambda: pd.Timestamp('2026-09-14T19:00Z'))
    assert storage.candles.find_one({'timestamp':pd.Timestamp('2026-09-14').to_pydatetime()})['close'] == 121.
    decisions = list(storage.database.session_evaluations.find())
    assert len(decisions) == 1 and decisions[0]['triggered']
    assert decisions[0]['reference_price'] == 121.
    assert decisions[0]['previous_candle_snapshot']['close'] == 111.
    assert decisions[0]['expires_at'] == pd.Timestamp('2026-09-14T19:10').to_pydatetime()
    restarted = Storage(storage.database)
    assert run_daily_timeframe(cfg,restarted,fetcher,cfg.timeframes[0],clock=lambda: pd.Timestamp('2026-09-14T19:05Z')) == []
    assert list(storage.database.session_evaluations.find()) == decisions
    assert len(storage.get_actionable_events(now='2026-09-14T19:05Z')) == 1
    assert storage.get_actionable_events(now='2026-09-14T19:10Z') == []
    assert storage.get_actionable_events(now='2026-09-15T14:00Z') == []


def test_false_decision_is_checkpointed_not_reevaluated():
    cfg = config(); storage = Storage(); history, minutes = frames()
    minutes[['Open','High','Low','Close']] = 1.
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:00Z'))
    assert storage.database.session_evaluations.count_documents({'triggered':False}) == 1
    _, changed = frames()
    fetcher = collector(history,changed)
    run_daily_timeframe(cfg,Storage(storage.database),fetcher,cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:05Z'))
    fetcher.fetch_many.assert_not_called()
    assert storage.get_actionable_events(now='2026-09-14T19:06Z') == []


def test_finalization_updates_history_without_rewriting_signal_or_emitting():
    cfg = config(); storage = Storage(); history, minutes = frames()
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:00Z'))
    frozen = list(storage.database.session_evaluations.find())
    history.loc[history.index[-1], 'Close'] = 125.
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T20:16Z'))
    assert storage.candles.find_one({'timestamp':pd.Timestamp('2026-09-14').to_pydatetime()})['close'] == 125.
    assert list(storage.database.session_evaluations.find()) == frozen
    assert storage.get_actionable_events(now='2026-09-14T20:16Z') == []
    assert storage.events.count_documents({}) == 0


def test_late_start_does_not_backfill_actionable_events():
    cfg = config(); storage = Storage(); history, minutes = frames()
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:11Z'))
    assert storage.database.session_evaluations.count_documents({}) == 0


def test_atomic_first_decision_wins_under_concurrent_retries():
    storage = Storage()
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: persist_decision(storage, {'_id':'same-session','reference_price':i}), range(20)))
    assert sum(results) == 1
    assert storage.database.session_evaluations.count_documents({}) == 1


def test_stale_data_can_retry_within_window_without_false_checkpoint():
    cfg = config(); storage = Storage(); history, minutes = frames()
    run_daily_timeframe(cfg,storage,collector(history,minutes.iloc[:-10]),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:00Z'))
    assert storage.database.session_evaluations.count_documents({}) == 0
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:pd.Timestamp('2026-09-14T19:04Z'))
    assert storage.database.session_evaluations.count_documents({}) == 1


def test_processing_crossing_deadline_does_not_publish():
    cfg = config(); storage = Storage(); history, minutes = frames()
    now = [pd.Timestamp('2026-09-14T19:00Z')]
    original = storage.upsert_indicators
    def slow_write(frame):
        result = original(frame)
        now[0] = pd.Timestamp('2026-09-14T19:11Z')
        return result
    storage.upsert_indicators = slow_write
    run_daily_timeframe(cfg,storage,collector(history,minutes),cfg.timeframes[0],clock=lambda:now[0])
    assert storage.database.session_evaluations.count_documents({}) == 0


def test_history_event_scan_skips_daily_even_for_wildcard_strategy(monkeypatch):
    from chart_analyzer.app import detect_configured_events
    cfg = config()
    cfg = replace(cfg, events=[replace(cfg.events[0], timeframe=None)])
    storage = Mock()
    monkeypatch.setattr('chart_analyzer.app.MongoStorage', lambda *a,**kw: storage)
    detect_configured_events(cfg)
    storage.detect_events.assert_not_called()
    storage.close.assert_called_once()


def test_latest_api_uses_actionable_daily_events_not_historical_events(tmp_path, monkeypatch):
    import yaml
    from fastapi.testclient import TestClient
    from chart_analyzer.api import create_app
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump({'mongodb': {'uri':'mongodb://unused','database':'test'},
        'tickers':['AAPL'], 'timeframes':[{'name':'daily','interval':'1d','period':'1y','indicators':['rsi']}],
        'indicators':{'rsi':{}}, 'service':{'log_file':str(tmp_path/'service.log')}}))
    storage = Mock()
    storage.get_latest_candles_with_events.return_value = {'candles':[{'ticker':'AAPL'}], 'events':[{'strategy':'old'}]}
    storage.get_actionable_events.return_value = []
    monkeypatch.setattr('chart_analyzer.api.MongoStorage',lambda *a,**kw: storage)
    with TestClient(create_app(path)) as client:
        response = client.get('/timeframes/daily/latest')
        assert response.status_code == 200 and response.json()['events'] == []
        assert client.get('/events/actionable').json() == {'events':[]}
