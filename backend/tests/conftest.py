"""Test fixtures. All data generated here is SYNTHETIC and used only in tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from incendium.config import Settings  # noqa: E402
from incendium.db import Database  # noqa: E402


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.sqlite3",
        firms_map_key="TESTKEY_firms_0123456789abcdef",
        cdse_client_id="test-client",
        cdse_client_secret="test-secret-value-xyz",
        cors_origins=("http://localhost:5173",),
        risk_regions=("karnataka",),
        enable_scheduler=False,
        allow_manual_refresh=True,
    )


@pytest.fixture
def db(settings) -> Database:
    return Database(settings.db_path)


@pytest.fixture
def app(settings, db):
    from incendium import create_app

    return create_app(settings=settings, db=db, start_jobs=False)


@pytest.fixture
def client(app):
    return app.test_client()


FIRMS_HEADER = "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_ti5,frp,daynight"


def firms_csv(rows: list[str]) -> str:
    return "\n".join([FIRMS_HEADER, *rows]) + "\n"
