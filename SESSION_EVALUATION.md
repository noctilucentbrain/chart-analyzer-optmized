# Daily signals before the session close

This branch implements one evaluation per ticker, daily timeframe, strategy name
and exchange session. Production `main` and the live Argo CD Application are not
changed by pushing this branch.

## Timing and data

Daily (`interval: 1d`) work uses the configured exchange calendar, including DST,
holidays and early closes. The default is NYSE; this is an explicit assumption
for the US-stock universe, not automatic exchange detection. Set
`ticker_calendars` for tickers trading elsewhere. Unsupported calendar names fail
configuration validation. Normal US sessions evaluate at 15:00 New York time;
a 13:00 early close evaluates at noon.

The first poll at/after the target refreshes today's daily candle from Yahoo's
regular-session one-minute OHLCV bars and recomputes the daily indicators using
the full downloaded history. It compares today's updated indicators against the
previous completed session. Extended-session bars are excluded. The existing
volume condition and all indicator formulas are unchanged.

The minute series must start at the session open, have no missing minutes, and
end within `max_data_age_seconds` of the actual evaluation. This deliberately
skips delayed feeds, gaps, halted/illiquid tickers without continuous minute data,
and calendars with lunch breaks rather than generating a signal from incomplete
volume. `source_data_at` is the latest minute-bar timestamp, not a precise trade
or quote timestamp. The current minute bar may still be developing.

Failed/missing/stale data can retry until the deadline. Completed evaluations,
including false results, are never repeated within the same session. A MongoDB
atomic insert keyed by ticker/timeframe/session/strategy handles concurrent runs,
process restarts and ambiguous write retries. Changing strategy settings mid-session
does not permit a second decision under the same strategy name.

Each immutable document in `session_evaluations` contains the signal result,
`event_id`, reference price, current and previous candle/indicator snapshots,
configuration hash/settings, calendar, scheduled time, actual evaluation time,
data timestamp, fetch time, session close and expiration. Decisions are stored
in this separate collection so legacy event backfills cannot overwrite them.
There is no event/checkpoint dual-write gap: the decision document is both.

Finalization starts at close plus the configured delay. It updates closed daily
candles/indicators without generating signals. After the initial backfill it
writes new/recent rows rather than the entire history; EMA/RSI still use full
input history. `session_finalizations` records completion so unchanged sessions
are not downloaded on every poll. Missed finalizations catch up after restart,
including on weekends, but missed trading evaluations are never backfilled.
Initialization also only stores completed daily sessions and emits no scheduled
historical daily events.

## Configuration

In Helm values this lives under `appSetup`; in standalone YAML it is top-level:

```yaml
daily_evaluation:
  enabled: true
  default_calendar: NYSE
  ticker_calendars: {}  # Example: {VOD.L: LSE}
  minutes_before_close: 60
  max_lateness_minutes: 10
  event_ttl_minutes: 10
  stop_entries_minutes_before_close: 15
  max_data_age_seconds: 300
  finalization_delay_minutes: 15
```

Daily work runs before intraday work. Within the daily queue, evaluations take
priority over history maintenance. Every ticker is checked again after fetching
and computing: overdue work cannot create an actionable signal. The service
subtracts processing duration from its polling sleep in this mode. A poll
interval longer than the evaluation window is rejected. With 1,505 tickers,
provider latency can still cause missed windows; test a smaller universe first
and inspect `Daily evaluation skipped`, `deadline/freshness missed` and
`Daily decision` logs. This version does not guarantee simultaneous snapshots.
Initialize historical data before the first evaluation window to avoid spending
that window on a large first-time backfill.

Turning `enabled` off restores the prior append-only ingestion/event path for
rollback. Non-daily timeframes keep their existing behavior. The legacy
`event_detection_interval_seconds` remains unused as an independent scheduler.
The API does not auto-start polling: call `POST /service/start` after deployment
or restart, as before. Keep one replica/API worker; do not overlap initialization
with background ingestion.

## Trader contract

`GET /events/actionable?timeframe=daily&ticker=AAPL` returns only triggered,
unexpired session decisions; both filters are optional. The existing
`GET /timeframes/daily/latest` response also uses these filtered events when the
configured timeframe has interval `1d`. Historical/expired daily events from the
legacy `events` collection are excluded. Raw Mongo consumers must switch to
`session_evaluations` and apply the same filters; scheduled events are not copied
to the legacy collection.

Use `event_id` to deduplicate execution. Before submitting a position, the trader
must recheck `expires_at`, the exchange session, and a fresh executable quote
against `reference_price` using its configured tolerance. A buy comparison should
use the ask. Never use the daily candle's midnight `timestamp` as the decision
time, or replace `reference_price` with the subsequently finalized daily close.
Expiration is the earlier of evaluation time + TTL and close minus the entry
cutoff. Retrieving an event does not reserve it or extend expiration.

This branch changes the analyzer API/data contract, not the external trader or
portfolio-manager code. Their execution-time checks and durable order deduplication
must be verified before enabling automatic trading with this branch.

## Test deployment and rollback

Branch: `feature/session-close-daily-events`
Image tag: `0.16.0-session-close.1`

```sh
make build
make push
# Explicit opt-in: switches the existing test Application to this branch/image.
kubectl --context default apply -f argocd/application-session-close.yaml
# Then sync chart-analyzer-optimized in Argo CD.
```

The opt-in manifest keeps manual sync; pushing a commit alone does not deploy.
To switch back, apply `argocd/application.yaml` and sync: it still targets `main`
and image `0.15.0-optimized.1`. No collections must be deleted for rollback.
The additional decision/checkpoint collections remain as audit history. Back up
the test database before tests if you want to restore its candle values too.

Offline verification:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -c constraints.txt -e '.[dev]'
pytest -q
helm lint charts/chart-analyzer
```

Calendar source: https://pandas-market-calendars.readthedocs.io/en/latest/usage.html
