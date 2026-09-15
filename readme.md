> **Session evaluation branch:** See [SESSION_EVALUATION.md](SESSION_EVALUATION.md) for the new daily timing, immutable event API, test deployment and rollback. It supersedes the daily append-only behavior described below.

> **Optimized test version:** Start with [OPTIMIZATION.md](OPTIMIZATION.md) for deployment, benchmark results and limitations. The reference below describes the original API/CLI; use the new test deployment instructions instead of legacy deployment examples.

# Chart Analyzer

Python CLI application that collects OHLCV market data for configured tickers, cleans the candles, computes technical indicators, and stores candles plus indicator results in MongoDB.

The app uses `yfinance` as the market data source. Initialize MongoDB with the configured history:

```bash
chart-analyzer init-db --config examples/config.yaml
```

You can also run it as a Python module from the project directory:

```bash
python -m chart_analyzer init-db --config examples/config.yaml
```

## Features

- Reads tickers, timeframes, and indicators from YAML.
- Downloads OHLCV data, including volume, through `yfinance`.
- Cleans duplicate rows, invalid OHLC rows, numeric values, timestamps, and missing volume.
- Reports timestamp gaps in logs without creating synthetic candles.
- Computes MACD, RSI, EMA 50, EMA 200, volume SMA, Bollinger Bands, Volume at Price, OHLC4/HLCC4 standard deviation, support/resistance, and Volume Price Analysis.
- Stores cleaned candles and per-indicator results in MongoDB with unique indexes.

## Setup

Create a virtual environment and install the package:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

The `chart-analyzer` command is created by the editable install. If your shell says `command not found`, confirm the virtual environment is active and rerun:

```bash
pip install -e .
```

MongoDB must be running and reachable from the URI in the config. For a local default install, the URI is:

```yaml
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
  server_selection_timeout_ms: 5000
```

MongoDB settings can also be overridden with environment variables. Environment values take precedence over YAML:

```bash
export CHART_ANALYZER_MONGODB_URI="mongodb://mongo:27017"
export CHART_ANALYZER_MONGODB_DATABASE="trading"
export CHART_ANALYZER_MONGODB_SERVER_SELECTION_TIMEOUT_MS="5000"
```

## Configuration

See `examples/config.yaml` for a complete example.

```yaml
mongodb:
  uri: mongodb://localhost:27017
  database: trading_automation
  server_selection_timeout_ms: 5000

service:
  poll_interval_seconds: 300
  event_detection_interval_seconds: 300
  log_file: logs/chart-analyzer.log

tickers:
  - AAPL
  - MSFT

timeframes:
  - name: daily
    interval: 1d
    period: 1y
    indicators:
      - macd
      - rsi
      - ema_50
      - ema_200
      - volume_sma
      - bollinger_bands
      - volume_at_price
      - price_stddev
      - support_resistance
      - volume_price_analysis

indicators:
  rsi:
    window: 14
  ema_50:
    window: 50
  ema_200:
    window: 200
  volume_sma:
    window: 20
  bollinger_bands:
    window: 20
    standard_deviations: 2
  volume_at_price:
    lookback: 60
    price_bins: 24
  price_stddev:
    periods:
      - 20
      - 60
      - 120
  macd:
    fast: 12
    slow: 26
    signal: 9
  support_resistance:
    lookback: 60
    pivot_window: 3
  volume_price_analysis:
    volume_ma_window: 20

events:
  position_entry:
    timeframe: daily
    operator: AND
    conditions:
      macd_cross_over: {}
      ema50_above_ema200: {}
      rsi_between:
        min: 30
        max: 70
      volume_price_up:
        min_relative_volume: 0.9
        direction: up
  position_exit:
    timeframe: daily
    operator: OR
    conditions:
      macd_cross_under: {}
```

## MongoDB Collections

`candles` documents are unique by `ticker`, `timeframe`, and `timestamp`.

Candles include `ohlc4` and `hlcc4` derived price values:

- `ohlc4`: `(open + high + low + close) / 4`
- `hlcc4`: `(high + low + close + close) / 4`

Indicator documents are stored in a separate collection per indicator:

- `indicator_macd`
- `indicator_rsi`
- `indicator_ema_50`
- `indicator_ema_200`
- `indicator_volume_sma`
- `indicator_bollinger_bands`
- `indicator_volume_at_price`
- `indicator_price_stddev`
- `indicator_support_resistance`
- `indicator_volume_price_analysis`

Each indicator collection is unique by `ticker`, `timeframe`, and `timestamp`.

Triggered strategy events can also be stored in the `events` collection. Event documents are unique by `strategy`, `ticker`, `timeframe`, and `timestamp`.

Indexes are created automatically when the CLI runs.

## Reading Aggregated Candle Data

`MongoStorage.get_candle_with_indicators` returns one candle merged with all indicator values stored for the same ticker, timeframe, and timestamp.

```python
from chart_analyzer.storage import MongoStorage

storage = MongoStorage("mongodb://localhost:27017", "trading_automation")
document = storage.get_candle_with_indicators(
    ticker="AAPL",
    timeframe="daily",
    timestamp="2024-01-02T00:00:00Z",
)
storage.close()
```

The returned document keeps the candle fields at the top level and nests indicator values under `indicators`.

## Intraday Yahoo Finance Data

`YFinanceCollector.fetch_intraday` pulls intraday candles for one or more tickers. Supported intervals are `1m`, `2m`, `5m`, `15m`, `30m`, `60m`, `90m`, and `1h`.

```python
from chart_analyzer.collector import YFinanceCollector

collector = YFinanceCollector()
frames = collector.fetch_intraday(
    tickers=["AAPL", "MSFT"],
    interval="15m",
    period="5d",
    prepost=False,
)
```

The batch CLI can also ingest intraday candles by adding a timeframe with an intraday Yahoo interval, for example `interval: 15m` and `period: 5d`.

## Running As A Service

Initialize MongoDB with the full configured history before starting the latest-only service:

```bash
chart-analyzer init-db --config examples/config.yaml
```

You can also run one latest-only ingestion pass without starting the service:

```bash
chart-analyzer run-latest --config examples/config.yaml
```

Run the application continuously with the `service` command:

```bash
chart-analyzer service --config examples/config.yaml
```

The service runs one latest-only ingestion pass immediately, then sleeps for `service.poll_interval_seconds` before the next pass. Latest-only ingestion fetches the configured market-data window for indicator context, but only persists candles and indicator rows whose timestamps are newer than the latest candle already stored in MongoDB. If a ticker/timeframe has no stored candle yet, latest-only ingestion stores just the newest fetched candle.

```bash
chart-analyzer service --config examples/config.yaml --interval-seconds 60
```

Stop the service with Ctrl+C.

Each service run also evaluates configured event strategies for the newly stored candle range and persists only newly triggered events.

Logs are written to both stdout and `service.log_file`.

## REST API

Run the API:

```bash
chart-analyzer api --config examples/config.yaml --host 0.0.0.0 --port 8000
```

Endpoints:

- `GET /health`
- `GET /service/status`
- `POST /service/start`
- `POST /service/stop`
- `POST /database/init`
- `POST /latest/run`
- `GET /candles/{ticker}/{timeframe}/{timestamp}`
- `GET /timeframes/{timeframe}/latest`

Initialize MongoDB with the full configured history and configured events:

```bash
curl -X POST http://localhost:8000/database/init
```

Run one latest-only ingestion pass:

```bash
curl -X POST http://localhost:8000/latest/run
```

Start the background service through the API:

```bash
curl -X POST http://localhost:8000/service/start \
  -H "Content-Type: application/json" \
  -d '{"interval_seconds": 300, "event_interval_seconds": 300}'
```

Pull one aggregated candle:

```bash
curl "http://localhost:8000/candles/AAPL/daily/2024-01-02T00:00:00Z"
```

Pull the latest aggregated candles and latest stored events for a timeframe:

```bash
curl "http://localhost:8000/timeframes/daily/latest"
```

You can narrow the latest snapshot to one ticker or a subset of indicators:

```bash
curl "http://localhost:8000/timeframes/daily/latest?ticker=AAPL&indicators=rsi&indicators=macd"
```

## Docker

Build and run the service API image:

```bash
docker build -t chart-analyzer .
docker run --rm -p 8000:8000 -v "$PWD/logs:/app/logs" chart-analyzer
```

Inside Docker, `localhost` means the chart-analyzer container, not your host machine. Use one of these options:

- Run the included Compose stack:

```bash
docker compose up --build
```

- Or, when connecting to MongoDB on the host, mount a config whose Mongo URI uses a Docker-reachable hostname such as `host.docker.internal` where supported:

```yaml
mongodb:
  uri: mongodb://host.docker.internal:27017
  database: trading
```

The API returns `503` when MongoDB is unreachable. The interactive docs are at `/docs`; `/doc` redirects there.

## Helm On k3s

The daily values file enables a Traefik ingress for k3s:

```yaml
ingress:
  enabled: true
  className: traefik
  hosts:
    - host: chart-analyzer.local
      paths:
        - path: /
          pathType: Prefix
```

Install or upgrade the chart:

```bash
helm upgrade --install chart-analyzer charts/chart-analyzer \
  -f charts/chart-analyzer/values-daily.yaml
```

Point `chart-analyzer.local` at a k3s node IP in DNS or `/etc/hosts`, then open:

```text
http://chart-analyzer.local/docs
```

## Detecting Strategy Events

Event strategies are configured under `events`. Each strategy can combine enabled conditions with `AND` or `OR`.

Available conditions:

- `macd_cross_over`: previous MACD is at or below signal and current MACD is above signal
- `macd_cross_under`: previous MACD is at or above signal and current MACD is below signal
- `ema50_above_ema200`: EMA 50 is above EMA 200
- `rsi_between`: RSI is inside the configured range
- `volume_price_up`: relative volume is above the configured threshold and direction matches

MACD indicator values also include `histogram_direction`, which is `rising`, `falling`, `flat`, or `unknown` compared to the previous candle for the same ticker and timeframe.

Example:

```python
from chart_analyzer.config import load_config
from chart_analyzer.storage import MongoStorage

config = load_config("examples/config.yaml")
storage = MongoStorage(config.mongodb.uri, config.mongodb.database)
events = storage.detect_events(config.events[0], ticker="AAPL")
storage.close()
```

Each returned event includes `strategy`, `ticker`, `timeframe`, `timestamp`, `operator`, and per-condition boolean results.

To persist triggered events into the `events` collection, pass `save=True`:

```python
events = storage.detect_events(config.events[0], ticker="AAPL", save=True)
```

## Development

Run tests with:

```bash
pytest
```
