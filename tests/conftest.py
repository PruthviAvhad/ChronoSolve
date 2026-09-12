"""Shared fixtures for the whole suite.

The suite solves real CP-SAT models rather than mocking them -- the guarantees
worth protecting are properties of the actual solver output. To keep that
affordable, most tests run against a deliberately small department (2 divisions,
~18 sessions) that still exercises every structural feature: multi-hour labs,
parallel electives, room capacity limits and faculty unavailability.

HTTP tests each get a fresh SQLite database in pytest's tmp_path, copied from a
template migrated once per run, so no test can reach a real database or the
rehearsed fixture.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Fast password hashing for test accounts, and a fixed "today": Thursday
# 10 September 2026, so "tomorrow" and "next week" are stable.
os.environ.setdefault("CHRONOSOLVE_PBKDF2_ITERATIONS", "1000")
os.environ["CHRONOSOLVE_TODAY"] = "2026-09-10"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.data.synthetic import DepartmentSpec, build_department  # noqa: E402
from backend.app.db.seed import seed  # noqa: E402
from backend.app.db.session import make_engine, migrate  # noqa: E402
from backend.app.deps import DATABASE  # noqa: E402
from backend.app.solver.engine import generate  # noqa: E402

SMALL = DepartmentSpec(
    seed=11,
    n_batches=2,
    theory_per_batch=3,
    sessions_per_theory=2,
    labs_per_batch=1,
    lab_duration=2,
    elective_options=2,
    sessions_per_elective=1,
    n_faculty=6,
)


@pytest.fixture(scope="session")
def small_instance():
    return build_department(SMALL)


@pytest.fixture(scope="session")
def solved(small_instance):
    """A solved timetable for the small instance, shared across tests."""
    outcome = generate(small_instance, time_limit=20.0, workers=4)
    assert outcome.is_solved, f"fixture instance did not solve: {outcome.status}"
    return outcome


@pytest.fixture(scope="session")
def published(solved):
    return solved.timetable


@pytest.fixture(scope="session")
def full_instance():
    """The demo-scale department, for tests that care about real scale."""
    return build_department()


# --------------------------------------------------------------------------
# Database and HTTP
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated_template(tmp_path_factory):
    """An empty database migrated to head once; each test copies it."""
    path = tmp_path_factory.mktemp("template") / "template.db"
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    migrate(engine)
    engine.dispose()
    return path


@pytest.fixture
def seeded_db(small_instance, published, migrated_template, tmp_path):
    """A fresh database holding the small department, published as version 1
    with the label 'test-baseline', plus the demo accounts."""
    path = tmp_path / "test.db"
    shutil.copy(migrated_template, path)
    DATABASE.configure(
        f"sqlite:///{path.as_posix()}",
        run_migrations=False,
        seeder=lambda db: seed(db, small_instance, published, "test-baseline"),
    )
    yield DATABASE
    DATABASE.dispose()


@pytest.fixture
def sign_in(seeded_db):
    """Make a client signed in as a given account."""
    from backend.app import api

    clients: list[TestClient] = []

    def make(username: str, password: str) -> TestClient:
        c = TestClient(api.app)
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.close()


@pytest.fixture
def client(sign_in):
    """Signed in as the timetable coordinator."""
    return sign_in("admin", "admin123")


@pytest.fixture
def anonymous(seeded_db):
    from backend.app import api

    with TestClient(api.app) as c:
        yield c
