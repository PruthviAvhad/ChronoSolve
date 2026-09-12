"""The database layer: schema, seeding, round trips, versions and secrets.

Runs on SQLite; the same ORM schema and migration run on PostgreSQL in
deployment (see the README for verifying against a real server).
"""

from __future__ import annotations

import shutil
from datetime import date, timedelta

import pytest
from sqlalchemy import inspect, select

from backend.app.data.store import load_snapshot
from backend.app.db import models as m
from backend.app.db import repository as repo
from backend.app.db.models import utcnow
from backend.app.db.seed import FIXTURE, restore_rehearsed, seed, seed_if_empty
from backend.app.db.session import database_url, make_engine, session_factory
from backend.app.services.auth import token_digest
from backend.app.services.constraints import (
    ACTIVE,
    BASE,
    LOCK,
    TEMPORARY,
    ConstraintRecord,
)
from backend.app.solver.disruption import BATCH_UNAVAILABLE, FACULTY_UNAVAILABLE
from backend.app.solver.validate import validate

TABLES = {
    "departments",
    "programs",
    "academic_years",
    "semesters",
    "batches",
    "faculty",
    "faculty_slots",
    "rooms",
    "room_capabilities",
    "room_blocks",
    "batch_blocks",
    "subjects",
    "sessions",
    "constraint_records",
    "users",
    "auth_sessions",
    "timetables",
    "timetable_versions",
    "scheduled_sessions",
    "schedule_requests",
    "settings",
}


@pytest.fixture
def db(migrated_template, tmp_path):
    path = tmp_path / "persist.db"
    shutil.copy(migrated_template, path)
    engine = make_engine(f"sqlite:///{path.as_posix()}")
    with session_factory(engine)() as session:
        yield session
    engine.dispose()


def test_the_migration_creates_every_table(migrated_template):
    engine = make_engine(f"sqlite:///{migrated_template.as_posix()}")
    names = set(inspect(engine).get_table_names())
    engine.dispose()
    assert TABLES <= names
    assert "alembic_version" in names


def test_the_rehearsed_fixture_seeds_version_one(db):
    assert seed_if_empty(db, FIXTURE) is True
    v = repo.current_version(db)
    assert (v.number, v.label, v.status, v.is_current) == (1, "published-v1", "PUBLISHED", True)
    tt = repo.version_timetable(db, v)
    assert len(tt.placements) == 156
    assert validate(repo.load_instance(db), tt).is_clean


def test_seeding_is_idempotent(db):
    seed_if_empty(db, FIXTURE)
    assert seed_if_empty(db, FIXTURE) is False
    assert len(repo.list_versions(db)) == 1


def test_the_configuration_round_trips_through_the_database(db, small_instance, published):
    seed(db, small_instance, published, "t", with_users=False)
    back = repo.load_instance(db)
    assert back.rooms == small_instance.rooms
    assert back.faculty == small_instance.faculty
    assert back.batches == small_instance.batches
    assert back.sessions == small_instance.sessions
    assert back.name == small_instance.name


def test_divisions_keep_their_place_in_the_hierarchy(db, full_instance, published):
    seed(db, full_instance, published, "t", with_users=False)
    rows = db.execute(
        select(m.Department.name, m.Program.name, m.AcademicYear.label, m.Semester.number)
        .join(m.Program, m.Program.department_id == m.Department.id)
        .join(m.AcademicYear, m.AcademicYear.program_id == m.Program.id)
        .join(m.Semester, m.Semester.year_id == m.AcademicYear.id)
    ).all()
    assert {(r[2], r[3]) for r in rows} == {
        ("Second Year", 3),
        ("Third Year", 5),
        ("Final Year", 7),
    }


def test_only_one_version_is_ever_current(db, small_instance, published):
    first = seed(db, small_instance, published, "v1", with_users=False)
    second = repo.add_version(
        db,
        instance=small_instance,
        timetable=published,
        status=repo.PROPOSED,
        label="v2",
        created_by="t",
        reason="test",
        source=first,
    )
    repo.publish(db, second)
    db.commit()
    current = list(db.scalars(select(m.VersionRow).where(m.VersionRow.is_current.is_(True))))
    assert [v.id for v in current] == [second.id]
    db.refresh(first)
    assert first.status == repo.SUPERSEDED


def test_a_version_keeps_the_instance_it_was_solved_for(db, small_instance, published):
    v = seed(db, small_instance, published, "v1", with_users=False)
    room = small_instance.rooms[0]
    repo.update_room(db, room.id, capacity=room.capacity + 50)
    db.commit()
    assert repo.load_instance(db).room_by_id[room.id].capacity == room.capacity + 50
    assert repo.version_instance(v).room_by_id[room.id].capacity == room.capacity


def test_a_proposal_carries_the_rules_it_assumed(db, small_instance, published):
    seed(db, small_instance, published, "v1", with_users=False)
    assumed = [
        ConstraintRecord(category=BASE, kind=FACULTY_UNAVAILABLE, target_id="F01", days=(4,)),
        ConstraintRecord(category=BASE, kind=LOCK, session_id=small_instance.sessions[0].id, lock_timeslot=3),
    ]
    proposal = repo.add_version(
        db,
        instance=small_instance,
        timetable=published,
        status=repo.PROPOSED,
        label="p",
        created_by="t",
        reason="r",
        pending_rules=assumed,
    )
    db.commit()
    back = repo.version_pending_rules(proposal)
    assert [(r.kind, r.target_id, r.days, r.session_id) for r in back] == [
        (r.kind, r.target_id, r.days, r.session_id) for r in assumed
    ]


def test_rules_persist_with_their_dates(db, small_instance, published):
    seed(db, small_instance, published, "v1", with_users=False)
    repo.add_record(
        db,
        ConstraintRecord(
            category=BASE, kind=BATCH_UNAVAILABLE, target_id="SE-A", start_time="16:00"
        ),
    )
    repo.add_record(
        db,
        ConstraintRecord(
            category=TEMPORARY,
            kind=FACULTY_UNAVAILABLE,
            target_id="F02",
            start_date=date(2026, 9, 11),
            end_date=date(2026, 9, 11),
            start_time="13:00",
            end_time="16:00",
        ),
    )
    db.commit()
    base, temporary = repo.records(db)
    assert (base.category, base.target_id, base.start_time, base.status) == (
        BASE,
        "SE-A",
        "16:00",
        ACTIVE,
    )
    assert (temporary.start_date, temporary.end_date) == (date(2026, 9, 11), date(2026, 9, 11))


def test_passwords_are_salted_and_hashed(db, small_instance, published):
    seed(db, small_instance, published, "v1")
    admin = repo.user_by_username(db, "admin")
    other = repo.create_user(
        db, username="second", password="admin123", role="ADMIN", display_name="x"
    )
    assert admin.password_hash.startswith("pbkdf2_sha256$")
    assert "admin123" not in admin.password_hash
    assert admin.password_hash != other.password_hash  # same password, own salt
    assert repo.authenticate(db, "admin", "admin123").id == admin.id
    assert repo.authenticate(db, "admin", "wrong") is None


def test_sign_in_tokens_are_stored_only_as_digests(db, small_instance, published):
    seed(db, small_instance, published, "v1")
    user = repo.user_by_username(db, "admin")
    token = repo.open_session(db, user)
    db.commit()
    stored = [s.token_digest for s in db.scalars(select(m.AuthSession))]
    assert token not in stored
    assert token_digest(token) in stored
    assert repo.user_for_token(db, token).id == user.id


def test_an_expired_session_is_refused_and_removed(db, small_instance, published):
    seed(db, small_instance, published, "v1")
    user = repo.user_by_username(db, "admin")
    token = repo.open_session(db, user)
    row = db.get(m.AuthSession, token_digest(token))
    row.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    assert repo.user_for_token(db, token) is None
    assert db.get(m.AuthSession, token_digest(token)) is None


def test_platform_postgres_urls_get_a_driver(monkeypatch):
    monkeypatch.delenv("CHRONOSOLVE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.example:5432/cs")
    assert database_url() == "postgresql+psycopg://u:p@db.example:5432/cs"
    monkeypatch.setenv("CHRONOSOLVE_DATABASE_URL", "postgresql://a:b@h/x")
    assert database_url() == "postgresql+psycopg://a:b@h/x"


def test_without_configuration_the_demo_uses_local_sqlite(monkeypatch):
    monkeypatch.delenv("CHRONOSOLVE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url().startswith("sqlite:///")
    assert database_url().endswith("data/chronosolve.db")


def test_restoring_the_rehearsed_baseline_keeps_history(db, small_instance, published):
    seed_if_empty(db, FIXTURE)
    other = repo.add_version(
        db,
        instance=repo.load_instance(db),
        timetable=repo.version_timetable(db, repo.current_version(db)),
        status=repo.PROPOSED,
        label="something else",
        created_by="t",
        reason="r",
        source=repo.current_version(db),
    )
    repo.publish(db, other)
    db.commit()

    restored = restore_rehearsed(db, "admin")
    _, fixture_tt, _ = load_snapshot(FIXTURE)
    assert restored.number == 3
    assert restored.label == "published-v1 (restored)"
    assert repo.version_timetable(db, restored).placements == fixture_tt.placements
    assert [v.status for v in repo.list_versions(db)] == ["PUBLISHED", "SUPERSEDED", "SUPERSEDED"]
