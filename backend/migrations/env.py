"""Alembic environment.

Two ways in: the application passes an open connection (see
`backend.app.db.session.migrate`), or a developer runs the `alembic` CLI from
the repository root, in which case the URL comes from the same environment
variables the application reads.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from backend.app.db.models import Base
from backend.app.db.session import database_url, make_engine

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or database_url()


def run_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    # CLI use: configure logging without silencing loggers others set up.
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    engine = make_engine(_url())
    with engine.connect() as conn:
        context.configure(
            connection=conn, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
