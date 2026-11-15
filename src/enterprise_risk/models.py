"""Normalized records keyed by stable provider identifiers; all times are UTC aware."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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


class MarketProduct(Base):
    __tablename__ = "market_products"
    product_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(30))
    quote_currency: Mapped[str] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(50))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)


class MarketCandle(Base):
    __tablename__ = "market_candles"
    product_id: Mapped[str] = mapped_column(
        ForeignKey("market_products.product_id"), primary_key=True
    )
    candle_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    granularity: Mapped[str] = mapped_column(String(20), primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    high: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    low: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    close: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    source: Mapped[str] = mapped_column(String(50))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint(
            "open > 0 AND close > 0 AND low > 0 AND volume >= 0", name="ck_market_positive"
        ),
        CheckConstraint(
            "low <= open AND low <= close AND high >= open AND high >= close", name="ck_market_ohlc"
        ),
        CheckConstraint("granularity = 'ONE_DAY'", name="ck_market_daily"),
        CheckConstraint(
            (
                "candle_start_utc = date_trunc('day', candle_start_utc AT TIME "
                "ZONE 'UTC') AT TIME ZONE 'UTC'"
            ),
            name="ck_market_midnight",
        ),
        Index("ix_market_candle_date", "candle_start_utc"),
    )


class MarketRiskIndicator(Base):
    __tablename__ = "market_risk_indicators"
    product_id: Mapped[str] = mapped_column(
        ForeignKey("market_products.product_id"), primary_key=True
    )
    observation_date: Mapped[date] = mapped_column(Date, primary_key=True)
    calculation_version: Mapped[str] = mapped_column(String(80), primary_key=True)
    daily_return: Mapped[float | None] = mapped_column(Float)
    log_return: Mapped[float | None] = mapped_column(Float)
    absolute_return: Mapped[float | None] = mapped_column(Float)
    realized_volatility_7d: Mapped[float | None] = mapped_column(Float)
    realized_volatility_30d: Mapped[float | None] = mapped_column(Float)
    volume_change: Mapped[float | None] = mapped_column(Float)
    volume_mean_30d: Mapped[float | None] = mapped_column(Float)
    volume_zscore_30d: Mapped[float | None] = mapped_column(Float)
    return_threshold: Mapped[float | None] = mapped_column(Float)
    volatility_threshold: Mapped[float | None] = mapped_column(Float)
    volume_zscore_threshold: Mapped[float] = mapped_column(Float)
    market_stress_flag: Mapped[bool | None] = mapped_column(Boolean)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    __table_args__ = (Index("ix_market_indicator_date", "observation_date"),)


class IncidentCoverage(Base):
    "Reviewed external evidence; successful latest-50 polls alone cannot establish completeness."

    __tablename__ = "incident_coverage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(50))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)  # exclusive
    evidence: Mapped[str] = mapped_column(Text)
    reviewed_by: Mapped[str] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("end_date > start_date", name="ck_coverage_range"),
        UniqueConstraint("source", "start_date", "end_date", name="uq_coverage_interval"),
    )
