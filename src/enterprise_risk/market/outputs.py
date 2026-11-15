"""Database-backed CSV/JSON, deterministic report text and Matplotlib figures."""

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from enterprise_risk.market.indicators import IndicatorConfig, calculate_indicators
from enterprise_risk.market.research import (
    METRICS,
    assess_coverage,
    context_and_events,
    dates,
    duration_analysis,
    event_summary,
    frequency_analysis,
    load_data,
    quality,
    sensitivities,
)


def write_json(path, value):
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path, rows, fields):
    fields = list(dict.fromkeys([*fields, *sorted({key for row in rows for key in row})]))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    k: json.dumps(v, sort_keys=True, default=str)
                    if isinstance(v, (dict, list))
                    else v
                    for k, v in row.items()
                }
            )


def plot_rows(indicators, product, start, end):
    lookup = {r["observation_date"]: r for r in indicators if r["product_id"] == product}
    if not lookup:
        return []
    return [
        lookup.get(
            day,
            {
                "observation_date": day,
                "realized_volatility_30d": None,
                "absolute_return": None,
                "return_threshold": None,
                "market_stress_flag": None,
            },
        )
        for day in dates(start, end)
    ]


def plots(output, data, products, summaries, start, end, *, fixture=False, annualize=True):
    volatility_unit = "% annualized" if annualize else "% daily"
    subtitle = f"{start} to {end} (end exclusive, UTC) | Partial incident sample"
    source = "Source: Coinbase Advanced Trade candles / Coinbase Status; stored PostgreSQL snapshot"
    if not data["candles"]:
        source = "Source: Coinbase Status (observed); market candles unavailable"
    if fixture:
        subtitle = "SYNTHETIC FIXTURE DEMONSTRATION | " + subtitle
        source = "Source: deterministic test fixtures; no empirical evidence"
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, ax = plt.subplots(figsize=(11, 5))
    present = False
    for product in products:
        rows = plot_rows(data["indicators"], product, start, end)
        if rows:
            present = True
            ax.plot(
                [r["observation_date"] for r in rows],
                [
                    100 * r["realized_volatility_30d"]
                    if r["realized_volatility_30d"] is not None
                    else float("nan")
                    for r in rows
                ],
                label=product,
            )
    if present:
        ax.legend()
    else:
        ax.set_yticks([])
        ax.text(
            0.5,
            0.5,
            "No finalized market observations available\nLive market collection is required",
            transform=ax.transAxes,
            ha="center",
        )
    ax.set(
        title=f"Cryptocurrency realized volatility\n{subtitle}",
        xlabel="Date (UTC)",
        xlim=(start, end),
        ylabel=f"30-day log-return standard deviation ({volatility_unit})",
    )
    fig.text(0.01, 0.01, source, fontsize=8)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(output / "market_volatility.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(len(products), 1, figsize=(11, 3 * len(products)), squeeze=False)
    incident_dates = sorted(
        {
            i["occurrence_date"]
            for i in data["incidents"]
            if not i["is_maintenance"] and start <= i["occurrence_date"] < end
        }
    )
    for product, ax in zip(products, axes[:, 0], strict=True):
        rows = plot_rows(data["indicators"], product, start, end)
        for key, label in (
            ("absolute_return", "Absolute return"),
            ("return_threshold", "Prior return threshold"),
        ):
            ax.plot(
                [r["observation_date"] for r in rows],
                [100 * r[key] if r[key] is not None else float("nan") for r in rows],
                label=label,
            )
        stress = [
            r for r in rows if r["market_stress_flag"] is True and r["absolute_return"] is not None
        ]
        ax.scatter(
            [r["observation_date"] for r in stress],
            [100 * r["absolute_return"] for r in stress],
            s=16,
            color="crimson",
            label="Stress flag (any rule)",
        )
        for n, day in enumerate(incident_dates):
            ax.axvline(
                day,
                color="slategray",
                alpha=0.3,
                linewidth=1,
                label="Observed incident date" if n == 0 else None,
            )
        if not rows:
            ax.set_yticks([])
            ax.text(
                0.5,
                0.65,
                "Market indicators unavailable; incident markers only",
                transform=ax.transAxes,
                ha="center",
            )
        ax.set(
            title=product,
            ylabel="Absolute daily return (%)",
            xlabel="Date (UTC)",
            xlim=(start, end),
        )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    if not data["indicators"]:
        handles = [
            h for h, label in zip(handles, labels, strict=True) if label == "Observed incident date"
        ]
        labels = [label for label in labels if label == "Observed incident date"]
    if handles:
        axes[0, 0].legend(handles, labels, fontsize=8, loc="upper left")
    fig.suptitle(
        f"Observed incident dates and market stress\n{subtitle}\n"
        "Same-day market values are retrospective"
    )
    fig.text(0.01, 0.01, source, fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.91))
    fig.savefig(output / "incident_market_stress.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    labels = (
        f"Volatility ({volatility_unit})",
        "Absolute return (%)",
        "Volume z-score (base units normalized)",
    )
    for metric, label, ax in zip(METRICS, labels, axes, strict=True):
        present = False
        for product in products:
            rows = [r for r in summaries if r["metric"] == metric and r["product_id"] == product]
            valid = [r for r in rows if r["mean"] is not None]
            if valid:
                present = True
                scale = 1 if metric == "volume_zscore_30d" else 100
                ax.plot(
                    [r["event_day"] for r in rows],
                    [scale * r["mean"] if r["mean"] is not None else float("nan") for r in rows],
                    marker="o",
                    label=(
                        f"{product} (n={min(r['n'] for r in valid)}–{max(r['n'] for r in valid)})"
                    ),
                )
        if present:
            ax.legend(fontsize=8)
        else:
            ax.text(
                0.5,
                0.5,
                "Insufficient observations\nMinimum 3 events per offset",
                transform=ax.transAxes,
                ha="center",
            )
        ax.axvline(0, linestyle="--", color="gray")
        if not present:
            ax.set_yticks([])
        ax.set(xlabel="Days relative to incident (UTC)", ylabel=label, xlim=(-7, 7))
    fig.suptitle(
        f"Market conditions around observed incidents\n{subtitle}\n"
        "Descriptive means; overlapping event windows are not independent"
    )
    fig.text(0.01, 0.01, source, fontsize=8)
    fig.tight_layout(rect=(0, 0.06, 1, 0.87))
    fig.savefig(output / "event_comparison.png", dpi=150)
    plt.close(fig)


def report_text(manifest, quality_report, coverage, stats, data, contexts):
    products = ", ".join(manifest["products"])
    market_n = len(data["candles"])
    eligible = len({(r["source"], r["source_id"]) for r in contexts})
    absent = sum(not r["market_present_lag0"] for r in contexts)
    failed = [
        r
        for r in data["ingestion_runs"]
        if r["source"] == "coinbase_advanced_trade" and r["outcome"] == "failed"
    ]
    blockers = (
        "\n".join(f"- Run {r['id']}: {r['error']}" for r in failed[-3:])
        or "- No recorded market transport failure. Consult quality_summary.json for gaps."
    )
    coverage_lines = "\n".join(
        f"| {p} | {q['observations']} | {q['first'] or 'none'} | "
        f"{q['last'] or 'none'} | {len(q['missing_dates'])} |"
        for p, q in quality_report["products"].items()
    )
    valid_stats = [
        r
        for p in stats["frequency"]["products"].values()
        for r in p["lagged_spearman"]
        if "rho" in r
    ]
    stats_lines = "\n".join(
        f"- {product}, {r['metric']}, lag {r['lag_days']}: n={r['n']}, "
        f"rho={r['rho']:.4f}, status={r['status']}, CI={r.get('ci95', 'unavailable')}."
        for product, p in stats["frequency"]["products"].items()
        for r in p["lagged_spearman"]
        if "rho" in r
    )
    title = "# Phase 2 — Market data and operational incident analysis"
    if manifest["fixture_demonstration"]:
        title += "\n\n**SYNTHETIC FIXTURE DEMONSTRATION — not empirical findings.**"
    no_estimate = (
        "No eligible correlation estimate can be reported. The evidence does not support "
        "a market–incident relationship conclusion."
    )
    evidence_strength = (
        "No market observations were collected, so no empirical market–incident association "
        "or event-study pattern can be assessed."
        if not market_n
        else "This is observational, exploratory evidence subject to missingness, "
        "overlapping event windows and multiple comparisons."
    )
    volatility_convention = (
        "annualized by sqrt(365)"
        if manifest["indicator_parameters"]["annualize"]
        else "not annualized (daily scale)"
    )
    return f"""{title}

Generated from one PostgreSQL snapshot at {manifest["generated_at"]}. Analysis window:
{manifest["start"]} through {manifest["end_exclusive"]} (exclusive), UTC.
Calculation version: `{manifest["calculation_version"]}`.
Input SHA-256: `{manifest["input_sha256"]}`.

## 1. Observed facts: data collected and coverage

Products requested: {products}. Stored finalized market candles in this window: **{market_n}**.
Observed incident records in the window: **{coverage["observed_incidents"]}**;
Maintenance excluded: **{coverage["maintenance_excluded"]}**;
eligible operational events: **{eligible}**.
Observed incident timestamps in the stored archive range from {coverage["first_observed_incident"]}
to {coverage["last_observed_incident"]}. These endpoints do not establish a complete history.

| Product | Candles | First day | Last day | Missing requested days |
| --- | ---: | --- | --- | ---: |
{coverage_lines}

Incident coverage: **{coverage["status"]}**.
Reviewed complete days: **{coverage["complete_day_count"]}**;
unverified days: **{coverage["unverified_day_count"]}**. The Status API returns only the latest 50
incidents. Neither missing records nor overlapping snapshots establish zero-incident dates.
Market volume is in traded base-asset units, not USD turnover.

## 2. Indicators and rationale

Daily return is close / previous calendar-day close − 1 (fraction); log return is its log ratio.
Absolute return describes movement regardless of direction. Realized volatility is sample standard
deviation (ddof=1) of 7 or 30 consecutive daily log returns, {volatility_convention}.
A missing day breaks the rolling window; no interpolation is performed. Volume change is a fraction;
the 30-day volume mean and z-score use the **preceding** 30 daily candles. A zero prior standard
deviation or previous zero volume yields null, not infinity. Z-scores normalize within each product.

Stress is true if any available absolute-return, volatility, or volume criterion exceeds its stored
threshold. False requires all three evaluable criteria to be false; otherwise stress is unknown.
Return and volatility thresholds are prior-only empirical quantiles; parameters are
`{json.dumps(manifest["indicator_parameters"], sort_keys=True)}`.
The rule is transparent, exploratory,
and versioned; it is not a calibrated business risk score.

## 3. Analytical questions and observed results

We aligned indicators at day 0, −1, −3 and the mean of days −7 through −1, then exported event
windows −7 through +7. Day 0 uses the completed daily candle only for retrospective description.
Preceding windows use only information available before incident creation. Missing same-day
market rows: **{absent} / {len(contexts)}** incident–product contexts.

Frequency comparisons use only reviewed complete days, with incidents per observed day as the
exposure-adjusted measure. Lagged Spearman analysis requires at least 30 paired days, five
incidents,
and nonconstant inputs. Seven-day moving-block bootstrap intervals require contiguous paired days.
There are **{len(valid_stats)}** eligible correlation estimates in this run.
{stats_lines or no_estimate}

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

{evidence_strength}
Critical quality failures: **{quality_report["critical_failures"]}**. See `quality_summary.json`.
We cannot infer causation, advance-warning capability, financial loss, or a representative long-run
incident frequency. Provider severity is not an independently measured ERM impact rating.

Recorded market collection blockers:
{blockers}

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
"""


def render(
    data,
    products,
    start,
    end,
    output,
    config=IndicatorConfig(),
    *,
    fixture=False,
    generated_at=None,
):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    coverage = assess_coverage(data["incidents"], data["coverage"], start, end)
    check = quality(data, products, start, end)
    contexts, events = context_and_events(
        data["incidents"], data["indicators"], products, start, end
    )
    check["incident_market_context"] = {
        "rows": len(contexts),
        "missing_same_day_market": sum(not c["market_present_lag0"] for c in contexts),
        "duplicate_incident_product_rows": len(contexts)
        - len({(c["source"], c["source_id"], c["product_id"]) for c in contexts}),
    }
    check["incident_coverage_status"] = coverage["status"]
    if coverage["status"] != "complete":
        check["issues"].append({"severity": "warning", "kind": "incomplete_incident_coverage"})
    summary = event_summary(events)
    # Invalid source records block statistical inference even if a coverage attestation exists.
    frequency = frequency_analysis(
        data["incidents"],
        data["indicators"],
        coverage["complete_days"] if not check["critical_failures"] else [],
        products,
    )
    stats = {
        "frequency": frequency,
        "sensitivity": sensitivities(contexts, events),
        "incident_duration": duration_analysis(contexts) if not check["critical_failures"] else [],
        "event_summary": summary,
        "critical_quality_gate": bool(check["critical_failures"]),
        "interpretation": (
            "Exploratory, noncausal; no IID p-values or claims from incomplete coverage."
        ),
    }
    manifest = {
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "products": products,
        "start": str(start),
        "end_exclusive": str(end),
        "calculation_version": config.version,
        "indicator_parameters": config.__dict__,
        "fixture_demonstration": fixture,
    }
    snapshot = {"manifest": manifest, "data": data}
    write_json(output / "source_snapshot.json", snapshot)
    manifest["input_sha256"] = hashlib.sha256(
        (output / "source_snapshot.json").read_bytes()
    ).hexdigest()
    write_csv(
        output / "daily_market_indicators.csv",
        sorted(data["indicators"], key=lambda r: (r["product_id"], r["observation_date"])),
        [
            "product_id",
            "observation_date",
            "calculation_version",
            "daily_return",
            "log_return",
            "absolute_return",
            "realized_volatility_7d",
            "realized_volatility_30d",
            "volume_change",
            "volume_mean_30d",
            "volume_zscore_30d",
            "market_stress_flag",
            "return_threshold",
            "volatility_threshold",
            "volume_zscore_threshold",
        ],
    )
    write_csv(
        output / "incident_market_context.csv",
        contexts,
        [
            "source",
            "source_id",
            "product_id",
            "created_at",
            "resolved_at",
            "duration_hours",
            "impact",
            "status",
            "components",
            "occurrence_date",
        ],
    )
    write_csv(
        output / "event_records.csv",
        events,
        ["source", "incident_id", "product_id", "event_day", "observation_date", *METRICS],
    )
    write_csv(
        output / "event_summary.csv",
        summary,
        ["product_id", "event_day", "metric", "n", "mean", "median", "status"],
    )
    write_json(output / "quality_summary.json", check)
    write_json(output / "incident_coverage.json", coverage)
    write_json(output / "statistical_results.json", stats)
    plots(output, data, products, summary, start, end, fixture=fixture, annualize=config.annualize)
    (output / "phase2_market_incident_analysis.md").write_text(
        report_text(manifest, check, coverage, stats, data, contexts), encoding="utf-8"
    )
    names = [
        "source_snapshot.json",
        "daily_market_indicators.csv",
        "incident_market_context.csv",
        "event_records.csv",
        "event_summary.csv",
        "quality_summary.json",
        "incident_coverage.json",
        "statistical_results.json",
        "market_volatility.png",
        "incident_market_stress.png",
        "event_comparison.png",
        "phase2_market_incident_analysis.md",
    ]
    manifest["artifacts_sha256"] = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in names
    }
    write_json(output / "manifest.json", manifest)
    return {
        "output": str(output.resolve()),
        "critical_failures": check["critical_failures"],
        "market_observations": len(data["candles"]),
        "incident_context_rows": len(contexts),
        "coverage_status": coverage["status"],
        "calculation_version": config.version,
    }


def run_report(engine, products, start, end, output, config=IndicatorConfig()):
    calculate_indicators(engine, products, config)
    data = load_data(engine, products, config.version, start, end)
    return render(data, products, start, end, output, config)


def render_snapshot(path, output):
    from datetime import date
    from decimal import Decimal

    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    meta, data = snapshot["manifest"], snapshot["data"]
    for category, rows in data.items():
        for row in rows:
            for key, value in list(row.items()):
                if value is None:
                    continue
                if key in {
                    "candle_start_utc",
                    "ingested_at",
                    "calculated_at",
                    "created_at",
                    "resolved_at",
                    "recorded_at",
                    "started_at",
                    "finished_at",
                }:
                    row[key] = datetime.fromisoformat(value)
                elif key in {"observation_date", "occurrence_date", "start_date", "end_date"}:
                    row[key] = date.fromisoformat(value)
                elif category == "candles" and key in {"open", "high", "low", "close", "volume"}:
                    row[key] = Decimal(value)
    return render(
        data,
        meta["products"],
        date.fromisoformat(meta["start"]),
        date.fromisoformat(meta["end_exclusive"]),
        output,
        IndicatorConfig(**meta["indicator_parameters"]),
        fixture=meta["fixture_demonstration"],
        generated_at=meta["generated_at"],
    )
