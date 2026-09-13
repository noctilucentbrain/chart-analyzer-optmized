from click.testing import CliRunner

from chart_analyzer import cli as cli_module


def test_cli_smoke(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
service:
  log_file: {tmp_path / "run.log"}
tickers:
  - AAPL
timeframes:
  - name: daily
    interval: 1d
    period: 1y
    indicators:
      - rsi
indicators:
  rsi:
    window: 14
""",
        encoding="utf-8",
    )
    called = {}

    def fake_run(app_config):
        called["tickers"] = app_config.tickers

    monkeypatch.setattr(cli_module, "run_app", fake_run)

    result = CliRunner().invoke(cli_module.cli, ["run", "--config", str(config)])

    assert result.exit_code == 0
    assert called == {"tickers": ["AAPL"]}


def test_service_cli_smoke(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
service:
  poll_interval_seconds: 60
  log_file: {tmp_path / "service.log"}
tickers:
  - AAPL
timeframes:
  - name: daily
    interval: 1d
    period: 1y
    indicators:
      - rsi
indicators:
  rsi:
    window: 14
""",
        encoding="utf-8",
    )
    called = {}

    def fake_run_service(app_config, interval_seconds=None, event_interval_seconds=None):
        called["tickers"] = app_config.tickers
        called["interval_seconds"] = interval_seconds
        called["event_interval_seconds"] = event_interval_seconds

    monkeypatch.setattr(cli_module, "run_service_app", fake_run_service)

    result = CliRunner().invoke(
        cli_module.cli,
        [
            "service",
            "--config",
            str(config),
            "--interval-seconds",
            "10",
            "--event-interval-seconds",
            "20",
        ],
    )

    assert result.exit_code == 0
    assert called == {
        "tickers": ["AAPL"],
        "interval_seconds": 10.0,
        "event_interval_seconds": 20.0,
    }


def test_init_db_cli_smoke(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
service:
  log_file: {tmp_path / "init.log"}
tickers:
  - AAPL
timeframes:
  - name: daily
    interval: 1d
    period: 1y
    indicators:
      - rsi
indicators:
  rsi:
    window: 14
""",
        encoding="utf-8",
    )
    called = {}

    def fake_initialize(app_config):
        called["tickers"] = app_config.tickers

    monkeypatch.setattr(cli_module, "initialize_database", fake_initialize)

    result = CliRunner().invoke(cli_module.cli, ["init-db", "--config", str(config)])

    assert result.exit_code == 0
    assert called == {"tickers": ["AAPL"]}


def test_run_latest_cli_smoke(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
service:
  log_file: {tmp_path / "latest.log"}
tickers:
  - AAPL
timeframes:
  - name: daily
    interval: 1d
    period: 1y
    indicators:
      - rsi
indicators:
  rsi:
    window: 14
""",
        encoding="utf-8",
    )
    called = {}

    def fake_run_latest(app_config):
        called["tickers"] = app_config.tickers

    monkeypatch.setattr(cli_module, "run_latest_app", fake_run_latest)

    result = CliRunner().invoke(cli_module.cli, ["run-latest", "--config", str(config)])

    assert result.exit_code == 0
    assert called == {"tickers": ["AAPL"]}
