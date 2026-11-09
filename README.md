# Enterprise Risk Intelligence

A portfolio project exploring enterprise risk analytics for cryptocurrency exchanges.
Phase 1 collects public operational incidents and supports descriptive SQL queries:
what incidents were observed, their reported impact/status, and when they were created.
It is independent and is not affiliated with Coinbase.

## Current scope and verification status

Implemented: HTTP ingestion, per-incident validation, PostgreSQL persistence, Alembic
migrations, idempotent upserts, execution audit records, a CLI, and SQL summaries.
There is no risk score, prediction, frontend, or internal GRC dataset.

**Live source verification remains pending.** This development environment's proxy
returned HTTP 403 for both the API and its documentation. The implementation has
been exercised against deterministic synthetic fixtures and real PostgreSQL, but
has not yet ingested an observed Coinbase response. Do not treat fixture results as
real operational evidence. See `tests/fixtures/README.md` for provenance.

## Data source and interpretation

- Incidents: <https://status.coinbase.com/api/v2/incidents.json>
- Documentation to consult: <https://status.coinbase.com/api>
- Coverage: the endpoint documents only the **50 most recent incidents**, not a
  complete historical archive. No historical backfill or pagination is assumed.
- These are provider-reported public incidents. Impact labels are the source's
  labels, not independently measured financial loss or enterprise risk ratings.
- Stored records accumulate across runs. Incidents falling out of the response
  are retained. Missing dates in the summary do not establish zero incidents.
- Any future simulated internal controls, losses, or GRC records must be explicitly
  labelled and kept separate from public observations. Phase 1 contains none.

## Architecture

```text
Coinbase Status HTTPS
    -> HTTPX boundary (ingestion/coinbase.py)
    -> Pydantic validation + UTC normalization (transformations.py)
    -> transactional PostgreSQL upserts (ingestion/service.py)
    -> SQL summaries (analytics/queries.py)
    -> JSON CLI output
```

```text
src/enterprise_risk/
  config.py, db.py       Environment settings and connection creation
  models.py             SQLAlchemy 2.x normalized schema
  transformations.py    Source validation and analytical row mapping
  ingestion/            Source-specific retrieval and ingestion orchestration
  analytics/            SQL counts, grouped summaries, quality reports
  cli.py                ingest / counts / summary / quality commands
migrations/             Versioned, explicit Alembic schema migration
tests/                  Unit and PostgreSQL integration tests; offline fixtures
.github/workflows/      Pull-request and push CI
```

The schema uses `(source, source_id)` primary keys for incidents, updates, and
components. Incident/component relationships use a join table with foreign keys.
Updates reference their parent incident. Date/time columns use PostgreSQL
`timestamptz`; validation requires timezone-aware input and normalizes to UTC.
Creation time, status/impact, and relationship indexes support the current queries.
JSONB retains source fields and unknown attributes, with timestamps normalized;
it is not a byte-for-byte archival copy of the HTTP response.

Each ingestion execution has a separately committed audit row. Valid records are
stored atomically; invalid incidents (including invalid children) are rejected with
field paths and error types. Outcomes are `success`, `partial`, `failed`, or
`running`. HTTP and database failures are recorded without sensitive SQL/response
contents when the database is reachable. A process kill can leave a `running` row;
this is not a successful execution.

PostgreSQL upserts prevent duplicate records. A transaction-level advisory lock
serializes this source's writes. Older incident snapshots cannot replace newer
ones. Updates omitted by a later response are retained. Explicit component lists
replace relationships; missing/null lists preserve existing relationships. Component
names are the latest encountered names, not historical name versions.

`processed` counts accepted, non-stale incident snapshots, **not newly inserted
rows**. Equal snapshots count as processed on repeat runs; only audit rows grow.
`received` is the response incident count, and `rejected` counts invalid records.
Unknown status/impact strings are retained; absent impact becomes `unknown`.
Malformed envelopes fail the run; an empty valid incidents array succeeds with zero.
HTTP failures/timeouts fail promptly without unbounded retries; rerun after fixing
connectivity or observing provider rate-limit guidance.

## Run from Windows PowerShell

Prerequisite: Docker Desktop with Linux containers and Docker Compose v2. Python
and PostgreSQL do not need to be installed on Windows. Run from the repository root.

```powershell
Copy-Item .env.example .env
# Edit .env and choose a unique local POSTGRES_PASSWORD before continuing.
notepad .env
docker compose build
docker compose up -d --wait db
docker compose run --rm app alembic upgrade head
docker compose run --rm app eri ingest
docker compose run --rm app eri counts
docker compose run --rm app eri summary
docker compose run --rm app eri quality
```

Do not overwrite an existing `.env`. Compose passes DB settings separately, so
passwords with URL-special characters do not need manual URL encoding. Follow
Compose `.env` quoting rules if your value contains `$` or `#`. `.env` is ignored
by Git and excluded from Docker build context; `.env.example` has placeholders only.
PostgreSQL binds only to host loopback and uses a persistent named volume. Changing
`.env` credentials after initial database creation does not rotate existing roles.

Ingestion requires outbound HTTPS access to `status.coinbase.com`; no API key is
required. CLI exit codes: `0` successful command, `1` configuration/transport/database
failure, `2` partial or fully rejected validation batch. `quality` displays the most
recent 20 runs. Summaries group creation dates in UTC and cover all stored incidents.

```powershell
# Stop services while preserving data:
docker compose down
# Only to deliberately erase local database volumes:
# docker compose --profile test down -v
```

## Tests and CI

```powershell
# Separate ephemeral PostgreSQL test service; no live API calls:
docker compose --profile test run --build --rm tests
# Optional checks using the same application image:
docker compose run --rm app ruff check .
docker compose run --rm app ruff format --check .
docker compose run --rm app alembic check
# Stop the isolated test database after testing:
docker compose --profile test stop test-db
```

The test database uses fixed, disposable credentials and has no host port.
Integration fixtures **truncate their target tables**: never point
`TEST_DATABASE_URL` at a development or production database containing useful data.
Without it, integration tests are reported as skipped, not passed. CI always supplies
it and runs PostgreSQL tests, migration/schema checks, upgrade/downgrade checks,
lint/format checks, and Compose configuration validation on push and pull request.

Tests cover UTC normalization, missing/invalid fields, malformed envelopes/JSON,
HTTP errors and timeouts, optional fields, unknown attributes, idempotency, changed
records, stale responses, foreign keys, rollback, execution auditing and SQL summaries.

For native Python 3.13 development (PowerShell):

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r requirements.lock
.\.venv\Scripts\python -m pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\pytest -q -m "not integration"
# For DB commands supply DATABASE_URL=postgresql+psycopg://... or DB_* variables.
# Use a dedicated disposable DB for TEST_DATABASE_URL when running integration tests.
```

Python is pinned to 3.13, with 3.13.5 used in Docker/CI. Runtime and test dependencies
are pinned transitively with hashes in `requirements.lock`; regenerate deliberately
with `uv pip compile pyproject.toml --extra dev --generate-hashes -o requirements.lock`.
No dependency resolution is required during a normal locked install.

### Managed cloud proxy trust

Ordinary Docker Desktop builds need no custom CA. In a managed environment with
TLS inspection, mount its combined CA bundle through BuildKit (never disable TLS):

```bash
docker build --secret id=proxy_ca,src=/etc/ssl/certs/ca-certificates.crt -t eri-phase1 .
```

For runtime HTTPX, mount the trusted combined bundle read-only and set
`SSL_CERT_FILE` to its container path. Preserve the platform's proxy configuration.
Allow `status.coinbase.com` in environment network settings. A saved draft does not
apply network access or publish an environment.

## Validation performed

On Python 3.13.5 and PostgreSQL 17, all **39 tests passed** inside Docker Compose,
including seven PostgreSQL integration tests, with no skips. Ruff lint/format,
Compose configuration, Alembic schema-drift checks, and migration downgrade/upgrade
passed. The application image built with hash verification and trusted proxy CA;
container CLI migration, counts, summary and quality commands ran successfully.
A blocked live ingestion exited with code 1 and persisted a failed audit record.
GitHub-hosted CI and a Windows host have not been exercised in this workspace.

## Remaining work and boundaries

Phase 1's live acceptance check is still required: enable source access, consult the
API documentation, inspect actual field types, then run migration, ingestion twice,
counts, summary and quality against real public data. Add a sanitized observed
fixture and adjust the parser if the verified contract differs. Until then the live
pipeline is provisional. GitHub Actions is configured but must run on GitHub after
these files are pushed. PowerShell commands use Docker-compatible syntax; they were
validated through Docker on Linux rather than a Windows host.

Other limits: no scheduler/backfill, no immutable version history, no schema-drift
alerting, and no production deployment/security hardening. The database must be
available to persist failure audits. Counts are descriptive and subject to source
coverage, reporting practices and missed collection windows.

Recommended Phase 2 scope (not implemented): schedule collection with freshness
monitoring, capture collection coverage, and add documented data-quality and source
contract checks before expanding to market indicators or explicitly simulated GRC
records. Scope any new source or analytical claims separately.
