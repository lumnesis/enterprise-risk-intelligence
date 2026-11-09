from typing import Any

from sqlalchemy import Engine, text

TABLES = ("incidents", "incident_updates", "components", "incident_components", "ingestion_runs")


def counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            for table in TABLES
        }


def summary(engine: Engine) -> dict[str, Any]:
    queries = {
        "by_impact": (
            "SELECT impact, count(*) AS count FROM incidents GROUP BY impact ORDER BY impact"
        ),
        "by_status": (
            "SELECT status, count(*) AS count FROM incidents GROUP BY status ORDER BY status"
        ),
        "by_creation_date": """
            SELECT (created_at AT TIME ZONE 'UTC')::date AS date, count(*) AS count
            FROM incidents GROUP BY 1 ORDER BY 1
        """,
    }
    with engine.connect() as connection:
        return {
            name: [dict(row) for row in connection.execute(text(sql)).mappings()]
            for name, sql in queries.items()
        }


def quality(engine: Engine) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                text("""
            SELECT id, started_at, finished_at, outcome, records_received,
                   records_processed, records_rejected, quality_issues, error
            FROM ingestion_runs ORDER BY id DESC LIMIT 20
        """)
            ).mappings()
        ]
