# Phase 2 — Market data and operational incident analysis

Generated from one PostgreSQL snapshot at 2026-11-15T16:30:54.697937+00:00. Analysis window:
2026-06-21 through 2026-11-15 (exclusive), UTC.
Calculation version: `daily-v1-525074594d78fe52`.
Input SHA-256: `ad9f652d7425d5c1f782fdbabb6d9f0dca22b8e94b95c517a4049e6eca236c80`.

## 1. Observed facts: data collected and coverage

Products requested: BTC-USD, ETH-USD, SOL-USD. Stored finalized market candles in this window: **0**.
Observed incident records in the window: **50**;
Maintenance excluded: **0**;
eligible operational events: **50**.
Observed incident timestamps in the stored archive range from 2026-09-19 08:49:33.799000+00:00
to 2026-11-13 00:20:22.287000+00:00. These endpoints do not establish a complete history.

| Product | Candles | First day | Last day | Missing requested days |
| --- | ---: | --- | --- | ---: |
| BTC-USD | 0 | none | none | 147 |
| ETH-USD | 0 | none | none | 147 |
| SOL-USD | 0 | none | none | 147 |

Incident coverage: **partial_observations**.
Reviewed complete days: **0**;
unverified days: **147**. The Status API returns only the latest 50
incidents. Neither missing records nor overlapping snapshots establish zero-incident dates.
Market volume is in traded base-asset units, not USD turnover.

## 2. Indicators and rationale

Daily return is close / previous calendar-day close − 1 (fraction); log return is its log ratio.
Absolute return describes movement regardless of direction. Realized volatility is sample standard
deviation (ddof=1) of 7 or 30 consecutive daily log returns, annualized by sqrt(365).
A missing day breaks the rolling window; no interpolation is performed. Volume change is a fraction;
the 30-day volume mean and z-score use the **preceding** 30 daily candles. A zero prior standard
deviation or previous zero volume yields null, not infinity. Z-scores normalize within each product.

Stress is true if any available absolute-return, volatility, or volume criterion exceeds its stored
threshold. False requires all three evaluable criteria to be false; otherwise stress is unknown.
Return and volatility thresholds are prior-only empirical quantiles; parameters are
`{"annualize": true, "baseline_days": 30, "quantile": 0.95, "volume_z_threshold": 2.0}`.
The rule is transparent, exploratory,
and versioned; it is not a calibrated business risk score.

## 3. Analytical questions and observed results

We aligned indicators at day 0, −1, −3 and the mean of days −7 through −1, then exported event
windows −7 through +7. Day 0 uses the completed daily candle only for retrospective description.
Preceding windows use only information available before incident creation. Missing same-day
market rows: **150 / 150** incident–product contexts.

Frequency comparisons use only reviewed complete days, with incidents per observed day as the
exposure-adjusted measure. Lagged Spearman analysis requires at least 30 paired days, five
incidents,
and nonconstant inputs. Seven-day moving-block bootstrap intervals require contiguous paired days.
There are **0** eligible correlation estimates in this run.
No eligible correlation estimate can be reported. The evidence does not support a market–incident relationship conclusion.

Incident-level duration associations use prior-day indicators and only observed resolved incidents;
they do not require a complete daily incident archive. At least ten pairs are required; optional
Week-cluster bootstrap intervals require eight weeks.
Right-censoring and selection remain limitations.
Full sample sizes, missingness, group exposures, effect sizes and available uncertainty are in
`statistical_results.json`; event-level observations and offset summaries are in the CSV exports.
No statistical significance claim is made. Descriptive patterns require independent replication.

## 4. Alternative explanations evaluated

The reproducible sensitivity outputs compare 3- and 7-day pre/post windows, reported impact strata,
affected components, exclusion of overlapping incidents, and removal of the top 1% absolute paired
changes. Weekly incident counts disclose time clustering. Keyword-based network hints provide an
explicitly unvalidated exclusion sensitivity. Small groups (fewer than three paired events) have
no aggregate effect reported. If market observations are absent these checks are implemented but
**not empirically evaluable**. Network root causes, reporting changes, longer time dependence,
service-specific exposure and missing historical incidents remain unresolved.

## 5. Evidence strength and limits

No market observations were collected, so no empirical market–incident association or event-study pattern can be assessed.
Critical quality failures: **3**. See `quality_summary.json`.
We cannot infer causation, advance-warning capability, financial loss, or a representative long-run
incident frequency. Provider severity is not an independently measured ERM impact rating.

Recorded market collection blockers:
- Run 2: Market request failed after bounded retries
- Run 3: Market request failed after bounded retries
- Run 4: Market request failed after bounded retries

The market API and documentation require environment access to `api.coinbase.com` and
`docs.cdp.coinbase.com`. Product availability and historical coverage must be verified through a
successful live run. Fixture-based tests demonstrate software behavior only and are separate from
this database snapshot. No market values or statistical results were fabricated to fill gaps.

## 6. Hypothetical recommendations for the ERM analyst

1. Collect finalized market observations, inspect product availability and gaps, and rerun
quality checks.
2. Establish independently reviewed incident-observation coverage before interpreting daily rates.
3. Review service and network classifications, severity, durations and overlapping incidents
manually.
4. Predefine a longer validation period, operationally meaningful effect sizes, and sensitivity
rules.
5. In a separately scoped Phase 3, consider scheduled collection, freshness alerts and analyst
review
   of operational impact. No Phase 3 features are implemented here.

## 7. Reproducibility and artifacts

Run `eri market-report` with the product/date/configuration options in `manifest.json`, after
`eri market-indicators`. The report command recalculates indicators from stored candles, then reads
one repeatable snapshot. `source_snapshot.json` preserves that exact analytical input; hashes of
all outputs are in `manifest.json`. Collection timestamps may change on a fresh run, but analytical
values are deterministic for fixed source records and parameters. Use `eri market-render --snapshot`
to recreate outputs from a saved snapshot without network access.

- `daily_market_indicators.csv`: one product/date/version row.
- `incident_market_context.csv`: one incident/product row; component lists remain aggregated.
- `event_records.csv`, `event_summary.csv`: event offsets and descriptive summaries.
- `incident_coverage.json`, `quality_summary.json`, `statistical_results.json`: machine-readable
evidence.
- `market_volatility.png`, `incident_market_stress.png`, `event_comparison.png`: charts with units,
  dates, source attribution and explicit missing-sample labels.
