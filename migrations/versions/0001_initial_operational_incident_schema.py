"""Initial operational incident schema"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "components",
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id"),
    )
    op.create_table(
        "incidents",
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=100), nullable=False),
        sa.Column("impact", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("source", "source_id"),
    )
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"], unique=False)
    op.create_index("ix_incidents_status_impact", "incidents", ["status", "impact"], unique=False)
    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("records_processed", sa.Integer(), nullable=False),
        sa.Column("records_rejected", sa.Integer(), nullable=False),
        sa.Column("quality_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "incident_components",
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("incident_id", sa.String(length=100), nullable=False),
        sa.Column("component_id", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "component_id"],
            ["components.source", "components.source_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source", "incident_id"],
            ["incidents.source", "incidents.source_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source", "incident_id", "component_id"),
    )
    op.create_index(
        "ix_incident_components_component",
        "incident_components",
        ["source", "component_id"],
        unique=False,
    )
    op.create_table(
        "incident_updates",
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_id", sa.String(length=100), nullable=False),
        sa.Column("incident_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["source", "incident_id"],
            ["incidents.source", "incidents.source_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source", "source_id"),
    )
    op.create_index(
        "ix_updates_incident", "incident_updates", ["source", "incident_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_updates_incident", table_name="incident_updates")
    op.drop_table("incident_updates")
    op.drop_index("ix_incident_components_component", table_name="incident_components")
    op.drop_table("incident_components")
    op.drop_table("ingestion_runs")
    op.drop_index("ix_incidents_status_impact", table_name="incidents")
    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("components")
