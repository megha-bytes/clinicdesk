"""Test setup: isolated SQLite DB, no real secrets, no telemetry export."""
import os
import tempfile
from pathlib import Path

# Must run before `app` is imported (the engine is created at import time).
_tmp = Path(tempfile.mkdtemp(prefix="clinicdesk-test-"))
os.environ["APP_ENV"] = "test"
# Set TEST_DATABASE_URL to run the suite against Postgres instead of SQLite.
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["SECRETS_PROVIDER"] = "env"
os.environ.pop("TOKEN_FACTORY_API_KEY", None)

import pytest  # noqa: E402

from app import models  # noqa: E402,F401
from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s
