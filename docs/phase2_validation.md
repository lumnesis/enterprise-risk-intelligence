# Phase 2 validation results

Executed on 2026-11-15 in the managed Linux workspace, Python 3.13.5 and PostgreSQL 17.

| Check | Actual result |
| --- | --- |
| Full native test suite | **85 passed**, 0 failed, 0 skipped (6.11 seconds) |
| Full suite in built Docker image with current workspace mounted | **85 passed**, 0 failed, 0 skipped (4.77 seconds) |
| PostgreSQL integration tests | **16**, included in both full suite runs |
| Remaining deterministic unit tests | **69** |
| Original Phase 1 tests | All **39** preserved and passing |
| Ruff lint | Passed |
| Ruff format check | Passed |
| Alembic schema drift (`alembic check`) | No new upgrade operations detected |
| Alembic downgrade to base / upgrade to head | Passed on disposable test DB |
| Docker image build with hash-locked dependencies | Passed |
| Docker Compose configuration | Passed |
| Live Phase 1 ingestion | 50 accepted, 0 rejected |
| Live market ingestion | Blocked by enforced proxy policy; 3 failed product audit records |
| Actual database report | Generated all 13 report/data/plot/manifest artifacts; exit 2 for missing market data |
| Saved snapshot replay | Generated without live APIs or DB; values/report text reproducibility tested |

Market tests cover exact decimal parsing and precision limits, malformed/negative
OHLCV, UTC alignment, request parameter formatting, time-window splitting, bounded
HTTP retries, timeout recovery, Retry-After, terminal failures, and malformed JSON.

Transformation tests check return and sample-volatility mathematics, preceding volume
baselines, normalization invariance, stress quantiles, versioning, missing history,
zero variance and no look-ahead. Research tests check temporal joins, scheduled
maintenance exclusion, coverage unions, insufficient sample handling, known Spearman
results and clustered duration uncertainty.

PostgreSQL tests additionally verify backfill/idempotency, incremental updates and
gap repair, partial-candle exclusion, failure auditing, SQL/Python return parity,
indicator invalidation/rebuild, numeric constraints and exact persistence,
multicomponent cardinality, full product rollback after window/duplicate conflicts,
coverage evidence registration, and report/replay generation. Phase 1 transaction,
stale-snapshot and source-record tests remain unchanged.

## Live data outcome

The actual database has 50 public incident records created between
2026-09-19 08:49:33.799 UTC and 2026-11-13 00:20:22.287 UTC. Default requested market
window: 2026-06-21 to 2026-11-15 exclusive, 147 days per product. There are no market
candles and no reviewed complete incident days. The report therefore contains 150
incident/product contexts with missing market observations and 2,250 missing event
positions (50 incidents × 3 products × 15 offsets). Quality correctly flags three
critical absent-market-data conditions. No empirical correlations are reported.

The three charts are generated and visually checked. They label absent market data
explicitly; the timeline shows real incident dates. They are not evidence of market
patterns. Unit-test fixtures were not inserted into the observation database.

## Acceptance status and remaining checks

Implementation, automated tests, migrations, reproducible outputs and documentation
are delivered. Full live-data acceptance is **not yet met**: this environment must
allow `api.coinbase.com` and `docs.cdp.coinbase.com`, then the product/date coverage
and candle contract must be checked against successful origin responses. The official
SDK verified request and response shapes, but cannot establish actual availability.
The latest-50 incident limit separately prevents assumptions of complete history.

GitHub-hosted Actions and an actual Windows PowerShell host were not executed here.
Docker and native Linux commands were exercised. No Phase 3 functionality was added.
