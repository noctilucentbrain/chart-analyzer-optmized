"""Exchange-calendar scheduling. All clock comparisons use aware UTC timestamps."""
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

from chart_analyzer.config import DailyEvaluationConfig


def utc_now():
    return pd.Timestamp.now(tz='UTC')


def utc(value):
    value = pd.Timestamp(value)
    return value.tz_localize('UTC') if value.tzinfo is None else value.tz_convert('UTC')


@lru_cache(maxsize=64)
def calendar(name):
    return mcal.get_calendar(name)


@lru_cache(maxsize=128)
def schedule(name, date):
    day = pd.Timestamp(date)
    return calendar(name).schedule(start_date=day - pd.Timedelta(days=45), end_date=day)


@dataclass(frozen=True)
class Session:
    calendar: str
    date: str
    open: pd.Timestamp
    close: pd.Timestamp
    scheduled_at: pd.Timestamp
    deadline: pd.Timestamp
    entry_cutoff: pd.Timestamp

    def due(self, now):
        now = utc(now)
        return self.open <= self.scheduled_at <= now <= self.deadline and now < self.entry_cutoff


def session_plan(ticker, settings: DailyEvaluationConfig, now):
    now = utc(now)
    name = settings.ticker_calendars.get(ticker, settings.default_calendar)
    day = now.tz_convert(calendar(name).tz).date().isoformat()
    rows = schedule(name, day)
    today = None
    if pd.Timestamp(day) in rows.index:
        row = rows.loc[pd.Timestamp(day)]
        close = utc(row.market_close)
        scheduled = close - pd.Timedelta(minutes=settings.minutes_before_close)
        cutoff = close - pd.Timedelta(minutes=settings.stop_entries_minutes_before_close)
        today = Session(name, day, utc(row.market_open), close, scheduled,
                        scheduled + pd.Timedelta(minutes=settings.max_lateness_minutes), cutoff)
    completed = rows.loc[rows.market_close + pd.Timedelta(minutes=settings.finalization_delay_minutes) <= now]
    last_closed = None if completed.empty else completed.index[-1].date().isoformat()
    return today, last_closed
