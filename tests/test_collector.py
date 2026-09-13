import pandas as pd
import pytest

from chart_analyzer.collector import YFinanceCollector


def test_fetch_intraday_downloads_each_ticker(monkeypatch):
    calls = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(
            {
                "Open": [1.0],
                "High": [2.0],
                "Low": [0.5],
                "Close": [1.5],
                "Volume": [100],
            },
            index=pd.to_datetime(["2024-01-01 09:30:00"]),
        )

    monkeypatch.setattr("chart_analyzer.collector.yf.download", fake_download)

    frames = YFinanceCollector().fetch_intraday(["aapl", " MSFT "], interval="15m", period="5d", prepost=True)

    assert set(frames) == {"AAPL", "MSFT"}
    assert calls[0]["tickers"] == "AAPL"
    assert calls[0]["interval"] == "15m"
    assert calls[0]["period"] == "5d"
    assert calls[0]["prepost"] is True
    assert frames["AAPL"]["Close"].tolist() == [1.5]


def test_fetch_intraday_rejects_non_intraday_interval():
    with pytest.raises(ValueError, match="Unsupported Yahoo Finance intraday interval"):
        YFinanceCollector().fetch_intraday(["AAPL"], interval="1d")
