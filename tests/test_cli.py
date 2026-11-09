import sys

import pytest

from enterprise_risk.cli import main
from enterprise_risk.config import database_url


def test_missing_database_configuration(monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setattr(sys, "argv", ["eri", "counts"])
    assert main() == 1
    assert "Set DATABASE_URL" in caplog.text


def test_configuration_escapes_password(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_PASSWORD", "test-only@/with:symbols")
    assert database_url().password == "test-only@/with:symbols"
    assert "test-only" not in str(database_url())


def test_rejects_other_database_dialects(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    with pytest.raises(ValueError, match="postgresql"):
        database_url()
