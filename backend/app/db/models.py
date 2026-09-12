"""Relational schema.

The solver never sees these classes. `repository` converts rows to the plain
domain `Instance` and back, so CP-SAT stays testable without a database and the
database stays replaceable without touching the solver.

Column types are portable: the same schema runs on PostgreSQL in deployment
and on SQLite for tests and zero-setup local runs. Timestamps are naive UTC so
both engines round-trip them identically.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Academic structure: institution -> department -> programme -> year ->
# semester -> division. Divisions of every year share one resource pool.
# --------------------------------------------------------------------------


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class Program(Base):
    __tablename__ = "programs"
    __table_args__ = (UniqueConstraint("department_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(160))


class AcademicYear(Base):
    __tablename__ = "academic_years"
    __table_args__ = (UniqueConstraint("program_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(60))


class Semester(Base):
    __tablename__ = "semesters"
    __table_args__ = (UniqueConstraint("year_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year_id: Mapped[int] = mapped_column(
        ForeignKey("academic_years.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(60))


class BatchRow(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    semester_id: Mapped[int | None] = mapped_column(
        ForeignKey("semesters.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120))
    strength: Mapped[int] = mapped_column(Integer)


# --------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------


class FacultyRow(Base):
    __tablename__ = "faculty"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(120))
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    max_daily_load: Mapped[int] = mapped_column(Integer, default=5)
    max_weekly_load: Mapped[int] = mapped_column(Integer, default=18)
    max_consecutive: Mapped[int] = mapped_column(Integer, default=3)
    subjects: Mapped[list] = mapped_column(JSON, default=list)


class FacultySlot(Base):
    """Regular availability -- a base constraint held on the teacher.

    kind UNAVAILABLE is hard; PREFERRED_OFF is a soft preference.
    """

    __tablename__ = "faculty_slots"

    faculty_id: Mapped[str] = mapped_column(
        ForeignKey("faculty.id", ondelete="CASCADE"), primary_key=True
    )
    timeslot_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)


class RoomRow(Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(120))
    building: Mapped[str] = mapped_column(String(80), default="")
    room_type: Mapped[str] = mapped_column(String(16))
    capacity: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class RoomCapability(Base):
    __tablename__ = "room_capabilities"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(60), primary_key=True)


class RoomBlock(Base):
    """Regular room unavailability (a base constraint)."""

    __tablename__ = "room_blocks"

    room_id: Mapped[str] = mapped_column(
        ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    timeslot_id: Mapped[int] = mapped_column(Integer, primary_key=True)


class BatchBlock(Base):
    """Regular division unavailability (a base constraint)."""

    __tablename__ = "batch_blocks"

    batch_id: Mapped[str] = mapped_column(
        ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True
    )
    timeslot_id: Mapped[int] = mapped_column(Integer, primary_key=True)


# --------------------------------------------------------------------------
# Teaching requirements
# --------------------------------------------------------------------------


class SubjectRow(Base):
    """A subject as a coordinator configures it; sessions are generated from it."""

    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("batch_id", "code", "category"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(160))
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"))
    category: Mapped[str] = mapped_column(String(16))  # THEORY | TUTORIAL | LAB
    sessions_per_week: Mapped[int] = mapped_column(Integer)
    duration: Mapped[int] = mapped_column(Integer, default=1)
    room_type: Mapped[str] = mapped_column(String(16))
    required_capability: Mapped[str | None] = mapped_column(String(60), nullable=True)
    elective_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SessionRow(Base):
    """One weekly meeting -- the unit the solver places."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), nullable=True
    )
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    faculty_id: Mapped[str] = mapped_column(ForeignKey("faculty.id"))
    subject_code: Mapped[str] = mapped_column(String(20))
    subject_name: Mapped[str] = mapped_column(String(160))
    duration: Mapped[int] = mapped_column(Integer, default=1)
    room_type: Mapped[str] = mapped_column(String(16))
    elective_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_capability: Mapped[str | None] = mapped_column(String(60), nullable=True)
    category: Mapped[str] = mapped_column(String(16), default="THEORY")


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


class ConstraintRow(Base):
    """A base rule or a dated temporary override (see services.constraints)."""

    __tablename__ = "constraint_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(12))  # BASE | TEMPORARY
    kind: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    days: Mapped[list] = mapped_column(JSON, default=list)
    start_time: Mapped[str] = mapped_column(String(5), default="09:00")
    end_time: Mapped[str] = mapped_column(String(5), default="23:59")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rule_field: Mapped[str | None] = mapped_column(String(40), nullable=True)
    rule_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lock_timeslot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lock_room: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ACTIVE")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(12))  # ADMIN | FACULTY | STUDENT
    password_hash: Mapped[str] = mapped_column(String(255))
    faculty_id: Mapped[str | None] = mapped_column(
        ForeignKey("faculty.id", ondelete="SET NULL"), nullable=True
    )
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("batches.id", ondelete="SET NULL"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    """A signed-in session. Only a SHA-256 digest of the token is stored."""

    __tablename__ = "auth_sessions"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


# --------------------------------------------------------------------------
# Timetables and their versions
# --------------------------------------------------------------------------


class TimetableRow(Base):
    __tablename__ = "timetables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    term: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class VersionRow(Base):
    """One revision of a timetable: published, proposed or superseded.

    Every version keeps the exact instance it was solved against, so any past
    version can be re-validated, re-exported or restored on its own terms.
    """

    __tablename__ = "timetable_versions"
    __table_args__ = (UniqueConstraint("timetable_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timetable_id: Mapped[int] = mapped_column(
        ForeignKey("timetables.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16))
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(80), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    solver_status: Mapped[str] = mapped_column(String(16))
    objective: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_bound: Mapped[float | None] = mapped_column(Float, nullable=True)
    solve_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    source_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("timetable_versions.id", ondelete="SET NULL"), nullable=True
    )
    changed_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unchanged_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    instance_json: Mapped[str] = mapped_column(Text)
    # Rules a proposal assumed; they become constraint records only if the
    # proposal is approved.
    pending_rules_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlacementRow(Base):
    __tablename__ = "scheduled_sessions"

    version_id: Mapped[int] = mapped_column(
        ForeignKey("timetable_versions.id", ondelete="CASCADE"), primary_key=True
    )
    session_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    timeslot_id: Mapped[int] = mapped_column(Integer)
    room_id: Mapped[str] = mapped_column(String(40))


# --------------------------------------------------------------------------
# Operational requests
# --------------------------------------------------------------------------


class RequestRow(Base):
    """A teacher's temporary-unavailability request and its repair options.

    `faculty_id` is a plain reference rather than a foreign key so that the
    request history survives a department being re-imported.
    """

    __tablename__ = "schedule_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    faculty_id: Mapped[str] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    base_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    constraint_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affected_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    options_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSON, nullable=True)
