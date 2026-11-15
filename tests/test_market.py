"""Synthetic, deterministic contract and mathematics tests; no live API calls."""

import json
import math
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import stdev

import httpx
import numpy as np
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_risk.ingestion.coinbase import SourceError
from enterprise_risk.ingestion.service import ingest
from enterprise_risk.market.api import (
    DAY,
    MarketAPI,
    decimal_value,
    midnight,
    parse_candle,
    windows,
)
from enterprise_risk.market.indicators import IndicatorConfig, calculate, calculate_indicators
from enterprise_risk.market.ingestion import default_start, ingest_market
from enterprise_risk.market.outputs import render_snapshot, run_report
from enterprise_risk.market.research import (
    assess_coverage,
    context_and_events,
    duration_analysis,
    frequency_analysis,
    register_coverage,
    spearman_block,
)
from enterprise_risk.models import IngestionRun, MarketCandle, MarketRiskIndicator

START = date(2025, 10, 1)


def raw_candle(day, index=0):
    close = 100 + index * 0.1 + 4 * math.sin(index)
    return {
        "start": str(int(midnight(day).timestamp())),
        "open": f"{close:.8f}",
        "high": f"{close + 1:.8f}",
        "low": f"{close - 1:.8f}",
        "close": f"{close:.8f}",
        "volume": str(100 + index % 17 * 10),
    }


def candle_rows(n=100, start=START):
    return [
        {"product_id": "BTC-USD", **parse_candle(raw_candle(start + i * DAY, i))} for i in range(n)
    ]


def market_api(*, missing=(), invalid=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.append(request)
        if request.url.path.endswith("/candles"):
            first = datetime.fromtimestamp(int(request.url.params["start"]), UTC).date()
            last = datetime.fromtimestamp(int(request.url.params["end"]), UTC).date()
            raw = [
                raw_candle(first + i * DAY, (first + i * DAY - START).days)
                for i in range((last - first).days + 1)
                if first + i * DAY not in missing
            ]
            if invalid:
                raw.append(invalid)
            return httpx.Response(200, json={"candles": list(reversed(raw))})
        product = request.url.path.split("/")[-1]
        return httpx.Response(
            200,
            json={
                "product_id": product,
                "base_currency_id": product.split("-")[0],
                "quote_currency_id": "USD",
            },
        )

    return MarketAPI(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)


def fact(day, identifier="a", components=None, maintenance=False):
    return {
        "source": "coinbase_status",
        "source_id": identifier,
        "name": "Test incident",
        "status": "resolved",
        "impact": "major",
        "created_at": midnight(day) + timedelta(hours=1),
        "resolved_at": midnight(day) + timedelta(hours=2),
        "occurrence_date": day,
        "duration_hours": 1,
        "components": components or [],
        "is_maintenance": maintenance,
    }


def test_contract_exact_precision():
    raw = raw_candle(START)
    raw["volume"] = "12345678901234567890.123456789012345678"
    row = parse_candle(raw)
    assert row["volume"] == Decimal(raw["volume"])
    assert row["candle_start_utc"] == midnight(START)


@pytest.mark.parametrize(
    "field,value",
    [
        ("close", "0"),
        ("open", "-1"),
        ("high", "1"),
        ("low", "999"),
        ("volume", "-1"),
        ("open", "NaN"),
        ("close", "Infinity"),
        ("start", "123"),
        ("start", True),
        ("close", 1.1),
        ("close", "1e21"),
        ("volume", "0.0000000000000000001"),
    ],
)
def test_bad_candles(field, value):
    raw = raw_candle(START)
    raw[field] = value
    with pytest.raises(ValueError):
        parse_candle(raw)


def test_decimal_scale_never_silently_rounded():
    with pytest.raises(ValueError):
        decimal_value("12345678901234567890.1234567890123456789")


def test_windows_cover_every_day_once():
    end = START + 701 * DAY
    chunks = list(windows(START, end))
    assert chunks == [
        (START, START + 300 * DAY),
        (START + 300 * DAY, START + 600 * DAY),
        (START + 600 * DAY, end),
    ]
    with pytest.raises(ValueError):
        list(windows(end, START))


def test_api_parameters_and_response():
    seen = []
    api = market_api(seen=seen)
    assert api.product("ETH-USD")["base_currency"] == "ETH"
    assert len(api.candles("ETH-USD", START, START + 2 * DAY)) == 2
    assert dict(seen[-1].url.params) == {
        "start": str(int(midnight(START).timestamp())),
        "end": str(int(midnight(START + 2 * DAY).timestamp()) - 1),
        "granularity": "ONE_DAY",
        "limit": "350",
    }


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retry_limit(status):
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Retry-After": "3"})

    api = MarketAPI(httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    with pytest.raises(SourceError):
        api.product("BTC-USD")
    assert len(calls) == 4
    assert sleeps == [0.2, 3, 0.2, 3, 0.2, 4, 0.2]


def test_timeout_retry_then_success():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ReadTimeout("timeout", request=request)
        return httpx.Response(200, json={"candles": []})

    api = MarketAPI(httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    assert api.candles("BTC-USD", START, START + DAY) == []
    assert len(calls) == 3


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.Response(404),
        httpx.Response(200, text="bad json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"candles": {}}),
        httpx.Response(429, headers={"Retry-After": "1000"}),
    ],
)
def test_malformed_and_terminal_failures(response):
    api = MarketAPI(
        httpx.Client(transport=httpx.MockTransport(lambda _: response)), sleep=lambda _: None
    )
    with pytest.raises(SourceError):
        api.candles("BTC-USD", START, START + DAY)


def test_returns_volatility_and_prior_volume_baseline():
    candles = candle_rows()
    rows = calculate(candles)
    logs = [math.log(float(candles[i]["close"] / candles[i - 1]["close"])) for i in range(1, 31)]
    assert rows[0]["daily_return"] is None
    assert rows[1]["daily_return"] == pytest.approx(
        float(candles[1]["close"] / candles[0]["close"] - 1)
    )
    assert rows[6]["realized_volatility_7d"] is None
    assert rows[7]["realized_volatility_7d"] == pytest.approx(stdev(logs[:7]) * math.sqrt(365))
    assert rows[29]["realized_volatility_30d"] is None
    assert rows[30]["realized_volatility_30d"] == pytest.approx(stdev(logs) * math.sqrt(365))
    volumes = [float(c["volume"]) for c in candles[:30]]
    assert rows[30]["volume_mean_30d"] == pytest.approx(np.mean(volumes))
    assert rows[30]["volume_zscore_30d"] == pytest.approx(
        (float(candles[30]["volume"]) - np.mean(volumes)) / stdev(volumes)
    )
    assert rows[59]["volatility_threshold"] is None
    assert rows[60]["volatility_threshold"] == pytest.approx(
        np.quantile([r["realized_volatility_30d"] for r in rows[30:60]], 0.95)
    )
    assert rows[31]["return_threshold"] == pytest.approx(
        np.quantile([r["absolute_return"] for r in rows[1:31]], 0.95)
    )
    assert (
        calculate(candles, IndicatorConfig(quantile=0.90))[60]["calculation_version"]
        != rows[60]["calculation_version"]
    )


def test_no_lookahead_and_normalization():
    candles = candle_rows()
    rows = calculate(candles)
    altered = [dict(c) for c in candles]
    altered[65]["close"] *= 10
    altered[65]["volume"] *= 100
    changed = calculate(altered)
    for r, c in zip(rows[:65], changed[:65], strict=True):
        for metric in (
            "daily_return",
            "volume_zscore_30d",
            "return_threshold",
            "volatility_threshold",
        ):
            assert r[metric] == c[metric]
    assert changed[65]["return_threshold"] == rows[65]["return_threshold"]
    assert changed[65]["market_stress_flag"] is True
    scaled = calculate([{**c, "volume": c["volume"] * Decimal(1000)} for c in candles])
    assert scaled[65]["volume_zscore_30d"] == pytest.approx(rows[65]["volume_zscore_30d"])


def test_gaps_and_zero_variance_are_unknown():
    candles = candle_rows()
    rows = {r["observation_date"]: r for r in calculate(candles[:50] + candles[51:])}
    assert rows[START + 51 * DAY]["daily_return"] is None
    assert rows[START + 57 * DAY]["realized_volatility_7d"] is None
    assert rows[START + 58 * DAY]["realized_volatility_7d"] is not None
    zero = calculate([{**c, "volume": Decimal(0)} for c in candles])
    assert zero[90]["volume_zscore_30d"] is None
    assert zero[90]["volume_change"] is None
    assert zero[0]["market_stress_flag"] is None


def test_temporal_alignment_cardinality_maintenance():
    rows = calculate(candle_rows())
    day = START + 70 * DAY
    contexts, events = context_and_events(
        [fact(day, components=["a", "b"]), fact(day, "maintenance", maintenance=True)],
        rows,
        ["BTC-USD"],
        START,
        START + 100 * DAY,
    )
    assert len(contexts) == 1
    assert len(events) == 15
    assert contexts[0]["absolute_return_lag1"] == rows[69]["absolute_return"]
    assert contexts[0]["absolute_return_lag3"] == rows[67]["absolute_return"]
    assert contexts[0]["absolute_return_preceding7_mean"] == pytest.approx(
        np.mean([r["absolute_return"] for r in rows[63:70]])
    )
    with pytest.raises(ValueError, match="Duplicate"):
        context_and_events([], rows + rows, ["BTC-USD"], START, START + 100 * DAY)


def test_partial_incident_history_never_becomes_zero():
    incidents = [fact(START)]
    coverage = assess_coverage(incidents, [], START, START + 100 * DAY)
    assert coverage["status"] == "partial_observations"
    assert coverage["complete_day_count"] == 0
    result = frequency_analysis(incidents, calculate(candle_rows()), [], ["BTC-USD"])
    assert result["status"] == "coverage_unverified"
    assert all(r["n"] == 0 for r in result["products"]["BTC-USD"]["lagged_spearman"])


def test_coverage_union_does_not_double_count():
    evidence = [
        {"start_date": START, "end_date": START + 10 * DAY},
        {"start_date": START + 5 * DAY, "end_date": START + 15 * DAY},
    ]
    assert assess_coverage([], evidence, START, START + 15 * DAY)["complete_day_count"] == 15


def test_spearman_known_effect_and_insufficient_samples():
    pairs = [(START + i * DAY, i, i) for i in range(40)]
    result = spearman_block(pairs)
    assert result["rho"] == pytest.approx(1)
    assert result["ci95"] == pytest.approx([1, 1])
    assert spearman_block(pairs[:10])["status"] == "insufficient_sample"
    assert spearman_block([(d, 1, y) for d, _, y in pairs])["status"] == "constant_input"
    assert "ci95" not in spearman_block(pairs[:20] + pairs[21:])


@pytest.mark.integration
def test_backfill_idempotency_incremental_and_gap_repair(engine):
    end = START + 305 * DAY
    seen = []
    api = market_api(missing={START + 10 * DAY}, seen=seen)
    assert ingest_market(engine, api, ["BTC-USD"], START, end)["outcome"] == "partial"
    assert len([r for r in seen if r.url.path.endswith("/candles")]) == 2
    for _ in range(2):
        assert (
            ingest_market(engine, market_api(), ["BTC-USD"], START, end, incremental=True)[
                "outcome"
            ]
            == "success"
        )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketCandle)) == 305
        assert session.scalar(select(func.count()).select_from(IngestionRun)) == 3
    seen = []
    ingest_market(engine, market_api(seen=seen), ["BTC-USD"], START, end + DAY, incremental=True)
    first = next(r for r in seen if r.url.path.endswith("/candles"))
    assert int(first.url.params["start"]) == int(midnight(end - 6 * DAY).timestamp())


@pytest.mark.integration
def test_partial_current_day_excluded_and_validation_audited(engine):
    today = START + 3 * DAY
    api = market_api(invalid=raw_candle(today))
    result = ingest_market(engine, api, ["BTC-USD"], START, today + DAY, today=today)
    assert result["outcome"] == "partial"
    assert result["products"][0]["rejected"] == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketCandle)) == 3
        assert session.scalar(select(func.max(MarketCandle.candle_start_utc))) < midnight(today)


@pytest.mark.integration
def test_failed_http_audit_and_no_candles(engine):
    api = MarketAPI(
        httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))),
        sleep=lambda _: None,
    )
    result = ingest_market(engine, api, ["BTC-USD", "ETH-USD"], START, START + DAY)
    assert result["outcome"] == "failed"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketCandle)) == 0
        assert all(
            r.outcome == "failed" and r.finished_at for r in session.scalars(select(IngestionRun))
        )


@pytest.mark.integration
def test_sql_python_parity_recalculation_and_numeric_constraints(engine):
    ingest_market(engine, market_api(), ["BTC-USD"], START, START + 100 * DAY)
    for _ in range(2):
        assert calculate_indicators(engine, ["BTC-USD"])["rows"] == 100
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketRiskIndicator)) == 100
        sql = session.execute(
            text("SELECT daily_return FROM market_daily_returns WHERE observation_date=:day"),
            {"day": START + 5 * DAY},
        ).scalar()
        py = session.scalar(
            select(MarketRiskIndicator.daily_return).where(
                MarketRiskIndicator.observation_date == START + 5 * DAY
            )
        )
        assert float(sql) == pytest.approx(py)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("UPDATE market_candles SET low=high+1"))
    ingest_market(engine, market_api(), ["BTC-USD"], START, START + DAY)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketRiskIndicator)) == 0


@pytest.mark.integration
def test_report_from_database_and_replay(engine, payload, tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        assert ingest(engine, client)["outcome"] == "success"
    end = date(2026, 1, 20)
    ingest_market(engine, market_api(), ["BTC-USD"], START, end)
    result = run_report(engine, ["BTC-USD"], START, end, tmp_path / "first")
    assert result["critical_failures"] == 0
    assert result["incident_context_rows"] == 2
    assert result["coverage_status"] == "partial_observations"
    assert len(list((tmp_path / "first").glob("*.png"))) == 3
    replay = render_snapshot(tmp_path / "first/source_snapshot.json", tmp_path / "replay")
    assert replay["market_observations"] == result["market_observations"]
    for name in (
        "statistical_results.json",
        "daily_market_indicators.csv",
        "incident_market_context.csv",
        "phase2_market_incident_analysis.md",
    ):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "replay" / name).read_bytes()
    with Session(engine) as session:
        assert default_start(session, end) == date(2026, 1, 5) - 90 * DAY


@pytest.mark.integration
def test_coverage_registration_and_frequency_gate(engine):
    record = {
        "source": "coinbase_status",
        "start_date": str(START),
        "end_date": str(START + 100 * DAY),
        "complete": True,
        "evidence": "Synthetic test attestation only",
        "reviewed_by": "test fixture",
    }
    register_coverage(engine, record)
    register_coverage(engine, record)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM incident_coverage")).scalar() == 1
    with pytest.raises(ValueError):
        register_coverage(engine, {**record, "evidence": ""})
    indicators = calculate(candle_rows())
    days = [str(START + i * DAY) for i in range(60, 100)]
    incidents = [fact(START + i * DAY, str(i)) for i in range(60, 100)]
    result = frequency_analysis(incidents, indicators, days, ["BTC-USD"])
    assert result["status"] == "coverage_gated"
    groups = result["products"]["BTC-USD"]["groups"]["absolute_return"]["groups"]
    assert sum(g["observed_days"] for g in groups.values()) == 40
    assert sum(g["incidents"] for g in groups.values()) == 40


@pytest.mark.integration
def test_empty_report_has_explicit_limitations(engine, tmp_path):
    result = run_report(engine, ["BTC-USD"], START, START + DAY, tmp_path)
    assert result["critical_failures"] == 1
    report = (tmp_path / "phase2_market_incident_analysis.md").read_text()
    assert "No market observations were collected" in report
    assert "not zero-incident dates" not in report or "unverified" in report
    assert json.loads((tmp_path / "statistical_results.json").read_text())["critical_quality_gate"]


def test_duration_association_excludes_unresolved_and_reports_effect():
    contexts = [
        {
            **fact(START + i * 7 * DAY, str(i)),
            "product_id": "BTC-USD",
            "duration_hours": i + 1,
            "absolute_return_lag1": i + 1,
            "realized_volatility_30d_lag1": None,
            "volume_zscore_30d_lag1": None,
        }
        for i in range(12)
    ]
    contexts.append({**contexts[-1], "source_id": "unresolved", "duration_hours": None})
    result = next(r for r in duration_analysis(contexts) if r["metric_lag1"] == "absolute_return")
    assert result["n"] == 12
    assert result["missing_or_unresolved"] == 1
    assert result["rho"] == pytest.approx(1)
    assert result["ci95"] == pytest.approx([1, 1])


@pytest.mark.integration
def test_conflicting_duplicates_and_window_failure_are_atomic(engine):
    api = market_api()
    original = api.candles

    def conflict(product, start, end):
        rows = original(product, start, end)
        return rows + [{**rows[0], "volume": "999999"}]

    api.candles = conflict
    assert ingest_market(engine, api, ["BTC-USD"], START, START + DAY)["outcome"] == "failed"

    def second_window_fails(product, start, end):
        if start > START:
            raise SourceError("fixture failure")
        return original(product, start, end)

    api.candles = second_window_fails
    assert ingest_market(engine, api, ["BTC-USD"], START, START + 301 * DAY)["outcome"] == "failed"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(MarketCandle)) == 0


@pytest.mark.integration
def test_multicomponent_view_and_financial_precision(engine, payload):
    payload["incidents"][0]["components"].append({"id": "another", "name": "Another service"})
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        ingest(engine, client)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM operational_incident_facts")).scalar() == 2
        assert (
            conn.execute(
                text("SELECT max(jsonb_array_length(components)) FROM operational_incident_facts")
            ).scalar()
            == 2
        )
    api = market_api()
    original = api.candles

    def exact(product, start, end):
        return [
            {**r, "volume": "12345678901234567890.123456789012345678"}
            for r in original(product, start, end)
        ]

    api.candles = exact
    ingest_market(engine, api, ["BTC-USD"], START, START + DAY)
    with Session(engine) as session:
        assert session.scalar(select(MarketCandle.volume)) == Decimal(
            "12345678901234567890.123456789012345678"
        )
