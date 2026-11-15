"""Idempotent per-product transactions, resumable gaps, and Phase 1 audit reuse."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from enterprise_risk.ingestion.coinbase import SourceError
from enterprise_risk.ingestion.service import upsert
from enterprise_risk.market.api import DAY, SOURCE, MarketAPI, midnight, parse_candle, windows
from enterprise_risk.models import (
    Incident,
    IngestionRun,
    MarketCandle,
    MarketProduct,
    MarketRiskIndicator,
)


def default_start(session: Session, end: date) -> date:
    earliest = session.scalar(select(func.min(Incident.created_at)))
    # 90 preceding days cover 30 return observations plus a 30-day volatility baseline.
    return (earliest.astimezone(UTC).date() if earliest else end - timedelta(days=365)) - timedelta(
        days=90
    )


def incremental_start(session: Session, product: str, start: date, end: date) -> date:
    observed = set(
        session.scalars(
            select(MarketCandle.candle_start_utc).where(
                MarketCandle.product_id == product,
                MarketCandle.candle_start_utc >= midnight(start),
                MarketCandle.candle_start_utc < midnight(end),
            )
        )
    )
    day = start
    while day < end and midnight(day) in observed:
        day += DAY
    # Reconcile the last seven completed days, and retry every earlier gap.
    return max(start, min(day, end - timedelta(days=7)))


def ingest_market(
    engine, api: MarketAPI, products, start=None, end=None, *, incremental=False, today=None
):
    today = today or datetime.now(UTC).date()
    end = min(end or today, today)
    results = []
    for product in dict.fromkeys(products):
        with Session(engine) as session, session.begin():
            begin = start or default_start(session, end)
            if begin >= end:
                raise ValueError("Market start must precede the finalized end date")
            if incremental:
                begin = incremental_start(session, product, begin, end)
            run = IngestionRun(
                source=SOURCE,
                started_at=datetime.now(UTC),
                outcome="running",
                quality_issues=[
                    {"product_id": product, "start": str(begin), "end_exclusive": str(end)}
                ],
            )
            session.add(run)
            session.flush()
            run_id = run.id
        issues, received, rejected, candles = [], 0, 0, {}
        error = None
        try:
            metadata = api.product(product)
            for first, stop in windows(begin, end):
                batch = api.candles(product, first, stop)
                received += len(batch)
                for index, raw in enumerate(batch):
                    try:
                        candle = parse_candle(raw)
                        stamp = candle["candle_start_utc"]
                        if not midnight(first) <= stamp < midnight(stop):
                            raise ValueError("Out-of-window or incomplete candle")
                        if stamp in candles:
                            if candles[stamp] != candle:
                                raise SourceError("Conflicting duplicate candles")
                            issues.append(
                                {"kind": "duplicate_response_candle", "date": str(stamp.date())}
                            )
                        candles[stamp] = candle
                    except ValueError as exc:
                        rejected += 1
                        issues.append(
                            {
                                "kind": "invalid_candle",
                                "window_start": str(first),
                                "index": index,
                                "reason": str(exc),
                            }
                        )
            missing = [
                str(begin + i * DAY)
                for i in range((end - begin).days)
                if midnight(begin + i * DAY) not in candles
            ]
            if missing:
                issues.append(
                    {"kind": "missing_observations", "count": len(missing), "dates": missing}
                )
            with Session(engine) as session, session.begin():
                session.execute(text("SELECT pg_advisory_xact_lock(721349002)"))
                upsert(session, MarketProduct, metadata)
                for candle in candles.values():
                    upsert(
                        session,
                        MarketCandle,
                        {
                            **candle,
                            "product_id": product,
                            "source": SOURCE,
                            "ingested_at": datetime.now(UTC),
                            "ingestion_run_id": run_id,
                        },
                    )
                # Derived values must never survive an upstream correction unmarked.
                if candles:
                    session.execute(
                        delete(MarketRiskIndicator).where(MarketRiskIndicator.product_id == product)
                    )
        except (SourceError, ValueError) as exc:
            error = str(exc)
        except Exception as exc:
            error = f"Market ingestion failed: {type(exc).__name__}"
        outcome = "failed" if error else "partial" if issues else "success"
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            run.finished_at = datetime.now(UTC)
            run.outcome = outcome
            run.records_received = received
            run.records_processed = 0 if error else len(candles)
            run.records_rejected = rejected
            run.quality_issues = run.quality_issues + issues
            run.error = error
        results.append(
            {
                "product_id": product,
                "run_id": run_id,
                "outcome": outcome,
                "received": received,
                "processed": 0 if error else len(candles),
                "rejected": rejected,
                "start": str(begin),
                "end_exclusive": str(end),
                "issues": issues,
                "error": error,
            }
        )
    return {
        "outcome": "failed"
        if all(r["outcome"] == "failed" for r in results)
        else "partial"
        if any(r["outcome"] != "success" for r in results)
        else "success",
        "products": results,
    }
