# Phase 2 architecture and analytical methods

## Inspection and compatibility

The starting repository was commit `2584d07` (Phase 1), with 39 deterministic tests,
SQLAlchemy models, migration `0001`, `eri ingest/counts/summary/quality`, and a
latest-50 Status API adapter. There was no scheduler, incident archive, coverage
evidence, or market source. Existing behavior and tables are preserved. Phase 2
adds migration `0002` and modules under `enterprise_risk.market`; it reuses the
Phase 1 execution audit and PostgreSQL upsert helper. No frontend or Phase 3 work.

## Data lineage and grains

| Entity/output | Grain | Role |
| --- | --- | --- |
| `incidents` | source, source ID | Phase 1 source record, with JSONB snapshot |
| `components`, `incident_components` | source identifiers | Phase 1 normalized relationships |
| `ingestion_runs` | run ID | Existing audit; one new run per market product attempt |
| `market_products` | product ID | Verified public product, base/quote currency, raw metadata |
| `market_candles` | product, UTC start, granularity | Finalized source OHLCV; `NUMERIC(38,18)`; audit FK |
| `market_risk_indicators` | product, UTC date, calculation version | Rebuildable float statistics and explicit thresholds |
| `incident_coverage` | reviewed source/date interval | External evidence and reviewer; end exclusive |
| `operational_incident_facts` | source, incident ID | SQL view aggregates components before integration |
| `market_daily_returns` | product, UTC date | SQL view with gap-aware daily/log returns and volume change |
| `incident_market_context.csv` | source, incident, product | UTC lags 0/1/3 and seven preceding days |
| `event_records.csv` | source, incident, product, offset | Days −7 through +7, with missingness retained |

No source incidents are copied into a derived database table. Incident updates
are never joined into counts. Components remain lists on the incident view.
Adding component strata does not create independent incidents; strata overlap.
Daily indicators have an indexed date and versioned configuration hash. New market
writes invalidate that product's derived rows. Indicator recomputation atomically
replaces one version over all stored finalized candles, including after backfills.
A shared transaction advisory lock serializes market writes and transformations.
Reports read one repeatable-read snapshot after recomputation. Concurrent market
writes that invalidate indicators appear as a critical quality failure, rather
than allowing an internally inconsistent report to pass quality checks.

## API and ingestion contract

Public endpoint:
`GET https://api.coinbase.com/api/v3/brokerage/market/products/{product_id}/candles`.
Parameters: `start`, `end` (Unix seconds), `granularity=ONE_DAY`, `limit=350`.
Responses contain `candles` with string `start/open/high/low/close/volume`.
Public product retrieval verifies `product_id`, `base_currency_id` and
`quote_currency_id` before candle ingestion. No key or subscription is used.

Time windows are UTC, half-open `[start,end)`, capped at today's midnight. The
request sends `end−1 second` to avoid dependence on endpoint end inclusivity.
Windows contain at most 300 daily buckets, below the expected 350-candle limit.
Missing dates and rejected out-of-window observations are audited; an empty
response never becomes an invented candle. No interpolation or assumed zero
volume. Actual earliest/latest source coverage is derived from stored observations,
not from requested dates or product age. The 350 limit and live contract need a
live acceptance check in an environment allowed to reach the endpoint.

The default starts 90 days before the earliest stored incident (or 455 days before
end if there are no incidents). This allows rolling returns, volatility and
prior-only stress baselines. Incremental updates find the earliest missing day
within the requested range and reconcile at least the last seven completed days.
An unavailable early interval is retried until the analyst narrows the range;
this favors visible gaps over silently declaring a complete backfill.

Four attempts, 30-second request timeout, at least 0.2-second pacing, bounded
exponential backoff for transport errors/429/500/502/503/504. Honor `Retry-After`
seconds or HTTP date; values beyond 60 seconds abort with a rerun instruction.
Other HTTP errors and malformed envelopes fail the product. Individual malformed
candles are rejected and audited; valid candles commit in one product transaction.
Exact duplicate response rows are disclosed; conflicting duplicates abort the
product. Repeated ingestion upserts the same keys. Received/processed counts mean
observations handled, not newly inserted records. Audit rows intentionally grow.

## Indicator definitions (`daily-v1-*`)

- Return: `close[t]/close[t−1]−1`; log return: `ln(close[t]/close[t−1])`.
  Fractions in datasets, percentages only on plots. Require adjacent UTC dates.
- Absolute return: `abs(return)`.
- Volatility: sample standard deviation (`ddof=1`) of 7 or 30 consecutive daily
  log returns, multiplied by `sqrt(365)` by default. First 30-day value requires
  31 consecutive closes. Gaps break windows; warmup remains null.
- Volume change: `volume[t]/volume[t−1]−1`; previous zero yields null.
- Volume mean and z-score: **prior 30 days**, excluding day t; sample standard
  deviation. Zero variance yields null. Each product is standardized separately.
  Candle volume is base units, not USD value, so raw cross-product comparisons
  are inappropriate.
- Stress: absolute return above its prior 30-day 95th percentile **or** 30-day
  volatility above its prior 30-day 95th percentile **or** prior-normalized volume
  z-score above 2. Quantile uses linear interpolation of the observed distribution;
  this is not interpolation of missing candles. Baseline length, quantile and z
  threshold are configurable and included in the version hash and every row.
  Strict `>` comparison. True if any criterion fires; false only when all three
  are known and false; otherwise null. Full volatility baseline needs 61 closes.

## Incident coverage, timing and filtering

The Status API has only the latest 50 incidents. Successful snapshots, or even
matching IDs across snapshots, do not prove a complete incident archive. The
coverage assessment defaults to **partial observations**. Daily incident rates
and correlation tests use only the union of analyst-reviewed complete intervals
from `incident_coverage`. Missing dates outside those intervals never count as
zero. No interval is created automatically by ingestion. An attestation must cite
independent evidence for complete ascertainment and identify the reviewer; it is
not a convenience override for obtaining statistics.

Maintenance is excluded when raw `scheduled_for`/`scheduled_until` is populated,
raw `type=maintenance`, or status is scheduled/in_progress/verifying. The original
raw records are retained. Unknown classifications remain operational observations
with that limitation. Names alone do not exclude maintenance. Dates use incident
creation, not necessarily actual outage onset. Duration is resolved minus created
in hours, only when resolution is valid; ongoing incidents are right-censored,
with no invented resolution or duration. Missing severity stays `unknown`.

Day 0 is retrospective: its completed candle was not available at incident onset.
Lags −1 and −3 and the exact UTC days −7..−1 are preceding observations. The
seven-day mean requires all seven values. Event offsets are −7..+7. The requested
analysis range bounds observations; offsets outside it stay missing even if other
dates are stored. Use a wider `--start/--end` for boundary events. Product contexts
are not independent samples of the incident population.

## Exploratory methods and gates

- Offset means/medians require at least three observed incidents per product,
  metric and offset. Every output reports eligible, observed and missing counts.
- Pre/post contrasts exclude day 0 and require all 3 or 7 values on each side.
  Sensitivities include impact, component, nonoverlapping incidents, network-keyword
  exclusions, top-1% absolute-change exclusions, and incident week counts.
- Frequency groups report actual day exposure, incidents, days with an incident,
  and incidents per day. Rate ratios/differences require at least ten days and five
  incidents in each group. There is no independent-Poisson confidence interval:
  incident clustering makes that assumption dubious.
- Spearman rho at lags 0/1/3/7 requires >=30 pairs, >=5 incidents and nonconstant
  inputs. A 95% percentile interval uses 500 moving-block bootstrap draws (7 days,
  seed 2026), at least 400 nonconstant replicates and contiguous paired dates.
  With gaps, rho is descriptive and the interval is withheld. Longer dependence,
  weak stationarity and selection remain unresolved. No confirmatory IID p-values.
- Critical source quality failures block frequency statistics. Insufficient or
  unavailable data yields explicit status fields, never invented estimates.

No causality, prediction, operational significance or calibrated risk score is
claimed. Network keywords are unvalidated hints; service groups and event windows
can overlap. Multiple comparisons, reporting practices, market-wide common shocks,
and missing incident histories limit every exploratory interpretation.

## Quality, reproducibility and expected outputs

`market-quality` checks requested daily coverage, duplicates, OHLC, finite numeric
values, UTC alignment, partial days, indicator warmup, source/derived completeness,
incident dates/durations/severity, and coverage evidence. Reports additionally
check incident/product cardinality and missing aligned market observations.
Critical errors are JSON fields and CLI exit code 2. Gaps and incomplete incident
history are visible warnings; unverified history blocks frequency analysis.

`market-report` and `market-analysis` regenerate indicators and the same artifact
bundle. `source_snapshot.json` contains the exact analytical inputs from PostgreSQL,
including audit failures and coverage evidence. `manifest.json` records inputs,
parameters, dates and SHA-256 output hashes. `market-render --snapshot` reproduces
CSV values, statistical JSON and report text without a DB or live API. Plots use
NaN gaps instead of visually joining missing observations. Snapshot replay is
covered by tests. Reading a saved snapshot does not insert records into a database.

A restored database is still required for new ingestion and recomputation. The
saved analytical snapshot is not a full replacement for a PostgreSQL backup.

Incident-level duration Spearman analysis is available even with partial incident
coverage, but is conditional on observed resolved incidents. It uses lag-1 market
indicators, requires ten valid pairs, and reports omitted/unresolved counts. Optional
95% calendar-week cluster bootstrap intervals require at least eight distinct weeks
and 400 valid replicates out of 500 (seed 2026). Independence between weeks remains
an assumption; censoring, public-report selection and business impact are unresolved.
