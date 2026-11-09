from datetime import UTC, datetime

import pytest

from enterprise_risk.transformations import incident_values, parse_incidents


def test_parsing_and_transformation(payload):
    batch = parse_incidents(payload)
    assert batch.received == 2 and not batch.issues
    beta = batch.incidents[1]
    assert beta.created_at == datetime(2026, 1, 5, 23, tzinfo=UTC)
    assert beta.components is None and beta.resolved_at is None
    assert beta.incident_updates[0].body is None
    row = incident_values(batch.incidents[0], "coinbase_status")
    assert row["source_id"] == "incident-alpha"
    assert row["raw"]["shortlink"] == "https://example.test/alpha"


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", None),
        ("id", " "),
        ("created_at", "not-a-date"),
        ("created_at", "2026-01-05T10:00:00"),
        ("updated_at", "2020-01-01T00:00:00Z"),
        ("resolved_at", "2020-01-01T00:00:00Z"),
    ],
)
def test_invalid_record_is_rejected_without_losing_valid_records(payload, field, value):
    payload["incidents"][0][field] = value
    batch = parse_incidents(payload)
    assert len(batch.incidents) == 1
    assert len(batch.issues) == 1
    assert batch.issues[0]["record_index"] == 0


def test_missing_identifier(payload):
    del payload["incidents"][0]["id"]
    assert parse_incidents(payload).issues[0]["errors"] == [{"field": "id", "type": "missing"}]


def test_optional_and_future_fields(payload):
    record = payload["incidents"][1]
    del record["impact"]
    record["status"] = "new-provider-status"
    record["future_field"] = "retained"
    parsed = parse_incidents(payload).incidents[1]
    assert parsed.impact == "unknown"
    assert parsed.model_dump()["future_field"] == "retained"


@pytest.mark.parametrize("change", ["parent", "duplicate", "bad_time"])
def test_invalid_child_rejects_parent(payload, change):
    incident = payload["incidents"][0]
    if change == "parent":
        incident["incident_updates"][0]["incident_id"] = "wrong"
    elif change == "duplicate":
        incident["incident_updates"].append(incident["incident_updates"][0])
    else:
        incident["incident_updates"][0]["created_at"] = "bad"
    assert len(parse_incidents(payload).issues) == 1


def test_duplicate_incident_reported(payload):
    payload["incidents"].append(payload["incidents"][0])
    batch = parse_incidents(payload)
    assert len(batch.incidents) == 2 and len(batch.issues) == 1


@pytest.mark.parametrize("payload", [{}, {"incidents": None}, [], {"incidents": {}}])
def test_invalid_envelope(payload):
    with pytest.raises(ValueError, match="incidents array"):
        parse_incidents(payload)
