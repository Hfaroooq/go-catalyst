"""Alembic migration environment.

We pull the database URL from our application settings (environment / .env) rather than from
``alembic.ini`` so that no secret is stored in a committed file, and so migrations use the exact
same connection logic as the app.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from catalyst.db.models import Base
from catalyst.db.session import normalize_database_url
from catalyst.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# What Alembic compares the database against when autogenerating migrations.
target_metadata = Base.metadata


def _url() -> str:
    return normalize_database_url(get_settings().database_url)


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (``alembic upgrade --sql``)."""
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
