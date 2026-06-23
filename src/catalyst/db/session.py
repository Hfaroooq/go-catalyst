"""Database connection + session management.

One engine per process (cached), and a simple ``session_scope`` context manager that commits on
success and rolls back on error. Everything that touches the database goes through here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from catalyst.config import get_settings


def normalize_database_url(url: str) -> str:
    """Force SQLAlchemy to use the modern psycopg (v3) driver.

    Supabase hands you a ``postgresql://...`` URL. SQLAlchemy needs the driver named explicitly,
    so we rewrite the scheme to ``postgresql+psycopg://``.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):  # some providers use this older alias
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


@lru_cache
def get_engine() -> Engine:
    """Return the process-wide SQLAlchemy engine (created once)."""
    url = normalize_database_url(get_settings().database_url)
    # pool_pre_ping avoids handing out connections that the server has already dropped.
    return create_engine(url, pool_pre_ping=True, future=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session: commit on success, roll back on error."""
    session = _session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
