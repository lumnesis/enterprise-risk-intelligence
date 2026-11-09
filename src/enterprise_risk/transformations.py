"""Validate source records independently; reject a whole incident if a child is invalid.

Absent components mean 'not supplied', not 'remove all known relationships'.
Unknown status/impact values are kept rather than silently forced into an enum.
"""

from dataclasses import dataclass, field
from datetime import UTC
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
UTCTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]


class SourceModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class SourceUpdate(SourceModel):
    id: Identifier
    incident_id: Identifier | None = None
    status: Identifier
    body: str | None = None
    created_at: UTCTime
    updated_at: UTCTime | None = None


class SourceComponent(SourceModel):
    id: Identifier
    name: Label


class SourceIncident(SourceModel):
    id: Identifier
    name: Label
    status: Identifier
    impact: Identifier = "unknown"
    created_at: UTCTime
    updated_at: UTCTime
    resolved_at: UTCTime | None = None
    incident_updates: list[SourceUpdate] = Field(default_factory=list)
    components: list[SourceComponent] | None = None

    @model_validator(mode="after")
    def consistent_record(self) -> "SourceIncident":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at precedes created_at")
        if self.resolved_at and self.resolved_at < self.created_at:
            raise ValueError("resolved_at precedes created_at")
        for update in self.incident_updates:
            if update.incident_id is not None and update.incident_id != self.id:
                raise ValueError("update incident_id does not match its parent")
        for items in (self.incident_updates, self.components or []):
            if len({item.id for item in items}) != len(items):
                raise ValueError("duplicate child identifier")
        return self


@dataclass
class ParsedBatch:
    received: int
    incidents: list[SourceIncident] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)


def parse_incidents(payload: dict[str, Any]) -> ParsedBatch:
    if not isinstance(payload, dict) or not isinstance(payload.get("incidents"), list):
        raise ValueError("Expected a JSON object with an incidents array")
    batch = ParsedBatch(received=len(payload["incidents"]))
    seen: set[str] = set()
    for index, raw in enumerate(payload["incidents"]):
        try:
            record = SourceIncident.model_validate(raw)
            if record.id in seen:
                raise ValueError("duplicate incident identifier within response")
            seen.add(record.id)
            batch.incidents.append(record)
        except (ValidationError, ValueError) as exc:
            errors = (
                [
                    {"field": ".".join(map(str, err["loc"])), "type": err["type"]}
                    for err in exc.errors()
                ]
                if isinstance(exc, ValidationError)
                else [{"field": "id", "type": "duplicate_identifier"}]
            )
            batch.issues.append({"record_index": index, "errors": errors})
    return batch


def incident_values(record: SourceIncident, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "source_id": record.id,
        "name": record.name,
        "status": record.status,
        "impact": record.impact,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "resolved_at": record.resolved_at,
        "raw": record.model_dump(mode="json", exclude_unset=True),
    }
