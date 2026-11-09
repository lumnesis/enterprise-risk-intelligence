import json
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


@pytest.fixture
def payload():
    return json.loads((Path(__file__).parent / "fixtures" / "incidents.json").read_text())


@pytest.fixture
def engine(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated, disposable PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE ingestion_runs, incidents, components RESTART IDENTITY CASCADE")
        )
    yield engine
    engine.dispose()
