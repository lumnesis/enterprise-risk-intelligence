from copy import deepcopy
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from enterprise_risk.analytics.queries import counts, quality, summary
from enterprise_risk.ingestion.coinbase import SourceError
from enterprise_risk.ingestion.service import ingest
from enterprise_risk.models import Incident, IncidentUpdate

pytestmark = pytest.mark.integration


def run(engine, payload):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as c:
        return ingest(engine, c)


def test_idempotency_and_changes(engine, payload):
    assert run(engine, payload)["processed"] == 2
    assert run(engine, payload)["processed"] == 2
    assert counts(engine) == {
        "incidents": 2,
        "incident_updates": 3,
        "components": 1,
        "incident_components": 1,
        "ingestion_runs": 2,
    }
    changed = deepcopy(payload)
    changed["incidents"][0].update(
        name="Updated incident",
        status="monitoring",
        updated_at="2026-01-05T13:00:00Z",
        resolved_at=None,
    )
    changed["incidents"][0]["incident_updates"][0]["body"] = "Corrected update"
    changed["incidents"][0]["components"] = []
    run(engine, changed)
    with Session(engine) as session:
        incident = session.get(Incident, ("coinbase_status", "incident-alpha"))
        assert incident.name == "Updated incident" and incident.resolved_at is None
        update = session.get(IncidentUpdate, ("coinbase_status", "update-alpha-1"))
        assert update.body == "Corrected update"
        assert incident.created_at == datetime(2026, 1, 5, 10, tzinfo=UTC)
    assert counts(engine)["incident_components"] == 0
    assert counts(engine)["incident_updates"] == 3
    assert run(engine, payload)["processed"] == 1  # older alpha snapshot is ignored
    with Session(engine) as session:
        assert (
            session.get(Incident, ("coinbase_status", "incident-alpha")).name == "Updated incident"
        )


def test_missing_components_preserves_relationship(engine, payload):
    run(engine, payload)
    del payload["incidents"][0]["components"]
    run(engine, payload)
    assert counts(engine)["incident_components"] == 1


def test_partial_and_rejected_runs(engine, payload):
    payload["incidents"][0]["created_at"] = "bad"
    assert run(engine, payload)["outcome"] == "partial"
    assert counts(engine)["incidents"] == 1
    assert quality(engine)[0]["records_rejected"] == 1
    payload["incidents"][1]["id"] = None
    assert run(engine, payload)["outcome"] == "failed"
    assert quality(engine)[0]["records_processed"] == 0


def test_http_failure_is_audited(engine):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
        with pytest.raises(SourceError, match="503"):
            ingest(engine, client)
    assert quality(engine)[0]["outcome"] == "failed"
    assert quality(engine)[0]["finished_at"] is not None
    assert counts(engine)["incidents"] == 0


def test_database_failure_rolls_back_whole_batch(engine, payload):
    # Same update ID under two parents cannot be silently reassigned.
    payload["incidents"][1]["incident_updates"][0]["id"] = "update-alpha-1"
    with pytest.raises(SourceError, match="ValueError"):
        run(engine, payload)
    assert counts(engine)["incidents"] == 0
    assert quality(engine)[0]["outcome"] == "failed"


def test_constraints(engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO incident_components VALUES ('coinbase_status', 'x', 'y')""")
        )


def test_summary_utc_and_empty_response(engine, payload):
    assert run(engine, {"incidents": []})["outcome"] == "success"
    assert summary(engine)["by_impact"] == []
    run(engine, payload)
    report = summary(engine)
    assert report["by_impact"] == [{"impact": "major", "count": 1}, {"impact": "minor", "count": 1}]
    assert [row["count"] for row in report["by_status"]] == [1, 1]
    assert len(report["by_creation_date"]) == 1
    assert str(report["by_creation_date"][0]["date"]) == "2026-01-05"
    assert report["by_creation_date"][0]["count"] == 2
