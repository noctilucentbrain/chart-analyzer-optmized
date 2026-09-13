import pandas as pd

from chart_analyzer.cleaning import clean_ohlcv


def test_clean_ohlcv_normalizes_sorts_drops_duplicates_and_missing_prices():
    frame = pd.DataFrame(
        {
            "Open": [2, 1, 1, None],
            "High": [3, 2, 2, 5],
            "Low": [1, 0.5, 0.5, 4],
            "Close": [2.5, 1.5, 1.5, 4.5],
            "Volume": [20, None, None, 40],
        },
        index=pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-01", "2024-01-04"]),
    )

    cleaned, report = clean_ohlcv(frame, "aapl", "Daily", "1d")

    assert cleaned["ticker"].tolist() == ["AAPL", "AAPL"]
    assert cleaned["timeframe"].tolist() == ["daily", "daily"]
    assert cleaned["volume"].tolist() == [0, 20]
    assert cleaned["ohlc4"].tolist() == [1.25, 2.125]
    assert cleaned["hlcc4"].tolist() == [1.375, 2.25]
    assert report.downloaded_rows == 4
    assert report.cleaned_rows == 2
    assert report.duplicate_rows == 1
    assert report.missing_ohlc_rows == 1


def test_clean_ohlcv_reports_gaps_without_fabricating_rows():
    frame = pd.DataFrame(
        {
            "Open": [1, 2],
            "High": [2, 3],
            "Low": [0.5, 1],
            "Close": [1.5, 2.5],
            "Volume": [10, 20],
        },
        index=pd.to_datetime(["2024-01-01", "2024-01-05"]),
    )

    cleaned, report = clean_ohlcv(frame, "MSFT", "daily", "1d")

    assert len(cleaned) == 2
    assert len(report.gaps) == 1
