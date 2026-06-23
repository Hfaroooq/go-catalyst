"""Shared test fixtures.

``db_session`` binds a SQLAlchemy session to a single connection with an open
transaction, and rolls it back after the test — so DB-backed tests run against
the real Postgres schema but never leave any data behind. If no database is
reachable (e.g. CI without secrets), the test is skipped rather than failing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session


@pytest.fixture
def db_session() -> Iterator[Session]:
    from catalyst.db.session import get_engine

    try:
        engine = get_engine()
        connection = engine.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no database available: {exc}")

    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
