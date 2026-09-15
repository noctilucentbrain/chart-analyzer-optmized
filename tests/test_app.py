import pandas as pd

from chart_analyzer.app import _newer_candles, run_service
from chart_analyzer.config import DailyEvaluationConfig, AppConfig, MongoConfig, ServiceConfig, TimeframeConfig


def config():
    return AppConfig(
        daily_evaluation=DailyEvaluationConfig(enabled=False),
        mongodb=MongoConfig(uri="mongodb://localhost:27017", database="trading"),
        tickers=["AAPL"],
        timeframes=[
            TimeframeConfig(
                name="daily",
                interval="1d",
                period="1y",
                indicators=["rsi"],
            )
        ],
        indicators={"rsi": {"window": 14}},
        service=ServiceConfig(poll_interval_seconds=30),
    )


def test_run_service_runs_immediately_and_sleeps_between_runs(monkeypatch):
    calls = []
    sleeps = []

    def fake_run_latest(app_config):
        calls.append(app_config)

    monkeypatch.setattr("chart_analyzer.app.run_latest", fake_run_latest)

    run_service(config(), max_runs=2, sleep=sleeps.append)

    assert len(calls) == 2
    assert sleeps == [30]


def test_run_service_accepts_interval_override(monkeypatch):
    sleeps = []

    monkeypatch.setattr("chart_analyzer.app.run_latest", lambda app_config: None)

    run_service(config(), interval_seconds=5, max_runs=2, sleep=sleeps.append)

    assert sleeps == [5]


def test_newer_candles_without_existing_timestamp_returns_only_latest():
    candles = pd.DataFrame(
        {
            "timestamp": [
                pd.Timestamp("2024-01-01", tz="UTC"),
                pd.Timestamp("2024-01-02", tz="UTC"),
            ],
            "close": [1.0, 2.0],
        }
    )

    latest = _newer_candles(candles, None)

    assert latest["timestamp"].tolist() == [pd.Timestamp("2024-01-02", tz="UTC")]
