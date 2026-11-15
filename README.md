# Enterprise Risk Intelligence

A reproducible exploratory risk-analysis project using Coinbase public operational
incidents and daily cryptocurrency market data. Independent of Coinbase. No trading,
price prediction, frontend, authentication, ML framework or composite risk score.

Phase 1 ingestion remains available. Phase 2 adds market ingestion, calendar-aware
indicators, coverage-gated incident analysis, CSV/JSON exports and Matplotlib charts.

**Current empirical limitation:** live Status ingestion succeeded with 50 records.
This environment blocks `api.coinbase.com` and `docs.cdp.coinbase.com`, so no real
market candles were collected. The [generated report](reports/phase2_market_incident_analysis.md)
shows the missing data and withholds unsupported conclusions. The adapter is tested
with deterministic fixtures and PostgreSQL, and its parameters/response fields were
checked against Coinbase's official SDK; live market acceptance remains pending.
See [source verification](docs/phase2_source_verification.md).

## Architecture

```text
Coinbase Status -> Phase 1 validation -> incidents / components / updates
Coinbase public products/candles -> exact Decimal validation -> market_candles
                         both -> ingestion_runs audit
market_candles -> versioned indicators -> incident context / event windows
coverage evidence -> explicit frequency gates -> statistics / report / charts
```

Python 3.13, PostgreSQL 17, SQLAlchemy 2, Alembic, HTTPX, NumPy/SciPy and Matplotlib.
No credentials are needed for public sources. Dependencies are transitively pinned
with hashes in `requirements.lock`. Docker and CI use Python 3.13.5.

Migration `0002` preserves all Phase 1 tables and adds:

- `market_products`: product identity, base/quote currencies, source metadata.
- `market_candles`: unique daily product/start/granularity keys, `NUMERIC(38,18)`
  OHLCV, UTC timestamps, source and ingestion-run lineage, OHLC constraints.
- `market_risk_indicators`: product/date/version keys, returns, rolling volatility,
  relative volume, stress flags, thresholds and calculation parameters.
- `incident_coverage`: reviewed complete-observation intervals with evidence.
- SQL views `operational_incident_facts` and `market_daily_returns` for incident
  facts without component fanout and independently reproducible base returns.

[Technical methods and data lineage](docs/phase2_architecture.md) explain formulas,
calendar windows, filtering, sample-size gates, uncertainty and unresolved assumptions.

## Run in Windows PowerShell with Docker

Use Docker Desktop with Linux containers and Compose v2, from the repository root.
If `.env` does not exist, create it and choose a unique local database password:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
docker compose build
docker compose up -d --wait db
docker compose run --rm app alembic upgrade head

# Existing Phase 1 commands:
docker compose run --rm app eri ingest
docker compose run --rm app eri counts
docker compose run --rm app eri summary
docker compose run --rm app eri quality

# Default products: BTC-USD ETH-USD SOL-USD.
# Default start: 90 days before earliest incident; default end: today's UTC midnight.
docker compose run --rm app eri market-ingest
docker compose run --rm app eri market-update
docker compose run --rm app eri market-indicators
docker compose run --rm app eri market-quality
docker compose run --rm app eri market-analysis
docker compose run --rm app eri market-report
```

The `reports` directory is bind-mounted, so exports survive `--rm`. The analysis
and report commands generate the same complete bundle; running either is sufficient.
PostgreSQL persists in a named Docker volume. `docker compose down` stops services
without removing data. Do not use `down -v` unless deliberately erasing database data.

For a fixed historical period and configurable thresholds:

```powershell
docker compose run --rm app eri market-ingest --products BTC-USD ETH-USD SOL-USD --start 2026-06-21 --end 2026-11-15
docker compose run --rm app eri market-report --products BTC-USD ETH-USD SOL-USD --start 2026-06-21 --end 2026-11-15 --baseline-days 30 --quantile 0.95 --volume-z 2.0
```

Dates are UTC and **end is exclusive**, capped at today's midnight. Only completed
daily candles enter finalized analytics. Widen the analysis period to include event
windows and adequate warmup. Use the same indicator options for `market-quality`
as the preceding calculation; report/analysis commands recalculate automatically.
Incremental updates retry earlier gaps and refresh at least the last seven days.
No data is interpolated. Repeated writes cannot duplicate source or indicator keys.

### Native Python in PowerShell

Install Python 3.13 and use a running local PostgreSQL instance (or the Compose DB):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
$env:DB_HOST = "localhost"
$env:DB_PORT = "5432"
$env:DB_USER = "eri"
$env:DB_NAME = "eri"
$secret = Read-Host "Local database password" -AsSecureString
$env:DB_PASSWORD = [System.Net.NetworkCredential]::new("", $secret).Password
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\eri ingest
.\.venv\Scripts\eri market-ingest
.\.venv\Scripts\eri market-update
.\.venv\Scripts\eri market-indicators
.\.venv\Scripts\eri market-quality
.\.venv\Scripts\eri market-report
```

Alternatively set `DATABASE_URL` with the `postgresql+psycopg` driver and properly
escaped credentials. Separate `DB_*` settings avoid URL escaping. Never commit `.env`.
Changing Compose credentials after database creation does not rotate existing roles.

### Replay the delivered report without APIs or a database

The saved report is based on actual stored incident data and recorded market-source
failures. This replay requires only installed Python dependencies:

```powershell
.\.venv\Scripts\eri market-render --snapshot reports/source_snapshot.json --output reports/replayed
# Or through Docker (no database connection used by market-render):
docker compose run --rm --no-deps app eri market-render --snapshot reports/source_snapshot.json --output reports/replayed
```

Replay deliberately exits **2** for the delivered snapshot because market data is
absent. It still produces all artifacts. That status is a data-quality result,
not a failed report-generation operation. JSON, CSV and report text replay are tested.
`manifest.json` records date/configuration parameters, source snapshot hash and artifact
hashes. Snapshots contain public incident facts and audit metadata, not credentials.

## Analytical outputs

| File in `reports/` | Purpose |
| --- | --- |
| `phase2_market_incident_analysis.md` | Facts, interpretations, limitations and next investigations |
| `daily_market_indicators.csv` | Daily product/version observations and thresholds |
| `incident_market_context.csv` | UTC incident dates, duration, severity, components and market lags |
| `event_records.csv`, `event_summary.csv` | Event-level windows and descriptive aggregates |
| `quality_summary.json` | Critical failures, gaps, null indicators and join checks |
| `incident_coverage.json` | Observed dates, complete/unverified days and reviewed evidence |
| `statistical_results.json` | Coverage-gated rates, lagged Spearman, duration and sensitivity results |
| `market_volatility.png` | Rolling market volatility, with missing-data labels |
| `incident_market_stress.png` | Incident dates and market stress, explicitly retrospective |
| `event_comparison.png` | Market conditions around incident dates and sample sizes |
| `source_snapshot.json`, `manifest.json` | Exact analytical input and reproducibility hashes |

No real market observations means an empty indicator CSV with headers and clearly
labeled unavailable market plots. This is preferable to claiming fixture-based
results are empirical findings. All automated market fixtures are synthetic; tests
never depend on a live API or mix fixture records into the real observation database.

## Interpretation and coverage safeguards

The central question is whether market stress coincides with operational incidents,
not whether prices can be predicted. Daily returns are fractions; plots show percent.
Volatility uses log returns, sample standard deviation and `sqrt(365)` annualization.
Volume is **base-asset units**, normalized within each product using prior history.
Stress thresholds use preceding rolling quantiles and an explicit volume z threshold.
Insufficient history and zero variance remain null.

Same-day completed candles are retrospective, never advance warning. Preceding-day
context respects UTC boundaries. Counts are not multiplied by components or updates.
Maintenance is excluded only through explicit source classifications; filters and
ongoing/invalid durations are disclosed. Event windows may overlap and are not
independent samples. Duration associations are conditional on observed resolved events.

The incident feed returns only its **50 latest incidents**. Missing records do not
establish zero-incident days. Frequency analysis is withheld unless independent
complete-observation evidence has been reviewed. To record real reviewed evidence,
create a JSON file outside the generated artifact names with these fields:

```json
{
  "source": "coinbase_status",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "complete": true,
  "evidence": "Specific independently reviewed archival evidence and location",
  "reviewed_by": "Actual reviewer"
}
```

Replace placeholders with actual evidence. This is not an assertion that such evidence
exists. Place the file in `reports/coverage-evidence.json` for Docker access, then run:

```powershell
docker compose run --rm app eri coverage-import --coverage-file reports/coverage-evidence.json
```

Repeated interval registration is idempotent; overlapping reviewed intervals are
unioned, not double counted. No coverage evidence was registered for the delivered
live sample. Correlation is exploratory, not causal or operational significance.

## Tests and CI

```powershell
docker compose --profile test run --build --rm tests
docker compose run --rm app ruff check .
docker compose run --rm app ruff format --check .
docker compose run --rm app alembic check
# Native unit tests only:
.\.venv\Scripts\pytest -q -m "not integration"
```

Integration tests use `TEST_DATABASE_URL` pointing to a **dedicated disposable**
PostgreSQL database and truncate target tables. Never point it at useful data.
Without that variable PostgreSQL tests are skipped, not passed. CI supplies a separate
PostgreSQL 17 service, checks lint/format, all tests, schema drift, migration roundtrips
and Compose configuration. GitHub-hosted CI and a Windows host have not been run here.
See [actual verification results](docs/phase2_validation.md).

Dependencies can be deliberately regenerated with:
`uv pip compile pyproject.toml --extra dev --generate-hashes -o requirements.lock`.
Hash-locked installs do not resolve dependencies during normal builds.

## Audit and command exit behavior

Phase 1 source records retain raw JSONB and UTC timestamps. Newer incident snapshots
cannot be overwritten by older updates; absent component lists preserve existing
relationships, explicit lists replace them, and omitted updates remain stored.
Valid records commit atomically; child validation failures reject their incident.
`processed` counts accepted snapshots, not newly inserted incidents. Phase 1 `quality`
displays the latest 20 ingestion runs, now including market runs.

Market audit rows persist product/range, received/processed/rejected counts, gaps,
completion status and safe transport errors. HTTP requests have bounded retries and
pacing. A process kill can leave an audit row `running`; it is not a successful run.
The DB must be available to record a failure. No source revision archive is provided.

Exit codes: `0` successful operation; `1` configuration/uncaught transport/database
failure; `2` a recorded non-success ingestion outcome or critical market quality
failure. A market batch can fail one product and continue the others. Critical report
failures do not suppress useful exports. CLI JSON explains the failure or limitation.

## Managed network access

Allow `status.coinbase.com`, `api.coinbase.com` and `docs.cdp.coinbase.com` in the
environment's HTTP access policy. Keep TLS verification enabled. When a managed
proxy requires its trusted CA, the Dockerfile supports BuildKit secrets:

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t eri-phase2 .
```

At runtime mount the combined CA bundle read-only and set `SSL_CERT_FILE` to that
path. Preserve inherited proxy settings. Normal Docker Desktop builds need no custom
CA. A saved environment draft does not itself apply network changes to a running task.

## Remaining work

Engineering and deterministic validation are implemented. Live market acceptance,
actual historical product coverage and an empirical market–incident conclusion remain
blocked by network policy. Complete incident ascertainment remains analytically
unresolved even after market access is restored. Phase 3 recommendations are scheduled
collection, freshness monitoring, reviewed service/root-cause classifications and
business-impact validation; those features have not been implemented in Phase 2.
