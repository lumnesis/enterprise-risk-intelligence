"""Normalized records keyed by stable provider identifiers; all times are UTC aware."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Incident(Base):
    __tablename__ = "incidents"
    source: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(100))
    impact: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        Index("ix_incidents_created_at", "created_at"),
        Index("ix_incidents_status_impact", "status", "impact"),
    )


class IncidentUpdate(Base):
    __tablename__ = "incident_updates"
    source: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(100))
    body: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "incident_id"],
            ["incidents.source", "incidents.source_id"],
            ondelete="CASCADE",
        ),
        Index("ix_updates_incident", "source", "incident_id"),
    )


class Component(Base):
    __tablename__ = "components"
    source: Mapped[str] = mapped_column(String(50), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)


class IncidentComponent(Base):
    __tablename__ = "incident_components"
    source: Mapped[str] = mapped_column(String(50), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    component_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["source", "incident_id"],
            ["incidents.source", "incidents.source_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source", "component_id"],
            ["components.source", "components.source_id"],
            ondelete="CASCADE",
        ),
        Index("ix_incident_components_component", "source", "component_id"),
    )


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(30))
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_processed: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    quality_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    error: Mapped[str | None] = mapped_column(Text)
