"""Environment configuration; credentials are never logged."""

import os

from sqlalchemy.engine import URL, make_url

SOURCE_URL = "https://status.coinbase.com/api/v2/incidents.json"
SOURCE = "coinbase_status"


def database_url() -> URL:
    if value := os.getenv("DATABASE_URL"):
        url = make_url(value)
    else:
        password = os.getenv("DB_PASSWORD")
        if not password:
            raise ValueError("Set DATABASE_URL or DB_PASSWORD (and optional DB_* settings)")
        url = URL.create(
            "postgresql+psycopg",
            username=os.getenv("DB_USER", "eri"),
            password=password,
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "eri"),
        )
    if url.drivername != "postgresql+psycopg":
        raise ValueError("Use a postgresql+psycopg database URL")
    return url
