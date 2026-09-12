"""Where the database is, how to connect to it, and how to migrate it."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE = ROOT / "data" / "chronosolve.db"
ALEMBIC_INI = ROOT / "alembic.ini"
MIGRATIONS = ROOT / "backend" / "migrations"


def database_url() -> str:
    """The configured database, PostgreSQL first.

    `CHRONOSOLVE_DATABASE_URL` -- or the hosting platform's `DATABASE_URL` --
    selects the database. Without either, a local SQLite file keeps the demo
    runnable with no setup at all.
    """
    url = os.getenv("CHRONOSOLVE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return f"sqlite:///{DEFAULT_SQLITE.as_posix()}"
    # Platforms hand out bare postgres:// URLs; SQLAlchemy must be told the driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        path = url.split("sqlite:///", 1)[-1]
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url, connect_args={"check_same_thread": False, "timeout": 30}
        )

        @event.listens_for(engine, "connect")
        def _enforce_foreign_keys(dbapi_connection, _record) -> None:
            # SQLite ignores foreign keys unless asked, per connection.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def migrate(engine: Engine) -> None:
    """Bring the schema to the latest migration. Safe to run on every start."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(MIGRATIONS))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
