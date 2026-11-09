"""Atomic data ingestion with a separately committed execution audit trail."""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from enterprise_risk.config import SOURCE
from enterprise_risk.ingestion.coinbase import SourceError, fetch_incidents
from enterprise_risk.models import (
    Component,
    Incident,
    IncidentComponent,
    IncidentUpdate,
    IngestionRun,
)
from enterprise_risk.transformations import ParsedBatch, incident_values, parse_incidents

logger = logging.getLogger(__name__)


def upsert(session: Session, model: Any, values: dict[str, Any]) -> None:
    statement = insert(model).values(**values)
    keys = [column.name for column in model.__table__.primary_key]
    session.execute(
        statement.on_conflict_do_update(
            index_elements=keys,
            set_={key: getattr(statement.excluded, key) for key in values if key not in keys},
        )
    )


def store_batch(session: Session, batch: ParsedBatch) -> int:
    # Serialize this source's writes, including the freshness check and relation replacement.
    session.execute(text("SELECT pg_advisory_xact_lock(721349001)"))
    processed = 0
    for record in batch.incidents:
        existing = session.get(Incident, (SOURCE, record.id))
        if existing and existing.updated_at > record.updated_at:
            continue  # Never overwrite a newer snapshot with an older response.
        upsert(session, Incident, incident_values(record, SOURCE))
        for update in record.incident_updates:
            owner = session.scalar(
                select(IncidentUpdate.incident_id).where(
                    IncidentUpdate.source == SOURCE,
                    IncidentUpdate.source_id == update.id,
                )
            )
            if owner is not None and owner != record.id:
                raise ValueError("Source update identifier belongs to another incident")
            upsert(
                session,
                IncidentUpdate,
                {
                    "source": SOURCE,
                    "source_id": update.id,
                    "incident_id": record.id,
                    "status": update.status,
                    "body": update.body,
                    "created_at": update.created_at,
                    "updated_at": update.updated_at,
                    "raw": update.model_dump(mode="json", exclude_unset=True),
                },
            )
        if record.components is not None:
            session.execute(
                delete(IncidentComponent).where(
                    IncidentComponent.source == SOURCE,
                    IncidentComponent.incident_id == record.id,
                )
            )
            for component in record.components:
                upsert(
                    session,
                    Component,
                    {
                        "source": SOURCE,
                        "source_id": component.id,
                        "name": component.name,
                        "raw": component.model_dump(mode="json", exclude_unset=True),
                    },
                )
                session.add(
                    IncidentComponent(
                        source=SOURCE, incident_id=record.id, component_id=component.id
                    )
                )
        processed += 1
    return processed


def ingest(engine: Engine, client: httpx.Client) -> dict[str, Any]:
    started = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        run = IngestionRun(source=SOURCE, started_at=started, outcome="running")
        session.add(run)
        session.flush()
        run_id = run.id
    batch = None
    try:
        batch = parse_incidents(fetch_incidents(client))
        with Session(engine) as session, session.begin():
            processed = store_batch(session, batch)
            run = session.get(IngestionRun, run_id)
            assert run is not None
            run.finished_at = datetime.now(UTC)
            run.records_received = batch.received
            run.records_processed = processed
            run.records_rejected = len(batch.issues)
            run.quality_issues = batch.issues
            run.outcome = (
                "failed"
                if batch.issues and not batch.incidents
                else "partial"
                if batch.issues
                else "success"
            )
            result = {
                "run_id": run_id,
                "outcome": run.outcome,
                "received": batch.received,
                "processed": processed,
                "rejected": len(batch.issues),
                "quality_issues": batch.issues,
            }
        logger.info("Ingestion run %s: %s", run_id, result["outcome"])
        return result
    except Exception as exc:
        # Do not persist SQL parameters, credentials, arbitrary payloads, or response bodies.
        message = (
            str(exc) if isinstance(exc, SourceError) else f"Ingestion failed: {type(exc).__name__}"
        )
        with Session(engine) as session, session.begin():
            run = session.get(IngestionRun, run_id)
            assert run is not None
            run.outcome = "failed"
            run.finished_at = datetime.now(UTC)
            run.error = message
            if batch is not None:
                run.records_received = batch.received
                run.records_rejected = len(batch.issues)
                run.quality_issues = batch.issues
        raise SourceError(message) from exc
