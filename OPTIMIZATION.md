> **Session evaluation branch:** See [SESSION_EVALUATION.md](SESSION_EVALUATION.md) for the new daily timing, immutable event API, test deployment and rollback. It supersedes the daily append-only behavior described below.

# Optimized chart analyzer

This is a separate working copy of the original service. The original repository has not been modified. Start with the deployment steps below; the original API/CLI reference is in `readme.md`.

## What changed

- Download tickers in chunks of 20 with four yfinance download threads. Only one chunk is retained at a time. Both initialization and recurring ingestion use batching. Top-level yfinance calls are serialized within the process to avoid overlapping downloader state.
- Preserve full candle history for indicator warm-up, but only construct indicator documents for new candles. Volume profiles are computed only at requested timestamps, using the original lookback and binning formula. EMA, RSI, centered support/resistance and other indicator formulas are preserved.
- Event detection for a specific ticker/timeframe reads new candles plus one predecessor. Indicators are fetched in batches of up to 500 exact candle keys, replacing a separate database round trip per candle and indicator. Full-history detection remains available for initialization and queries without a specific series.
- Latest snapshots use MongoDB aggregation to select the newest candle/event per ticker/strategy before transferring results. Added indexes support these queries. Indicator indexes are created once per storage instance. Latest timestamp queries explicitly limit results to one.
- Mongo bulk writes are split into at most 1,000 operations. Indicators are saved before candle watermarks, so an indicator write failure does not advance the candle watermark.
- Duplicate configured tickers are removed. The six original values files contained 1,573 entries, representing 1,505 unique tickers. Default Helm values combine these into one deployment.
- Added ingestion cycle duration and series-count logging. Missing downloads are logged and retried on the next cycle.
- New test defaults use a dedicated database, secret, release, image tag and namespace; public routing is disabled. Removed copied production deployment/start scripts and the lifecycle hook that printed configuration. Docker Compose uses its own named database volume.

The downloader uses yfinance's documented [thread and timeout options](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html). Latest queries use MongoDB's documented [sort/group/first optimization pattern](https://www.mongodb.com/docs/manual/reference/operator/aggregation/group/).

## Measured result

`benchmarks/results.json` records an offline comparison against the original indicator module:

| Workload | Original | Optimized |
| --- | ---: | ---: |
| 756 historical candles, all 10 indicators, one new candle | 264.3 ms | 31.7 ms |

This is an **8.35× indicator CPU speedup**, median of three runs. Full-history outputs and selected-row outputs matched the original. This does not measure Yahoo downloads, real MongoDB latency, pod memory, or end-to-end processing of all 1,505 symbols.

For illustration, with 756 stored candles and five required indicators, the old event path issued 4,537 find/find-one calls per strategy and ticker. For one new candle, the new path issues seven: two candle queries and five batched indicator queries. This is a code-path count, not a measured live MongoDB benchmark.

## Deploy to the test namespace

From this folder, build and push to the installed local registry, `registry.ibis-silverside.ts.net` (reachable through Tailscale). The Makefile defaults to this registry; override `REGISTRY` or `IMAGE` if needed:

```sh
make build
make push
kubectl create namespace chart-analyzer-optimized
```

MongoDB credentials are now managed by the chart's Vault Secrets Operator
integration. Run `python3 scripts/configure-vault-mongodb.py` once (or after
source credential rotation); see [argocd/VAULT.md](argocd/VAULT.md) for details.
Do not manually create the destination Secret when Vault synchronization is enabled.

The configured database is `chart_analyzer_optimized`, including an environment override. Its credentials must allow access to that database. MongoDB itself is not deployed by this chart. Use a separate Mongo instance as well if you need infrastructure-level performance isolation.

```sh
helm upgrade --install chart-analyzer-optimized charts/chart-analyzer \
  --namespace chart-analyzer-optimized \
  -f charts/chart-analyzer/values-optimized.yaml \
  --set image.repository=registry.ibis-silverside.ts.net/chart-analyzer-optimized \
  --set-string image.tag=0.15.0-optimized.1
kubectl -n chart-analyzer-optimized rollout status deployment/chart-analyzer-optimized
kubectl -n chart-analyzer-optimized port-forward service/chart-analyzer-optimized 8001:8000
```

In another terminal, initialize historical data once, then start polling:

```sh
curl --fail -X POST http://localhost:8001/database/init
curl --fail -X POST http://localhost:8001/service/start
curl --fail http://localhost:8001/service/status
```

Initialization is synchronous and can take a long time for the full universe. Wait for it to finish before starting polling. The API retains the original explicit start behavior, so start it again after a pod restart. Use one replica and one API worker: there is no distributed ingestion ownership or leader election.

For a smaller first trial, create a values override such as:

```yaml
appSetup:
  tickers: [AAPL, MSFT, SPY]
```

Pass it as the last `-f` argument. The full combined universe is already in `charts/chart-analyzer/values.yaml`.

## Tuning and comparison

```yaml
appSetup:
  service:
    download_batch_size: 20
    download_workers: 4
    download_timeout_seconds: 10
    poll_interval_seconds: 300
```

Batch size controls peak download memory; worker count controls concurrent Yahoo requests. Begin with these defaults. Increase workers gradually only after observing throttling, empty downloads, CPU and memory. A timeout applies to downloader requests, not the entire ingestion cycle. Yahoo still receives per-symbol requests internally; batching does not eliminate provider limits.

The default resource request is 0.5 CPU / 512 MiB and limit is 2 CPU / 2 GiB. These are starting values, not measured capacity requirements. The service still sleeps for the poll interval after each completed cycle, preserving original cadence. `event_detection_interval_seconds` retains its original behavior: events run inline with ingestion; it is not an independent scheduler.

Compare the original and optimized deployment using the same ticker subset, indicator settings and database history. Inspect `Ingestion cycle seconds=...`, missing-download warnings, pod CPU/memory, and latest timestamps over several market-open cycles. Then increase the ticker count. Match stored indicators and events by ticker/timeframe/timestamp before switching consumers. Helm manifests have been rendered and linted, but no cluster deployment was performed.

## Validation and reproduction

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -c constraints.txt -e '.[dev]'
pytest -q
python benchmarks/benchmark.py --baseline /path/to/original/chart-analyzer
helm lint charts/chart-analyzer
helm template chart-analyzer-optimized charts/chart-analyzer --namespace chart-analyzer-optimized
```

**Local results:** 79 tests passed; the Python wheel built successfully; Helm lint, rendering and rendered deployment-isolation checks passed.

Tests cover every indicator's selected-row parity, sparse/non-default indices, empty selections, crossover boundaries, batched query size, naive Mongo timestamps, missing symbols, download chunking, configuration limits and bounded writes. Mongo tests use fakes/mongomock. The local runtime was Python 3.11; Docker targets Python 3.12. `constraints.txt` records the versions used for these checks and is applied during image builds.

Docker was not running locally, so image build/run and real MongoDB integration were not validated. No Yahoo requests or production database operations were used in the tests.

## Preserved behavior and remaining limits

To avoid silently changing signals, this version continues downloading the configured history window; it does not truncate EMA/RSI warm-up or introduce approximate cached state. Full historical initialization still calculates and saves all requested rows.

The original append-only latest-candle policy is preserved: a brand-new series ingests only its latest candle unless initialized, and already stored timestamps are not refreshed. Consequently, active-bar revisions and historical corrections remain outside the ingestion policy. The existing centered support/resistance calculation is also unchanged.

Writes across candles, indicators and events are not transactional. An event-write failure after candle ingestion can require rerunning event detection through the existing CLI. Do not overlap manual initialization/latest-run requests with the background loop. These are existing correctness/operational constraints, not addressed by the performance changes.

To stop the trial, stop the service or remove its release; keep the database for comparison:

```sh
curl --fail -X POST http://localhost:8001/service/stop
helm uninstall chart-analyzer-optimized --namespace chart-analyzer-optimized
```
