from __future__ import annotations

from threading import RLock

import pandas as pd
import yfinance as yf


DOWNLOAD_LOCK = RLock()

INTRADAY_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}


class YFinanceCollector:
    def fetch(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        frame = _download(
            tickers=ticker,
            interval=interval,
            period=period,
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        return _normalize_frame(frame)

    def fetch_many(self, tickers: list[str], interval: str, period: str,
                   workers: int = 4, timeout: float = 10) -> dict[str, pd.DataFrame]:
        tickers = list(dict.fromkeys(t.strip().upper() for t in tickers if t.strip()))
        if not tickers:
            return {}
        # Serialize top-level calls; yfinance manages its own bounded worker pool.
        with DOWNLOAD_LOCK:
            frame = _download(tickers=tickers, interval=interval, period=period,
                                auto_adjust=False, progress=False, group_by="ticker",
                                threads=workers if workers > 1 else False, timeout=timeout)
        result = {}
        for ticker in tickers:
            if frame is None or frame.empty:
                result[ticker] = pd.DataFrame()
            elif isinstance(frame.columns, pd.MultiIndex):
                if ticker in frame.columns.get_level_values(0):
                    result[ticker] = frame[ticker].dropna(how="all").copy()
                else:
                    result[ticker] = pd.DataFrame()
            elif len(tickers) == 1:
                result[ticker] = frame.dropna(how="all").copy()
            else:
                raise ValueError("Expected ticker-grouped columns for a multi-ticker download")
        return result

    def fetch_intraday(
        self,
        tickers: list[str],
        interval: str = "5m",
        period: str = "5d",
        prepost: bool = False,
    ) -> dict[str, pd.DataFrame]:
        if interval not in INTRADAY_INTERVALS:
            intervals = ", ".join(sorted(INTRADAY_INTERVALS))
            raise ValueError(f"Unsupported Yahoo Finance intraday interval: {interval}. Use one of: {intervals}.")

        normalized_tickers = [ticker.strip().upper() for ticker in tickers if ticker.strip()]
        if not normalized_tickers:
            raise ValueError("fetch_intraday requires at least one ticker.")

        frames = {}
        for ticker in normalized_tickers:
            frame = _download(
                tickers=ticker,
                interval=interval,
                period=period,
                prepost=prepost,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            frames[ticker] = _normalize_frame(frame)
        return frames


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    return frame


def _download(**kwargs):
    with DOWNLOAD_LOCK:
        return yf.download(**kwargs)
