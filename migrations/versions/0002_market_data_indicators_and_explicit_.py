"""market data indicators and explicit coverage evidence"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incident_coverage",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("end_date > start_date", name="ck_coverage_range"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "start_date", "end_date", name="uq_coverage_interval"),
    )
    op.create_table(
        "market_products",
        sa.Column("product_id", sa.String(length=50), nullable=False),
        sa.Column("base_currency", sa.String(length=30), nullable=False),
        sa.Column("quote_currency", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
    )
    op.create_table(
        "market_candles",
        sa.Column("product_id", sa.String(length=50), nullable=False),
        sa.Column("candle_start_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(length=20), nullable=False),
        sa.Column("open", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("high", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("low", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("close", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("volume", sa.Numeric(precision=38, scale=18), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            (
                "candle_start_utc = date_trunc('day', candle_start_utc AT TIME "
                "ZONE 'UTC') AT TIME ZONE 'UTC'"
            ),
            name="ck_market_midnight",
        ),
        sa.CheckConstraint("granularity = 'ONE_DAY'", name="ck_market_daily"),
        sa.CheckConstraint(
            "low <= open AND low <= close AND high >= open AND high >= close", name="ck_market_ohlc"
        ),
        sa.CheckConstraint(
            "open > 0 AND close > 0 AND low > 0 AND volume >= 0", name="ck_market_positive"
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["market_products.product_id"],
        ),
        sa.PrimaryKeyConstraint("product_id", "candle_start_utc", "granularity"),
    )
    op.create_index("ix_market_candle_date", "market_candles", ["candle_start_utc"], unique=False)
    op.create_table(
        "market_risk_indicators",
        sa.Column("product_id", sa.String(length=50), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("calculation_version", sa.String(length=80), nullable=False),
        sa.Column("daily_return", sa.Float(), nullable=True),
        sa.Column("log_return", sa.Float(), nullable=True),
        sa.Column("absolute_return", sa.Float(), nullable=True),
        sa.Column("realized_volatility_7d", sa.Float(), nullable=True),
        sa.Column("realized_volatility_30d", sa.Float(), nullable=True),
        sa.Column("volume_change", sa.Float(), nullable=True),
        sa.Column("volume_mean_30d", sa.Float(), nullable=True),
        sa.Column("volume_zscore_30d", sa.Float(), nullable=True),
        sa.Column("return_threshold", sa.Float(), nullable=True),
        sa.Column("volatility_threshold", sa.Float(), nullable=True),
        sa.Column("volume_zscore_threshold", sa.Float(), nullable=False),
        sa.Column("market_stress_flag", sa.Boolean(), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["market_products.product_id"],
        ),
        sa.PrimaryKeyConstraint("product_id", "observation_date", "calculation_version"),
    )
    op.create_index(
        "ix_market_indicator_date", "market_risk_indicators", ["observation_date"], unique=False
    )

    op.execute("""
    CREATE VIEW operational_incident_facts AS
    SELECT i.source, i.source_id, i.name, i.status, i.impact,
           i.created_at, i.resolved_at,
           (i.created_at AT TIME ZONE 'UTC')::date AS occurrence_date,
           CASE WHEN i.resolved_at >= i.created_at
                THEN extract(epoch FROM (i.resolved_at - i.created_at))/3600 END
                AS duration_hours,
           (i.raw->>'scheduled_for' IS NOT NULL OR i.raw->>'scheduled_until' IS NOT NULL
            OR i.status IN ('scheduled', 'in_progress', 'verifying')
            OR i.raw->>'type' = 'maintenance') IS TRUE AS is_maintenance,
           COALESCE((SELECT jsonb_agg(c.name ORDER BY c.source_id)
                     FROM incident_components ic JOIN components c
                       ON c.source=ic.source AND c.source_id=ic.component_id
                     WHERE ic.source=i.source AND ic.incident_id=i.source_id), '[]'::jsonb)
                AS components
    FROM incidents i
    """)
    op.execute("""
    CREATE VIEW market_daily_returns AS
    WITH history AS (
      SELECT product_id, candle_start_utc, close, volume,
             lag(candle_start_utc) OVER w AS previous_start,
             lag(close) OVER w AS previous_close,
             lag(volume) OVER w AS previous_volume
      FROM market_candles WHERE granularity='ONE_DAY'
      AND candle_start_utc < date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
      WINDOW w AS (PARTITION BY product_id ORDER BY candle_start_utc)
    )
    SELECT product_id, (candle_start_utc AT TIME ZONE 'UTC')::date AS observation_date,
           CASE WHEN candle_start_utc - previous_start = interval '1 day'
                THEN close / previous_close - 1 END AS daily_return,
           CASE WHEN candle_start_utc - previous_start = interval '1 day'
                THEN ln(close / previous_close) END AS log_return,
           CASE WHEN candle_start_utc - previous_start = interval '1 day'
                THEN volume / nullif(previous_volume, 0) - 1 END AS volume_change
    FROM history
    """)


def downgrade():
    op.execute("DROP VIEW market_daily_returns")
    op.execute("DROP VIEW operational_incident_facts")
    op.drop_index("ix_market_indicator_date", table_name="market_risk_indicators")
    op.drop_table("market_risk_indicators")
    op.drop_index("ix_market_candle_date", table_name="market_candles")
    op.drop_table("market_candles")
    op.drop_table("market_products")
    op.drop_table("incident_coverage")
