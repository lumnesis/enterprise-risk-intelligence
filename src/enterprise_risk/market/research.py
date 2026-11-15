"""Coverage-gated exploratory analysis. Missing incident history is never a zero."""

import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from statistics import mean

import numpy as np
from scipy.stats import spearmanr
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from enterprise_risk.config import SOURCE as INCIDENT_SOURCE
from enterprise_risk.market.api import DAY, midnight, parse_candle
from enterprise_risk.models import IncidentCoverage, IngestionRun, MarketCandle, MarketRiskIndicator

METRICS = ("realized_volatility_30d", "absolute_return", "volume_zscore_30d")


def dates(start, end):
    return [start + i * DAY for i in range(max(0, (end - start).days))]


def register_coverage(engine, record):
    """Explicit attestation from an analyst with evidence of complete incident ascertainment."""
    if record.get("source") != INCIDENT_SOURCE or record.get("complete") is not True:
        raise ValueError("Coverage must explicitly attest complete coinbase_status observation")
    start, end = (date.fromisoformat(record[k]) for k in ("start_date", "end_date"))
    if start >= end or end > datetime.now(UTC).date():
        raise ValueError("Coverage must be a completed half-open daily interval")
    if any(
        not isinstance(record.get(k), str) or not record[k].strip()
        for k in ("evidence", "reviewed_by")
    ):
        raise ValueError("Coverage requires documentary evidence and reviewer")
    values = {
        "source": INCIDENT_SOURCE,
        "start_date": start,
        "end_date": end,
        "evidence": record["evidence"],
        "reviewed_by": record["reviewed_by"],
        "recorded_at": datetime.now(UTC),
    }
    with engine.begin() as conn:
        stmt = insert(IncidentCoverage).values(**values)
        conn.execute(
            stmt.on_conflict_do_update(
                constraint="uq_coverage_interval",
                set_={
                    k: v
                    for k, v in values.items()
                    if k in ("evidence", "reviewed_by", "recorded_at")
                },
            )
        )
    return {"registered": True, **values}


def load_data(engine, products, version, start, end):
    # One repeatable-read snapshot prevents mixed datasets during concurrent ingestion.
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn,
        conn.begin(),
    ):
        candles = [
            dict(r)
            for r in conn.execute(
                select(MarketCandle.__table__).where(
                    MarketCandle.product_id.in_(products),
                    MarketCandle.candle_start_utc >= midnight(start),
                    MarketCandle.candle_start_utc < midnight(end),
                )
            ).mappings()
        ]
        indicators = [
            dict(r)
            for r in conn.execute(
                select(MarketRiskIndicator.__table__).where(
                    MarketRiskIndicator.product_id.in_(products),
                    MarketRiskIndicator.calculation_version == version,
                    MarketRiskIndicator.observation_date >= start,
                    MarketRiskIndicator.observation_date < end,
                )
            ).mappings()
        ]
        # Load all facts for overlap assessments at the boundaries; select events later.
        incidents = [
            dict(r)
            for r in conn.execute(
                text(
                    "SELECT * FROM operational_incident_facts WHERE source=:source "
                    "ORDER BY created_at, source_id"
                ),
                {"source": INCIDENT_SOURCE},
            ).mappings()
        ]
        coverage = [
            dict(r)
            for r in conn.execute(
                select(IncidentCoverage.__table__).where(IncidentCoverage.source == INCIDENT_SOURCE)
            ).mappings()
        ]
        runs = [
            dict(r)
            for r in conn.execute(
                select(IngestionRun.__table__).order_by(IngestionRun.id)
            ).mappings()
        ]
    return {
        "candles": candles,
        "indicators": indicators,
        "incidents": incidents,
        "coverage": coverage,
        "ingestion_runs": runs,
    }


def assess_coverage(incidents, evidence, start, end):
    complete = {
        day
        for item in evidence
        for day in dates(max(start, item["start_date"]), min(end, item["end_date"]))
    }
    selected = [i for i in incidents if start <= i["occurrence_date"] < end]
    return {
        "source": INCIDENT_SOURCE,
        "status": "complete" if len(complete) == (end - start).days else "partial_observations",
        "requested_start": str(start),
        "requested_end_exclusive": str(end),
        "observed_incidents": len(selected),
        "operational_incidents": sum(not i["is_maintenance"] for i in selected),
        "maintenance_excluded": sum(i["is_maintenance"] for i in selected),
        "first_observed_incident": str(min((i["created_at"] for i in incidents), default="none")),
        "last_observed_incident": str(max((i["created_at"] for i in incidents), default="none")),
        "complete_days": [str(d) for d in sorted(complete)],
        "complete_day_count": len(complete),
        "unverified_day_count": (end - start).days - len(complete),
        "evidence": evidence,
        "limitation": (
            "Latest-50 snapshots and their overlap do not prove a complete "
            "archive. Unverified dates are not zero-incident dates. Coverage "
            "attestations require independent analyst review."
        ),
    }


def quality(data, products, start, end):
    issues, by_product = [], {}
    today = datetime.now(UTC).date()
    for product in products:
        candles = [c for c in data["candles"] if c["product_id"] == product]
        days = [c["candle_start_utc"].astimezone(UTC).date() for c in candles]
        missing = sorted(set(dates(start, end)) - set(days))
        duplicate = len(days) - len(set(days))
        invalid = 0
        for c in candles:
            try:
                parse_candle(
                    {
                        "start": str(int(c["candle_start_utc"].timestamp())),
                        **{k: c[k] for k in ("open", "high", "low", "close", "volume")},
                    }
                )
            except ValueError:
                invalid += 1
        partial = sum(d >= today for d in days)
        rows = [r for r in data["indicators"] if r["product_id"] == product]
        by_product[product] = {
            "observations": len(candles),
            "first": str(min(days)) if days else None,
            "last": str(max(days)) if days else None,
            "expected": (end - start).days,
            "missing_dates": [str(d) for d in missing],
            "duplicate_candles": duplicate,
            "invalid_candles": invalid,
            "incomplete_candles": partial,
            "indicator_rows": len(rows),
            "null_indicators": {
                key: sum(r[key] is None for r in rows) for key in (*METRICS, "market_stress_flag")
            },
        }
        if duplicate or invalid or partial or not candles:
            issues.append(
                {
                    "severity": "critical",
                    "product_id": product,
                    "kind": "invalid_or_absent_market_data",
                }
            )
        elif missing:
            issues.append(
                {
                    "severity": "warning",
                    "product_id": product,
                    "kind": "market_gaps",
                    "count": len(missing),
                }
            )
        if set(days) != {r["observation_date"] for r in rows}:
            issues.append(
                {
                    "severity": "critical",
                    "product_id": product,
                    "kind": "missing_or_stale_indicators",
                }
            )
    incidents = data["incidents"]
    negative = sum(
        i["resolved_at"] is not None and i["resolved_at"] < i["created_at"] for i in incidents
    )
    future = sum(
        i["created_at"] > datetime.now(UTC)
        or (i["resolved_at"] is not None and i["resolved_at"] > datetime.now(UTC))
        for i in incidents
    )
    unknown = sum(not i["impact"] or i["impact"] == "unknown" for i in incidents)
    unique = len({(i["source"], i["source_id"]) for i in incidents}) == len(incidents)
    if negative or future or not unique:
        issues.append({"severity": "critical", "kind": "invalid_incident_dates_or_cardinality"})
    if unknown:
        issues.append({"severity": "warning", "kind": "missing_severity", "count": unknown})
    return {
        "critical_failures": sum(i["severity"] == "critical" for i in issues),
        "issues": issues,
        "products": by_product,
        "incidents": {
            "negative_durations": negative,
            "invalid_future_dates": future,
            "missing_severity": unknown,
            "unique_source_records": unique,
        },
        "null_indicator_policy": (
            "Require consecutive calendar history; absent baselines, zero "
            "volume variance and unavailable candles remain null."
        ),
    }


def context_and_events(incidents, indicators, products, start, end, event_window=7):
    lookup = {(r["product_id"], r["observation_date"]): r for r in indicators}
    if len(lookup) != len(indicators):
        raise ValueError("Duplicate indicator grain (select one calculation version)")
    eligible = [
        i
        for i in incidents
        if not i["is_maintenance"]
        and start <= i["occurrence_date"] < end
        and (i["resolved_at"] is None or i["resolved_at"] >= i["created_at"])
    ]
    contexts, events = [], []
    for incident in eligible:
        day = incident["occurrence_date"]
        overlaps = sum(
            other["source_id"] != incident["source_id"]
            and not other["is_maintenance"]
            and other["created_at"] <= (incident["resolved_at"] or midnight(end))
            and (other["resolved_at"] or midnight(end)) >= incident["created_at"]
            for other in incidents
        )
        for product in products:
            row = {
                **incident,
                "product_id": product,
                "overlapping_incidents": overlaps,
                "network_keyword_hint": any(
                    word in (incident["name"] + " " + " ".join(incident["components"])).lower()
                    for word in ("network", "blockchain", "bitcoin", "ethereum", "solana")
                ),
            }
            for lag in (0, 1, 3):
                indicator = lookup.get((product, day - lag * DAY), {})
                row[f"market_present_lag{lag}"] = bool(indicator)
                for metric in (*METRICS, "market_stress_flag"):
                    row[f"{metric}_lag{lag}"] = indicator.get(metric)
            for metric in METRICS:
                prior = [
                    lookup.get((product, day - offset * DAY), {}).get(metric)
                    for offset in range(1, 8)
                ]
                row[f"{metric}_preceding7_n"] = sum(v is not None for v in prior)
                row[f"{metric}_preceding7_mean"] = (
                    mean(prior) if all(v is not None for v in prior) else None
                )
            contexts.append(row)
            for offset in range(-event_window, event_window + 1):
                observation = day + offset * DAY
                indicator = lookup.get((product, observation), {})
                events.append(
                    {
                        "source": incident["source"],
                        "incident_id": incident["source_id"],
                        "product_id": product,
                        "incident_date": day,
                        "observation_date": observation,
                        "event_day": offset,
                        "market_present": bool(indicator),
                        "impact": incident["impact"],
                        "overlapping_incidents": overlaps,
                        **{metric: indicator.get(metric) for metric in METRICS},
                    }
                )
    if len(contexts) != len(eligible) * len(products):
        raise ValueError("Incident context cardinality violation")
    return contexts, events


def spearman_block(pairs, *, min_n=30, seed=2026):
    """Effect + moving-block bootstrap interval; no invalid IID significance claim."""
    pairs = sorted(pairs)
    result = {
        "n": len(pairs),
        "period_start": str(pairs[0][0]) if pairs else None,
        "period_end": str(pairs[-1][0]) if pairs else None,
        "method": "Spearman rho; 7-day moving-block bootstrap, 500 draws, seed 2026",
        "status": "insufficient_sample",
    }
    if len(pairs) < min_n or sum(y for _, _, y in pairs) < 5:
        return result
    x, y = np.array([p[1] for p in pairs]), np.array([p[2] for p in pairs])
    if len(set(x)) < 2 or len(set(y)) < 2:
        return {**result, "status": "constant_input"}
    rho = float(spearmanr(x, y).statistic)
    result.update(status="descriptive_only", rho=rho)
    if (pairs[-1][0] - pairs[0][0]).days + 1 != len(pairs):
        return {
            **result,
            "limitation": "Calendar gaps prevent contiguous-block uncertainty estimation",
        }
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(500):
        idx = np.concatenate(
            [np.arange(i, i + 7) for i in rng.integers(0, len(x) - 6, size=math.ceil(len(x) / 7))]
        )[: len(x)]
        if len(set(x[idx])) > 1 and len(set(y[idx])) > 1:
            draws.append(float(spearmanr(x[idx], y[idx]).statistic))
    if len(draws) >= 400:
        result.update(
            status="exploratory",
            ci95=[float(v) for v in np.quantile(draws, [0.025, 0.975])],
            bootstrap_valid_draws=len(draws),
        )
    result["assumptions"] = (
        "Approximate weak stationarity; 7-day blocks may not capture "
        "longer dependence. Multiple exploratory comparisons; no causal "
        "interpretation or confirmatory p-values."
    )
    return result


def frequency_analysis(incidents, indicators, complete_days, products):
    complete = {date.fromisoformat(d) for d in complete_days}
    counts = Counter(i["occurrence_date"] for i in incidents if not i["is_maintenance"])
    lookup = {(r["product_id"], r["observation_date"]): r for r in indicators}
    output = {
        "status": "coverage_unverified" if not complete else "coverage_gated",
        "complete_days": len(complete),
        "products": {},
    }
    for product in products:
        groups, correlations = {}, []
        for metric, threshold in (
            ("realized_volatility_30d", "volatility_threshold"),
            ("volume_zscore_30d", "volume_zscore_threshold"),
            ("absolute_return", "return_threshold"),
        ):
            group = {"high": [], "normal": []}
            for day in sorted(complete):
                row = lookup.get((product, day), {})
                if row.get(metric) is not None and row.get(threshold) is not None:
                    group["high" if row[metric] > row[threshold] else "normal"].append(counts[day])
            summaries = {
                k: {
                    "observed_days": len(v),
                    "incidents": sum(v),
                    "incidents_per_day": mean(v) if v else None,
                    "days_with_incident": sum(n > 0 for n in v),
                }
                for k, v in group.items()
            }
            enough = all(len(v) >= 10 and sum(v) >= 5 for v in group.values())
            comparison = {
                "groups": summaries,
                "missing_indicator_days": len(complete) - sum(map(len, group.values())),
                "status": "descriptive" if enough else "insufficient_sample",
            }
            if enough:
                comparison["rate_ratio"] = mean(group["high"]) / mean(group["normal"])
                comparison["rate_difference"] = mean(group["high"]) - mean(group["normal"])
            comparison["limitation"] = (
                "Unequal day exposure accounted for. Rates are descriptive; "
                "incident clustering violates independent Poisson assumptions."
            )
            groups[metric] = comparison
            for lag in (0, 1, 3, 7):
                pairs = [
                    (day, lookup[(product, day - lag * DAY)][metric], counts[day])
                    for day in sorted(complete)
                    if lookup.get((product, day - lag * DAY), {}).get(metric) is not None
                ]
                result = spearman_block(pairs)
                result.update(
                    metric=metric,
                    lag_days=lag,
                    missing_indicator_days=len(complete) - len(pairs),
                    timing="retrospective_same_day" if lag == 0 else "preceding_completed_day",
                )
                correlations.append(result)
        output["products"][product] = {"groups": groups, "lagged_spearman": correlations}
    return output


def event_summary(events):
    groups = defaultdict(list)
    for event in events:
        groups[(event["product_id"], event["event_day"])].append(event)
    summaries = []
    for (product, offset), rows in sorted(groups.items()):
        for metric in METRICS:
            values = [r[metric] for r in rows if r[metric] is not None]
            summaries.append(
                {
                    "product_id": product,
                    "event_day": offset,
                    "metric": metric,
                    "n": len(values),
                    "eligible_incidents": len(rows),
                    "missing": len(rows) - len(values),
                    "mean": mean(values) if len(values) >= 3 else None,
                    "median": float(np.median(values)) if len(values) >= 3 else None,
                    "status": "descriptive_overlapping_events"
                    if len(values) >= 3
                    else "insufficient_sample",
                }
            )
    return summaries


def sensitivities(contexts, events):
    """Pre/post changes for 3/7 days, strata, overlaps, and outlier sensitivity."""
    pairs = defaultdict(list)
    for event in events:
        pairs[(event["product_id"], event["incident_id"])].append(event)
    contrasts = []
    for (product, incident_id), rows in sorted(pairs.items()):
        for window in (3, 7):
            for metric in METRICS:
                before = [
                    r[metric]
                    for r in rows
                    if -window <= r["event_day"] < 0 and r[metric] is not None
                ]
                after = [
                    r[metric]
                    for r in rows
                    if 0 < r["event_day"] <= window and r[metric] is not None
                ]
                contrasts.append(
                    {
                        "product_id": product,
                        "incident_id": incident_id,
                        "metric": metric,
                        "window_days": window,
                        "before_n": len(before),
                        "after_n": len(after),
                        "post_minus_pre": mean(after) - mean(before)
                        if len(before) == len(after) == window
                        else None,
                    }
                )
    strata = []
    for product in sorted({c["product_id"] for c in contexts}):
        rows = [c for c in contexts if c["product_id"] == product]
        selections = {
            "all": rows,
            "no_overlaps": [r for r in rows if not r["overlapping_incidents"]],
            "exclude_network_keyword_hint": [r for r in rows if not r["network_keyword_hint"]],
        }
        for impact in sorted({r["impact"] for r in rows}):
            selections[f"impact:{impact}"] = [r for r in rows if r["impact"] == impact]
        for service in sorted({s for r in rows for s in r["components"]}):
            selections[f"component:{service}"] = [r for r in rows if service in r["components"]]
        for label, selected in selections.items():
            ids = {r["source_id"] for r in selected}
            for window in (3, 7):
                for metric in METRICS:
                    values = [
                        c["post_minus_pre"]
                        for c in contrasts
                        if c["product_id"] == product
                        and c["incident_id"] in ids
                        and c["window_days"] == window
                        and c["metric"] == metric
                        and c["post_minus_pre"] is not None
                    ]
                    cutoff = float(np.quantile(np.abs(values), 0.99)) if values else None
                    trimmed = [v for v in values if abs(v) <= cutoff] if values else []
                    strata.append(
                        {
                            "product_id": product,
                            "stratum": label,
                            "window_days": window,
                            "metric": metric,
                            "n": len(values),
                            "mean_change": mean(values) if len(values) >= 3 else None,
                            "excluding_top1pct_abs_change_n": len(trimmed),
                            "excluding_top1pct_abs_change_mean": mean(trimmed)
                            if len(trimmed) >= 3
                            else None,
                            "status": "descriptive_only"
                            if len(values) >= 3
                            else "insufficient_sample",
                        }
                    )
    weeks = Counter(
        str(c["occurrence_date"] - timedelta(days=c["occurrence_date"].weekday()))
        for c in {(r["source"], r["source_id"]): r for r in contexts}.values()
    )
    return {
        "paired_changes": contrasts,
        "strata": strata,
        "incident_week_counts": dict(weeks),
        "limitations": [
            "Network keywords are unvalidated hints, not root-cause attribution.",
            "Service strata overlap; never add their incident counts.",
            (
                "Pre/post comparisons and outlier exclusion are descriptive; "
                "overlapping windows and secular trends remain confounders."
            ),
            "Incomplete archives cannot be repaired by sensitivity analysis.",
            "Severity and duration reflect provider reports, not business losses.",
        ],
    }


def duration_analysis(contexts):
    """Within the observed incident sample; unresolved incidents are not zero durations."""
    results = []
    for product in sorted({r["product_id"] for r in contexts}):
        rows = [r for r in contexts if r["product_id"] == product]
        for metric in METRICS:
            paired = [
                r
                for r in rows
                if r["duration_hours"] is not None and r[f"{metric}_lag1"] is not None
            ]
            result = {
                "product_id": product,
                "metric_lag1": metric,
                "n": len(paired),
                "observed_incidents": len(rows),
                "missing_or_unresolved": len(rows) - len(paired),
                "status": "insufficient_sample",
                "period_start": str(min((r["occurrence_date"] for r in paired), default="none")),
                "period_end": str(max((r["occurrence_date"] for r in paired), default="none")),
                "limitation": (
                    "Conditional on the observed, resolved incident sample; selection "
                    "and right-censoring may bias duration associations. No causal "
                    "inference."
                ),
            }
            if len(paired) >= 10:
                x = np.array([r[f"{metric}_lag1"] for r in paired])
                y = np.array([float(r["duration_hours"]) for r in paired])
                if len(set(x)) > 1 and len(set(y)) > 1:
                    result.update(status="descriptive_only", rho=float(spearmanr(x, y).statistic))
                    weeks = defaultdict(list)
                    for index, row in enumerate(paired):
                        day = row["occurrence_date"]
                        weeks[day - timedelta(days=day.weekday())].append(index)
                    result["calendar_week_clusters"] = len(weeks)
                    if len(weeks) >= 8:
                        clusters = list(weeks.values())
                        rng = np.random.default_rng(2026)
                        draws = []
                        for _ in range(500):
                            idx = np.concatenate(
                                [clusters[i] for i in rng.integers(0, len(clusters), len(clusters))]
                            )
                            if len(set(x[idx])) > 1 and len(set(y[idx])) > 1:
                                draws.append(float(spearmanr(x[idx], y[idx]).statistic))
                        if len(draws) >= 400:
                            result.update(
                                ci95=[float(v) for v in np.quantile(draws, [0.025, 0.975])],
                                status="exploratory",
                            )
                        result["uncertainty"] = (
                            "Calendar-week cluster bootstrap; 500 draws, seed 2026. Assumes "
                            "independent weeks; longer dependence remains unresolved."
                        )
                else:
                    result["status"] = "constant_input"
            results.append(result)
    return results
