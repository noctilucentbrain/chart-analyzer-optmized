from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
DERIVED_PRICE_COLUMNS = ["ohlc4", "hlcc4"]


@dataclass(frozen=True)
class CleaningReport:
    downloaded_rows: int
    cleaned_rows: int
    duplicate_rows: int
    missing_ohlc_rows: int
    gaps: list[dict[str, object]]


def clean_ohlcv(
    frame: pd.DataFrame,
    ticker: str,
    timeframe: str,
    interval: str,
) -> tuple[pd.DataFrame, CleaningReport]:
    downloaded_rows = len(frame)
    normalized = _normalize_columns(frame)

    if normalized.empty:
        cleaned = _empty_frame()
        return cleaned, CleaningReport(downloaded_rows, 0, 0, 0, [])

    normalized = normalized.reset_index()
    timestamp_column = _find_timestamp_column(normalized)
    normalized = normalized.rename(columns={timestamp_column: "timestamp"})

    required_columns = ["timestamp", *OHLCV_COLUMNS]
    missing_columns = [column for column in required_columns if column not in normalized.columns]
    if missing_columns:
        raise ValueError(f"Market data is missing columns: {', '.join(missing_columns)}")

    cleaned = normalized[required_columns].copy()
    cleaned["timestamp"] = pd.to_datetime(cleaned["timestamp"], utc=True)
    for column in OHLCV_COLUMNS:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")

    duplicate_rows = int(cleaned.duplicated().sum())
    cleaned = cleaned.drop_duplicates()

    valid_price_mask = cleaned[["open", "high", "low", "close"]].notna().all(axis=1)
    missing_ohlc_rows = int((~valid_price_mask).sum())
    cleaned = cleaned.loc[valid_price_mask].copy()
    cleaned["volume"] = cleaned["volume"].fillna(0)
    cleaned["ohlc4"] = (cleaned["open"] + cleaned["high"] + cleaned["low"] + cleaned["close"]) / 4
    cleaned["hlcc4"] = (cleaned["high"] + cleaned["low"] + cleaned["close"] + cleaned["close"]) / 4

    cleaned["ticker"] = ticker.upper()
    cleaned["timeframe"] = timeframe.lower()
    cleaned = cleaned.sort_values("timestamp").reset_index(drop=True)
    cleaned = cleaned[["ticker", "timeframe", "timestamp", *OHLCV_COLUMNS, *DERIVED_PRICE_COLUMNS]]

    gaps = detect_gaps(cleaned["timestamp"], interval)
    report = CleaningReport(
        downloaded_rows=downloaded_rows,
        cleaned_rows=len(cleaned),
        duplicate_rows=duplicate_rows,
        missing_ohlc_rows=missing_ohlc_rows,
        gaps=gaps,
    )
    return cleaned, report


def detect_gaps(timestamps: pd.Series, interval: str) -> list[dict[str, object]]:
    expected = _interval_to_timedelta(interval)
    if expected is None or len(timestamps) < 2:
        return []

    gaps = []
    ordered = pd.Series(pd.to_datetime(timestamps, utc=True)).sort_values().reset_index(drop=True)
    differences = ordered.diff()
    for index, difference in differences.items():
        if index == 0 or pd.isna(difference):
            continue
        if difference > expected * 1.5:
            gaps.append(
                {
                    "from": ordered.iloc[index - 1].to_pydatetime(),
                    "to": ordered.iloc[index].to_pydatetime(),
                    "observed_seconds": difference.total_seconds(),
                    "expected_seconds": expected.total_seconds(),
                }
            )
    return gaps


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    normalized.columns = [str(column).strip().lower().replace(" ", "_") for column in normalized.columns]
    rename_map = {
        "adj_close": "adj_close",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
    }
    return normalized.rename(columns=rename_map)


def _find_timestamp_column(frame: pd.DataFrame) -> str:
    candidates = ["timestamp", "datetime", "date", "index"]
    for candidate in candidates:
        if candidate in frame.columns:
            return candidate
    return frame.columns[0]


def _interval_to_timedelta(interval: str) -> pd.Timedelta | None:
    interval = interval.strip().lower()
    units = {
        "m": "min",
        "h": "h",
        "d": "D",
        "wk": "W",
    }
    for suffix, pandas_unit in units.items():
        if interval.endswith(suffix):
            amount = interval.removesuffix(suffix)
            if amount.isdigit():
                return pd.Timedelta(int(amount), unit=pandas_unit)
    if interval == "1mo":
        return pd.Timedelta(days=31)
    if interval == "3mo":
        return pd.Timedelta(days=93)
    return None


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["ticker", "timeframe", "timestamp", *OHLCV_COLUMNS, *DERIVED_PRICE_COLUMNS])
