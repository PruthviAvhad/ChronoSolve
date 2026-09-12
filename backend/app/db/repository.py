"""The only module that speaks SQL.

Rows become the solver's plain `Instance` and `Timetable` on the way in;
solver results become rows on the way out. Nothing above this layer writes a
query and nothing below it -- the CP-SAT engine -- knows a database exists,
which is what keeps the solver testable on its own.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import date

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session as DB

from ..data.store import instance_from_dict, instance_to_dict
from ..domain.models import (
    Batch,
    Calendar,
    Faculty,
    Instance,
    Placement,
    Room,
    RoomType,
    Session,
    Timetable,
)
from ..services.auth import (
    SESSION_LIFETIME,
    hash_password,
    new_token,
    token_digest,
    verify_password,
)
from ..services.constraints import ConstraintRecord
from ..solver.metrics import ScheduleDiff
from ..solver.model import ObjectiveWeights
from . import models as m
from .models import utcnow

# Version lifecycle.
PUBLISHED = "PUBLISHED"
PROPOSED = "PROPOSED"
SUPERSEDED = "SUPERSEDED"
DISCARDED = "DISCARDED"
STALE = "STALE"

# Faculty slot kinds.
UNAVAILABLE = "UNAVAILABLE"
PREFERRED_OFF = "PREFERRED_OFF"

DEFAULT_TIMETABLE = ("Computer Engineering timetable", "Odd term 2026-27")
WEIGHT_FIELDS = tuple(ObjectiveWeights.__dataclass_fields__)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def get_setting(db: DB, key: str, default=None):
    row = db.get(m.SettingRow, key)
    return row.value if row is not None else default


def set_setting(db: DB, key: str, value) -> None:
    row = db.get(m.SettingRow, key)
    if row is None:
        db.add(m.SettingRow(key=key, value=value))
    else:
        row.value = value


def get_weights(db: DB) -> ObjectiveWeights:
    stored = get_setting(db, "objective_weights") or {}
    return ObjectiveWeights(**{k: v for k, v in stored.items() if k in WEIGHT_FIELDS})


def set_weights(db: DB, weights: ObjectiveWeights) -> None:
    set_setting(db, "objective_weights", asdict(weights))


# --------------------------------------------------------------------------
# Academic structure
# --------------------------------------------------------------------------


def _get_or_create(db: DB, model, defaults: dict | None = None, **keys):
    row = db.scalars(select(model).filter_by(**keys)).first()
    if row is None:
        row = model(**keys, **(defaults or {}))
        db.add(row)
        db.flush()
    return row


def department_id(db: DB, name: str) -> int | None:
    if not name:
        return None
    return _get_or_create(db, m.Department, name=name).id


def ensure_semester(db: DB, batch: Batch) -> int | None:
    """The semester row for a division, creating its ancestors as needed."""
    if not batch.department or not batch.year:
        return None
    dept = _get_or_create(db, m.Department, name=batch.department)
    prog = _get_or_create(
        db, m.Program, department_id=dept.id, name=batch.program or batch.department
    )
    year = _get_or_create(
        db,
        m.AcademicYear,
        defaults={"label": batch.year_label or f"Year {batch.year}"},
        program_id=prog.id,
        number=batch.year,
    )
    number = batch.semester or (2 * batch.year - 1)
    sem = _get_or_create(
        db,
        m.Semester,
        defaults={"label": f"Semester {number}"},
        year_id=year.id,
        number=number,
    )
    return sem.id


def _hierarchy(db: DB) -> dict[int, tuple[str, str, int, str, int]]:
    """semester id -> (department, programme, year number, year label, semester)."""
    rows = db.execute(
        select(
            m.Semester.id,
            m.Semester.number,
            m.AcademicYear.number,
            m.AcademicYear.label,
            m.Program.name,
            m.Department.name,
        )
        .join(m.AcademicYear, m.Semester.year_id == m.AcademicYear.id)
        .join(m.Program, m.AcademicYear.program_id == m.Program.id)
        .join(m.Department, m.Program.department_id == m.Department.id)
    ).all()
    return {
        sem_id: (dept, prog, year_no, year_label, sem_no)
        for sem_id, sem_no, year_no, year_label, prog, dept in rows
    }


# --------------------------------------------------------------------------
# Configuration <-> Instance
# --------------------------------------------------------------------------


def load_instance(db: DB) -> Instance:
    """The institution's base configuration as a solver instance.

    No locks and no overrides: those are constraint records, compiled on top
    by `services.constraints.compile_instance` for a particular day.
    """
    cal_cfg = get_setting(db, "calendar") or {"days": 5, "periods": 8}
    calendar = Calendar(days=cal_cfg["days"], periods=cal_cfg["periods"])
    hierarchy = _hierarchy(db)
    departments = {d.id: d.name for d in db.scalars(select(m.Department))}

    capabilities: dict[str, set[str]] = defaultdict(set)
    for row in db.scalars(select(m.RoomCapability)):
        capabilities[row.room_id].add(row.capability)
    room_blocks: dict[str, set[int]] = defaultdict(set)
    for row in db.scalars(select(m.RoomBlock)):
        room_blocks[row.room_id].add(row.timeslot_id)
    batch_blocks: dict[str, set[int]] = defaultdict(set)
    for row in db.scalars(select(m.BatchBlock)):
        batch_blocks[row.batch_id].add(row.timeslot_id)
    slots: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for row in db.scalars(select(m.FacultySlot)):
        slots[row.faculty_id][row.kind].add(row.timeslot_id)

    rooms = [
        Room(
            id=r.id,
            name=r.name,
            capacity=r.capacity,
            room_type=RoomType(r.room_type),
            unavailable=frozenset(room_blocks[r.id]),
            building=r.building or "",
            capabilities=frozenset(capabilities[r.id]),
            active=bool(r.active),
        )
        for r in db.scalars(select(m.RoomRow).order_by(m.RoomRow.position, m.RoomRow.id))
    ]
    faculty = [
        Faculty(
            id=f.id,
            name=f.name,
            unavailable=frozenset(slots[f.id][UNAVAILABLE]),
            max_daily_load=f.max_daily_load,
            max_weekly_load=f.max_weekly_load,
            max_consecutive=f.max_consecutive,
            department=departments.get(f.department_id, ""),
            preferred_off=frozenset(slots[f.id][PREFERRED_OFF]),
            subjects=tuple(f.subjects or ()),
        )
        for f in db.scalars(
            select(m.FacultyRow).order_by(m.FacultyRow.position, m.FacultyRow.id)
        )
    ]
    batches = []
    for b in db.scalars(select(m.BatchRow).order_by(m.BatchRow.position, m.BatchRow.id)):
        dept, prog, year_no, year_label, sem_no = hierarchy.get(
            b.semester_id, ("", "", 0, "", 0)
        )
        batches.append(
            Batch(
                id=b.id,
                name=b.name,
                strength=b.strength,
                unavailable=frozenset(batch_blocks[b.id]),
                department=dept,
                program=prog,
                year=year_no,
                year_label=year_label,
                semester=sem_no,
            )
        )
    sessions = [
        Session(
            id=s.id,
            subject_code=s.subject_code,
            subject_name=s.subject_name,
            batch_id=s.batch_id,
            faculty_id=s.faculty_id,
            duration=s.duration,
            room_type=RoomType(s.room_type),
            elective_group=s.elective_group,
            headcount=s.headcount,
            required_capability=s.required_capability,
            category=s.category or "",
        )
        for s in db.scalars(
            select(m.SessionRow).order_by(m.SessionRow.position, m.SessionRow.id)
        )
    ]
    return Instance(
        name=get_setting(db, "institution_name", "ChronoSolve"),
        calendar=calendar,
        rooms=rooms,
        faculty=faculty,
        batches=batches,
        sessions=sessions,
    )


def _elective_key(session: Session) -> str | None:
    """"SE-A-ELECTIVE-1-2" -> "ELECTIVE-1": the group, minus division and week slot."""
    group = session.elective_group
    if not group:
        return None
    head, _, tail = group.rpartition("-")
    base = head if tail.isdigit() and head else group
    prefix = f"{session.batch_id}-"
    return base[len(prefix) :] if base.startswith(prefix) else base


def derive_subjects(instance: Instance) -> list[tuple[dict, list[Session]]]:
    """Group sessions back into the subjects that generate them."""
    groups: dict[tuple[str, str, str], list[Session]] = {}
    for s in instance.sessions:
        groups.setdefault((s.batch_id, s.subject_code, s.kind), []).append(s)
    out = []
    for (batch_id, code, kind), members in groups.items():
        first = members[0]
        out.append(
            (
                dict(
                    batch_id=batch_id,
                    code=code,
                    name=first.subject_name,
                    faculty_id=first.faculty_id,
                    category=kind,
                    sessions_per_week=len(members),
                    duration=first.duration,
                    room_type=first.room_type.value,
                    required_capability=first.required_capability,
                    elective_key=_elective_key(first),
                    headcount=first.headcount,
                ),
                members,
            )
        )
    return out


def session_ids_for(subject: m.SubjectRow) -> list[tuple[str, str | None]]:
    """(session id, elective group) for every weekly meeting of a subject.

    The id scheme matches the one the data was generated with, so editing a
    subject keeps every surviving session's id -- and with it, its published
    placement.
    """
    out = []
    for k in range(1, subject.sessions_per_week + 1):
        suffix = f"T{k}" if subject.category == "TUTORIAL" else str(k)
        group = (
            f"{subject.batch_id}-{subject.elective_key}-{k}"
            if subject.elective_key
            else None
        )
        out.append((f"{subject.batch_id}-{subject.code}-{suffix}", group))
    return out


def regenerate_sessions(db: DB, subject: m.SubjectRow) -> None:
    """Rebuild a subject's sessions after it was edited."""
    db.execute(delete(m.SessionRow).where(m.SessionRow.subject_id == subject.id))
    base = db.scalar(select(func.max(m.SessionRow.position))) or 0
    for offset, (sid, group) in enumerate(session_ids_for(subject), start=1):
        db.add(
            m.SessionRow(
                id=sid,
                position=base + offset,
                subject_id=subject.id,
                batch_id=subject.batch_id,
                faculty_id=subject.faculty_id,
                subject_code=subject.code,
                subject_name=subject.name,
                duration=subject.duration,
                room_type=subject.room_type,
                elective_group=group,
                headcount=subject.headcount,
                required_capability=subject.required_capability,
                category=subject.category,
            )
        )
    db.flush()


def replace_config(db: DB, instance: Instance) -> None:
    """Make the stored configuration exactly `instance`.

    Rooms, teachers and divisions are updated in place where their ids
    survive, so accounts linked to them stay linked; subjects and sessions are
    rebuilt from the instance's sessions.
    """
    set_setting(db, "institution_name", instance.name)
    set_setting(
        db, "calendar", {"days": instance.calendar.days, "periods": instance.calendar.periods}
    )

    db.execute(delete(m.SessionRow))
    db.execute(delete(m.SubjectRow))
    for model in (m.FacultySlot, m.RoomCapability, m.RoomBlock, m.BatchBlock):
        db.execute(delete(model))
    db.execute(delete(m.RoomRow).where(m.RoomRow.id.not_in([r.id for r in instance.rooms])))
    db.execute(
        delete(m.FacultyRow).where(m.FacultyRow.id.not_in([f.id for f in instance.faculty]))
    )
    db.execute(
        delete(m.BatchRow).where(m.BatchRow.id.not_in([b.id for b in instance.batches]))
    )
    db.flush()

    for pos, r in enumerate(instance.rooms):
        row = db.get(m.RoomRow, r.id) or m.RoomRow(id=r.id)
        row.position, row.name, row.building = pos, r.name, r.building
        row.room_type, row.capacity, row.active = r.room_type.value, r.capacity, r.active
        db.add(row)
    for pos, f in enumerate(instance.faculty):
        row = db.get(m.FacultyRow, f.id) or m.FacultyRow(id=f.id)
        row.position, row.name = pos, f.name
        row.department_id = department_id(db, f.department)
        row.max_daily_load, row.max_weekly_load = f.max_daily_load, f.max_weekly_load
        row.max_consecutive, row.subjects = f.max_consecutive, list(f.subjects)
        db.add(row)
    for pos, b in enumerate(instance.batches):
        row = db.get(m.BatchRow, b.id) or m.BatchRow(id=b.id)
        row.position, row.name, row.strength = pos, b.name, b.strength
        row.semester_id = ensure_semester(db, b)
        db.add(row)
    db.flush()

    for r in instance.rooms:
        db.add_all(m.RoomCapability(room_id=r.id, capability=c) for c in sorted(r.capabilities))
        db.add_all(m.RoomBlock(room_id=r.id, timeslot_id=t) for t in sorted(r.unavailable))
    for f in instance.faculty:
        db.add_all(
            m.FacultySlot(faculty_id=f.id, timeslot_id=t, kind=UNAVAILABLE)
            for t in sorted(f.unavailable)
        )
        db.add_all(
            m.FacultySlot(faculty_id=f.id, timeslot_id=t, kind=PREFERRED_OFF)
            for t in sorted(f.preferred_off)
        )
    for b in instance.batches:
        db.add_all(m.BatchBlock(batch_id=b.id, timeslot_id=t) for t in sorted(b.unavailable))
    db.flush()

    position = {s.id: i for i, s in enumerate(instance.sessions)}
    for spec, members in derive_subjects(instance):
        subject = m.SubjectRow(**spec)
        db.add(subject)
        db.flush()
        db.add_all(
            m.SessionRow(
                id=s.id,
                position=position[s.id],
                subject_id=subject.id,
                batch_id=s.batch_id,
                faculty_id=s.faculty_id,
                subject_code=s.subject_code,
                subject_name=s.subject_name,
                duration=s.duration,
                room_type=s.room_type.value,
                elective_group=s.elective_group,
                headcount=s.headcount,
                required_capability=s.required_capability,
                category=s.kind,
            )
            for s in members
        )
    db.flush()


# --------------------------------------------------------------------------
# Constraint records
# --------------------------------------------------------------------------


def record_from_row(row: m.ConstraintRow) -> ConstraintRecord:
    return ConstraintRecord(
        category=row.category,
        kind=row.kind,
        target_id=row.target_id,
        days=tuple(row.days or ()),
        start_time=row.start_time,
        end_time=row.end_time,
        start_date=row.start_date,
        end_date=row.end_date,
        rule_field=row.rule_field,
        rule_value=row.rule_value,
        session_id=row.session_id,
        lock_timeslot=row.lock_timeslot,
        lock_room=row.lock_room,
        status=row.status,
        reason=row.reason or "",
        created_by=row.created_by or "",
        created_at=row.created_at,
        request_id=row.request_id,
        id=row.id,
    )


def add_record(db: DB, rec: ConstraintRecord) -> m.ConstraintRow:
    row = m.ConstraintRow(
        category=rec.category,
        kind=rec.kind,
        target_id=rec.target_id,
        days=list(rec.days),
        start_time=rec.start_time,
        end_time=rec.end_time,
        start_date=rec.start_date,
        end_date=rec.end_date,
        rule_field=rec.rule_field,
        rule_value=rec.rule_value,
        session_id=rec.session_id,
        lock_timeslot=rec.lock_timeslot,
        lock_room=rec.lock_room,
        status=rec.status,
        reason=rec.reason,
        created_by=rec.created_by,
        created_at=rec.created_at or utcnow(),
        request_id=rec.request_id,
    )
    db.add(row)
    db.flush()
    return row


def record_rows(db: DB) -> list[m.ConstraintRow]:
    return list(db.scalars(select(m.ConstraintRow).order_by(m.ConstraintRow.id)))


def records(db: DB) -> list[ConstraintRecord]:
    return [record_from_row(r) for r in record_rows(db)]


def set_records_status(db: DB, ids: list[int], status: str) -> None:
    if ids:
        db.execute(
            update(m.ConstraintRow).where(m.ConstraintRow.id.in_(ids)).values(status=status)
        )


def record_to_json(rec: ConstraintRecord) -> dict:
    return {
        "category": rec.category,
        "kind": rec.kind,
        "target_id": rec.target_id,
        "days": list(rec.days),
        "start_time": rec.start_time,
        "end_time": rec.end_time,
        "start_date": rec.start_date.isoformat() if rec.start_date else None,
        "end_date": rec.end_date.isoformat() if rec.end_date else None,
        "rule_field": rec.rule_field,
        "rule_value": rec.rule_value,
        "session_id": rec.session_id,
        "lock_timeslot": rec.lock_timeslot,
        "lock_room": rec.lock_room,
        "status": rec.status,
        "reason": rec.reason,
    }


def record_from_json(d: dict) -> ConstraintRecord:
    return ConstraintRecord(
        category=d["category"],
        kind=d["kind"],
        target_id=d.get("target_id"),
        days=tuple(d.get("days") or ()),
        start_time=d.get("start_time", "09:00"),
        end_time=d.get("end_time", "23:59"),
        start_date=date.fromisoformat(d["start_date"]) if d.get("start_date") else None,
        end_date=date.fromisoformat(d["end_date"]) if d.get("end_date") else None,
        rule_field=d.get("rule_field"),
        rule_value=d.get("rule_value"),
        session_id=d.get("session_id"),
        lock_timeslot=d.get("lock_timeslot"),
        lock_room=d.get("lock_room"),
        status=d.get("status", "ACTIVE"),
        reason=d.get("reason", ""),
    )


# --------------------------------------------------------------------------
# Timetable versions
# --------------------------------------------------------------------------


def ensure_timetable(db: DB) -> m.TimetableRow:
    row = db.scalars(select(m.TimetableRow).order_by(m.TimetableRow.id)).first()
    if row is None:
        name, term = DEFAULT_TIMETABLE
        row = m.TimetableRow(name=name, term=term, created_at=utcnow())
        db.add(row)
        db.flush()
    return row


def add_version(
    db: DB,
    *,
    instance: Instance,
    timetable: Timetable,
    status: str,
    label: str,
    created_by: str,
    reason: str,
    source: m.VersionRow | None = None,
    diff: ScheduleDiff | None = None,
    request_id: int | None = None,
    effective_until: date | None = None,
    pending_rules: list[ConstraintRecord] | None = None,
) -> m.VersionRow:
    timetable_row = ensure_timetable(db)
    number = (
        db.scalar(
            select(func.max(m.VersionRow.number)).where(
                m.VersionRow.timetable_id == timetable_row.id
            )
        )
        or 0
    ) + 1
    row = m.VersionRow(
        timetable_id=timetable_row.id,
        number=number,
        label=label,
        status=status,
        is_current=False,
        created_at=utcnow(),
        created_by=created_by,
        reason=reason,
        solver_status=timetable.status,
        objective=timetable.objective,
        best_bound=timetable.best_bound,
        solve_seconds=timetable.solve_seconds,
        source_version_id=source.id if source is not None else None,
        changed_count=diff.changed if diff is not None else None,
        unchanged_count=diff.unchanged if diff is not None else None,
        retention_pct=round(diff.retention_pct, 2) if diff is not None else None,
        request_id=request_id,
        effective_until=effective_until,
        instance_json=json.dumps(instance_to_dict(instance)),
        pending_rules_json=(
            json.dumps([record_to_json(r) for r in pending_rules])
            if pending_rules
            else None
        ),
    )
    db.add(row)
    db.flush()
    db.add_all(
        m.PlacementRow(
            version_id=row.id,
            session_id=p.session_id,
            timeslot_id=p.timeslot_id,
            room_id=p.room_id,
        )
        for p in timetable.placements.values()
    )
    db.flush()
    return row


def publish(db: DB, version: m.VersionRow) -> None:
    """Make `version` the one current published version."""
    for current in db.scalars(
        select(m.VersionRow).where(
            m.VersionRow.is_current.is_(True), m.VersionRow.id != version.id
        )
    ):
        current.is_current = False
        if current.status == PUBLISHED:
            current.status = SUPERSEDED
    version.status = PUBLISHED
    version.is_current = True
    db.flush()


def current_version(db: DB) -> m.VersionRow | None:
    return db.scalars(
        select(m.VersionRow).where(m.VersionRow.is_current.is_(True))
    ).first()


def open_proposal(db: DB) -> m.VersionRow | None:
    return db.scalars(
        select(m.VersionRow)
        .where(m.VersionRow.status == PROPOSED)
        .order_by(m.VersionRow.id.desc())
    ).first()


def discard_open_proposals(db: DB) -> None:
    db.execute(
        update(m.VersionRow)
        .where(m.VersionRow.status == PROPOSED)
        .values(status=DISCARDED)
    )


def version_by_id(db: DB, version_id: int) -> m.VersionRow | None:
    return db.get(m.VersionRow, version_id)


def list_versions(db: DB, limit: int = 50) -> list[m.VersionRow]:
    return list(
        db.scalars(select(m.VersionRow).order_by(m.VersionRow.id.desc()).limit(limit))
    )


def version_timetable(db: DB, version: m.VersionRow) -> Timetable:
    placements = {
        p.session_id: Placement(
            session_id=p.session_id, timeslot_id=p.timeslot_id, room_id=p.room_id
        )
        for p in db.scalars(
            select(m.PlacementRow).where(m.PlacementRow.version_id == version.id)
        )
    }
    return Timetable(
        placements=placements,
        status=version.solver_status,
        objective=version.objective,
        best_bound=version.best_bound,
        solve_seconds=version.solve_seconds or 0.0,
    )


def version_instance(version: m.VersionRow) -> Instance:
    return instance_from_dict(json.loads(version.instance_json))


def version_pending_rules(version: m.VersionRow) -> list[ConstraintRecord]:
    if not version.pending_rules_json:
        return []
    return [record_from_json(d) for d in json.loads(version.pending_rules_json)]


# --------------------------------------------------------------------------
# Users and sign-in sessions
# --------------------------------------------------------------------------


def create_user(
    db: DB,
    *,
    username: str,
    password: str,
    role: str,
    display_name: str,
    faculty_id: str | None = None,
    batch_id: str | None = None,
) -> m.UserRow:
    row = m.UserRow(
        username=username.lower(),
        display_name=display_name,
        role=role,
        password_hash=hash_password(password),
        faculty_id=faculty_id,
        batch_id=batch_id,
        active=True,
        created_at=utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def user_by_username(db: DB, username: str) -> m.UserRow | None:
    return db.scalars(
        select(m.UserRow).where(m.UserRow.username == username.lower())
    ).first()


def authenticate(db: DB, username: str, password: str) -> m.UserRow | None:
    user = user_by_username(db, username)
    if user is None or not user.active:
        return None
    return user if verify_password(password, user.password_hash) else None


def open_session(db: DB, user: m.UserRow) -> str:
    """Start a session and return its token. Only the token's digest is stored."""
    token = new_token()
    now = utcnow()
    db.add(
        m.AuthSession(
            token_digest=token_digest(token),
            user_id=user.id,
            created_at=now,
            expires_at=now + SESSION_LIFETIME,
        )
    )
    db.flush()
    return token


def user_for_token(db: DB, token: str) -> m.UserRow | None:
    session = db.get(m.AuthSession, token_digest(token))
    if session is None:
        return None
    if session.expires_at < utcnow():
        db.delete(session)
        db.flush()
        return None
    user = db.get(m.UserRow, session.user_id)
    return user if user is not None and user.active else None


def close_session(db: DB, token: str) -> None:
    session = db.get(m.AuthSession, token_digest(token))
    if session is not None:
        db.delete(session)
        db.flush()


def list_users(db: DB) -> list[m.UserRow]:
    return list(db.scalars(select(m.UserRow).order_by(m.UserRow.role, m.UserRow.username)))


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------


def add_request(db: DB, **fields) -> m.RequestRow:
    row = m.RequestRow(created_at=utcnow(), **fields)
    db.add(row)
    db.flush()
    return row


def request_by_id(db: DB, request_id: int) -> m.RequestRow | None:
    return db.get(m.RequestRow, request_id)


def list_requests(
    db: DB, *, faculty_id: str | None = None, statuses: tuple[str, ...] | None = None
) -> list[m.RequestRow]:
    query = select(m.RequestRow).order_by(m.RequestRow.id.desc())
    if faculty_id is not None:
        query = query.where(m.RequestRow.faculty_id == faculty_id)
    if statuses:
        query = query.where(m.RequestRow.status.in_(statuses))
    return list(db.scalars(query))


# --------------------------------------------------------------------------
# Configuration edits
#
# Each edits the base configuration only. The published timetable is left
# alone: the state view reports any rule it now breaks, and a coordinator
# repairs it with minimum disruption when ready.
# --------------------------------------------------------------------------


def _set_fields(row, fields: dict) -> None:
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)


def update_faculty(db: DB, faculty_id: str, **fields) -> m.FacultyRow:
    row = db.get(m.FacultyRow, faculty_id)
    if row is None:
        raise KeyError(f"No faculty {faculty_id!r}")
    _set_fields(row, fields)
    db.flush()
    return row


def set_faculty_slots(db: DB, faculty_id: str, kind: str, slots: list[int]) -> None:
    db.execute(
        delete(m.FacultySlot).where(
            m.FacultySlot.faculty_id == faculty_id, m.FacultySlot.kind == kind
        )
    )
    db.add_all(
        m.FacultySlot(faculty_id=faculty_id, timeslot_id=t, kind=kind)
        for t in sorted(set(slots))
    )
    db.flush()


def update_room(
    db: DB, room_id: str, *, capabilities: list[str] | None = None, **fields
) -> m.RoomRow:
    row = db.get(m.RoomRow, room_id)
    if row is None:
        raise KeyError(f"No room {room_id!r}")
    _set_fields(row, fields)
    if capabilities is not None:
        db.execute(delete(m.RoomCapability).where(m.RoomCapability.room_id == room_id))
        db.add_all(
            m.RoomCapability(room_id=room_id, capability=c)
            for c in sorted({c.strip().lower() for c in capabilities if c.strip()})
        )
    db.flush()
    return row


def set_room_blocks(db: DB, room_id: str, slots: list[int]) -> None:
    db.execute(delete(m.RoomBlock).where(m.RoomBlock.room_id == room_id))
    db.add_all(m.RoomBlock(room_id=room_id, timeslot_id=t) for t in sorted(set(slots)))
    db.flush()


def update_batch(db: DB, batch_id: str, **fields) -> m.BatchRow:
    row = db.get(m.BatchRow, batch_id)
    if row is None:
        raise KeyError(f"No division {batch_id!r}")
    _set_fields(row, fields)
    db.flush()
    return row


def add_batch(db: DB, batch: Batch) -> m.BatchRow:
    if db.get(m.BatchRow, batch.id) is not None:
        raise ValueError(f"Division {batch.id} already exists")
    position = (db.scalar(select(func.max(m.BatchRow.position))) or 0) + 1
    row = m.BatchRow(
        id=batch.id,
        position=position,
        name=batch.name,
        strength=batch.strength,
        semester_id=ensure_semester(db, batch),
    )
    db.add(row)
    db.flush()
    return row


def subjects(db: DB, batch_id: str | None = None) -> list[m.SubjectRow]:
    query = select(m.SubjectRow).order_by(m.SubjectRow.batch_id, m.SubjectRow.code)
    if batch_id is not None:
        query = query.where(m.SubjectRow.batch_id == batch_id)
    return list(db.scalars(query))


def subject_by_id(db: DB, subject_id: int) -> m.SubjectRow | None:
    return db.get(m.SubjectRow, subject_id)


def _check_ids_free(db: DB, subject: m.SubjectRow) -> None:
    for sid, _ in session_ids_for(subject):
        existing = db.get(m.SessionRow, sid)
        if existing is not None and existing.subject_id != subject.id:
            raise ValueError(
                f"Session id {sid} is already used by another subject of {subject.batch_id}"
            )


def add_subject(db: DB, **fields) -> m.SubjectRow:
    row = m.SubjectRow(**fields)
    db.add(row)
    db.flush()
    _check_ids_free(db, row)
    regenerate_sessions(db, row)
    return row


def update_subject(db: DB, subject_id: int, **fields) -> m.SubjectRow:
    row = db.get(m.SubjectRow, subject_id)
    if row is None:
        raise KeyError(f"No subject {subject_id}")
    _set_fields(row, fields)
    if "required_capability" in fields and fields["required_capability"] == "":
        row.required_capability = None
    db.flush()
    _check_ids_free(db, row)
    regenerate_sessions(db, row)
    return row


def delete_subject(db: DB, subject_id: int) -> None:
    row = db.get(m.SubjectRow, subject_id)
    if row is None:
        raise KeyError(f"No subject {subject_id}")
    db.execute(delete(m.SessionRow).where(m.SessionRow.subject_id == subject_id))
    db.delete(row)
    db.flush()


def users_by_faculty(db: DB) -> dict[str, m.UserRow]:
    return {
        u.faculty_id: u
        for u in db.scalars(select(m.UserRow).where(m.UserRow.faculty_id.is_not(None)))
    }
