"""Coordinator administration: configuration, rules, locks, moves, versions,
analytics and recovery.

Edits here change the *base configuration*. They never rewrite the published
timetable: the state view reports any rule it now breaks, and the coordinator
repairs it with minimum disruption and approves the result.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session as DB

from ..db import models as m
from ..db import repository as repo
from ..db.seed import restore_rehearsed
from ..deps import (
    ADMIN_ONLY,
    DATABASE,
    SSE_HEADERS,
    STAFF,
    current_user,
    get_db,
    service_errors,
    stream_solve,
)
from ..domain.models import Batch, RoomType
from ..progress import ProgressLog
from ..schemas import (
    AvailabilityIn,
    BatchIn,
    BatchOut,
    BatchPatch,
    BlockerOut,
    BlocksIn,
    ConstraintIn,
    ConstraintOut,
    ConstraintPatch,
    FacultyOut,
    FacultyPatch,
    LockIn,
    MoveCheckOut,
    MoveIn,
    RoomOut,
    RoomPatch,
    SubjectIn,
    SubjectOut,
    SubjectPatch,
    VersionOut,
    WeightsIn,
    WeightsOut,
    WhatIfOut,
    constraint_out,
    repair_out,
    version_out,
    weights_out,
)
from ..services import scheduling
from ..services.constraints import ACTIVE, LOCK, TEMPORARY, WEEKDAYS, ConstraintRecord
from ..solver.metrics import faculty_workload, room_usage, schedule_metrics
from ..solver.moves import check_move
from ..solver.validate import validate
from .requests import request_out

router = APIRouter(prefix="/api", tags=["admin"])


def _ctx(db: DB) -> scheduling.Context:
    with service_errors():
        return scheduling.load_context(db)


def _slots(ctx: scheduling.Context, slots: list[int]) -> list[int]:
    valid = set(ctx.base.calendar.by_id)
    bad = sorted(set(slots) - valid)
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown timeslot(s): {bad}")
    return sorted(set(slots))


# --------------------------------------------------------------------------
# Dashboard and structure
# --------------------------------------------------------------------------


@router.get("/admin/dashboard")
def dashboard(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> dict:
    """The coordinator's overview. Every figure is computed now, from the
    published version and the rules in force today."""
    ctx = _ctx(db)
    report = validate(ctx.instance, ctx.published)
    quality = schedule_metrics(ctx.instance, ctx.published)
    pending = repo.list_requests(db, statuses=(scheduling.SUBMITTED,))
    overrides = [
        constraint_out(r, ctx.instance, ctx.today).model_dump()
        for r in ctx.records
        if r.category == TEMPORARY
        and r.status == ACTIVE
        and r.lifecycle(ctx.today) != "EXPIRED"
    ]
    proposal = scheduling.open_proposal(db, ctx)
    return {
        "today": ctx.today.isoformat(),
        "version": version_out(ctx.version).model_dump(),
        "proposal": version_out(proposal).model_dump() if proposal else None,
        "solver": {
            "status": ctx.published.status,
            "objective": ctx.published.objective,
            "best_bound": ctx.published.best_bound,
            "solve_seconds": round(ctx.published.solve_seconds, 2),
        },
        "violations": report.total,
        "families": len(report.counts),
        "quality": asdict(quality),
        "pending_count": len(pending),
        "pending_requests": [request_out(db, r).model_dump() for r in pending[:5]],
        "overrides": overrides,
        "locked": len(ctx.instance.locks),
        "versions": [version_out(v).model_dump() for v in repo.list_versions(db, 6)],
        "structure": {
            "years": len({b.year for b in ctx.base.batches if b.year}),
            "divisions": len(ctx.base.batches),
            "faculty": len(ctx.base.faculty),
            "rooms": sum(1 for r in ctx.base.rooms if r.room_type is RoomType.LECTURE),
            "labs": sum(1 for r in ctx.base.rooms if r.room_type is RoomType.LAB),
            "sessions": len(ctx.base.sessions),
        },
    }


@router.get("/academic/structure")
def academic_structure(
    user: m.UserRow = Depends(current_user), db: DB = Depends(get_db)
) -> dict:
    """Department -> programme -> year -> semester -> division, for every role."""
    return scheduling.structure(_ctx(db))


# --------------------------------------------------------------------------
# Divisions
# --------------------------------------------------------------------------


def _batch_out(ctx: scheduling.Context, batch_id: str) -> BatchOut:
    b = ctx.base.batch_by_id[batch_id]
    mine = [s for s in ctx.base.sessions if s.batch_id == batch_id]
    return BatchOut(
        id=b.id,
        name=b.name,
        strength=b.strength,
        department=b.department,
        program=b.program,
        year=b.year,
        year_label=b.year_label,
        semester=b.semester,
        sessions=len(mine),
        contact_hours=sum(s.duration for s in mine),
    )


@router.get("/batches", response_model=list[BatchOut])
def list_batches(
    user: m.UserRow = Depends(current_user), db: DB = Depends(get_db)
) -> list[BatchOut]:
    ctx = _ctx(db)
    return [_batch_out(ctx, b.id) for b in ctx.base.batches]


@router.post("/batches", response_model=BatchOut)
def create_batch(
    body: BatchIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> BatchOut:
    """Add a division; its department, programme, year and semester are created
    if they do not exist yet."""
    try:
        repo.add_batch(
            db,
            Batch(
                id=body.id,
                name=body.name,
                strength=body.strength,
                department=body.department,
                program=body.program or body.department,
                year=body.year,
                year_label=body.year_label or f"Year {body.year}",
                semester=body.semester,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _batch_out(_ctx(db), body.id)


@router.patch("/batches/{batch_id}", response_model=BatchOut)
def patch_batch(
    batch_id: str,
    body: BatchPatch,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> BatchOut:
    try:
        repo.update_batch(db, batch_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return _batch_out(_ctx(db), batch_id)


# --------------------------------------------------------------------------
# Faculty
# --------------------------------------------------------------------------


def _faculty_rows(db: DB, ctx: scheduling.Context) -> list[FacultyOut]:
    load = {w["faculty_id"]: w for w in faculty_workload(ctx.instance, ctx.published)}
    accounts = repo.users_by_faculty(db)
    out = []
    for f in ctx.base.faculty:
        w = load.get(f.id, {})
        taught = sorted({s.subject_code for s in ctx.base.sessions if s.faculty_id == f.id})
        out.append(
            FacultyOut(
                id=f.id,
                name=f.name,
                department=f.department,
                max_daily_load=f.max_daily_load,
                max_weekly_load=f.max_weekly_load,
                max_consecutive=f.max_consecutive,
                unavailable=sorted(f.unavailable),
                preferred_off=sorted(f.preferred_off),
                subjects=list(f.subjects) or taught,
                weekly_hours=w.get("weekly_hours", 0),
                busiest_day_hours=w.get("busiest_day_hours", 0),
                years_taught=w.get("years_taught", []),
                session_count=sum(1 for s in ctx.base.sessions if s.faculty_id == f.id),
                username=accounts[f.id].username if f.id in accounts else None,
            )
        )
    return out


def _one_faculty(db: DB, faculty_id: str) -> FacultyOut:
    for row in _faculty_rows(db, _ctx(db)):
        if row.id == faculty_id:
            return row
    raise HTTPException(status_code=404, detail=f"No faculty {faculty_id!r}")


@router.get("/faculty", response_model=list[FacultyOut])
def list_faculty(user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)) -> list[FacultyOut]:
    return _faculty_rows(db, _ctx(db))


@router.patch("/faculty/{faculty_id}", response_model=FacultyOut)
def patch_faculty(
    faculty_id: str,
    body: FacultyPatch,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> FacultyOut:
    try:
        repo.update_faculty(db, faculty_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return _one_faculty(db, faculty_id)


@router.put("/faculty/{faculty_id}/availability", response_model=FacultyOut)
def put_availability(
    faculty_id: str,
    body: AvailabilityIn,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> FacultyOut:
    """Regular availability: a base constraint, persistent until edited."""
    ctx = _ctx(db)
    if faculty_id not in ctx.base.faculty_by_id:
        raise HTTPException(status_code=404, detail=f"No faculty {faculty_id!r}")
    repo.set_faculty_slots(db, faculty_id, repo.UNAVAILABLE, _slots(ctx, body.unavailable))
    repo.set_faculty_slots(db, faculty_id, repo.PREFERRED_OFF, _slots(ctx, body.preferred_off))
    db.commit()
    return _one_faculty(db, faculty_id)


# --------------------------------------------------------------------------
# Rooms and laboratories
# --------------------------------------------------------------------------


def _room_rows(ctx: scheduling.Context) -> list[RoomOut]:
    usage = {u["room_id"]: u for u in room_usage(ctx.instance, ctx.published)}
    out = []
    for r in ctx.base.rooms:
        u = usage.get(r.id, {})
        out.append(
            RoomOut(
                id=r.id,
                name=r.name,
                building=r.building,
                room_type=r.room_type.value,
                capacity=r.capacity,
                active=r.active,
                capabilities=sorted(r.capabilities),
                unavailable=sorted(r.unavailable),
                booked_hours=u.get("booked_hours", 0),
                utilisation_pct=round(u.get("utilisation_pct", 0.0), 1),
                avg_fill_pct=round(u.get("avg_fill_pct", 0.0), 1),
            )
        )
    return out


def _one_room(db: DB, room_id: str) -> RoomOut:
    for row in _room_rows(_ctx(db)):
        if row.id == room_id:
            return row
    raise HTTPException(status_code=404, detail=f"No room {room_id!r}")


@router.get("/rooms", response_model=list[RoomOut])
def list_rooms(user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)) -> list[RoomOut]:
    return _room_rows(_ctx(db))


@router.patch("/rooms/{room_id}", response_model=RoomOut)
def patch_room(
    room_id: str,
    body: RoomPatch,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> RoomOut:
    fields = body.model_dump()
    capabilities = fields.pop("capabilities")
    try:
        repo.update_room(db, room_id, capabilities=capabilities, **fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return _one_room(db, room_id)


@router.put("/rooms/{room_id}/blocks", response_model=RoomOut)
def put_room_blocks(
    room_id: str,
    body: BlocksIn,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> RoomOut:
    ctx = _ctx(db)
    if room_id not in ctx.base.room_by_id:
        raise HTTPException(status_code=404, detail=f"No room {room_id!r}")
    repo.set_room_blocks(db, room_id, _slots(ctx, body.unavailable))
    db.commit()
    return _one_room(db, room_id)


# --------------------------------------------------------------------------
# Subjects
# --------------------------------------------------------------------------


def _subject_out(ctx: scheduling.Context, row: m.SubjectRow) -> SubjectOut:
    f = ctx.base.faculty_by_id.get(row.faculty_id)
    return SubjectOut(
        id=row.id,
        batch_id=row.batch_id,
        code=row.code,
        name=row.name,
        faculty_id=row.faculty_id,
        faculty_name=f.name if f else row.faculty_id,
        category=row.category,
        sessions_per_week=row.sessions_per_week,
        duration=row.duration,
        room_type=row.room_type,
        required_capability=row.required_capability,
        elective_key=row.elective_key,
        headcount=row.headcount,
    )


@router.get("/subjects", response_model=list[SubjectOut])
def list_subjects(
    batch_id: str | None = None,
    user: m.UserRow = Depends(STAFF),
    db: DB = Depends(get_db),
) -> list[SubjectOut]:
    ctx = _ctx(db)
    return [_subject_out(ctx, row) for row in repo.subjects(db, batch_id)]


@router.post("/subjects", response_model=SubjectOut)
def create_subject(
    body: SubjectIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> SubjectOut:
    ctx = _ctx(db)
    if body.batch_id not in ctx.base.batch_by_id:
        raise HTTPException(status_code=404, detail=f"No division {body.batch_id!r}")
    if body.faculty_id not in ctx.base.faculty_by_id:
        raise HTTPException(status_code=404, detail=f"No faculty {body.faculty_id!r}")
    try:
        row = repo.add_subject(
            db,
            batch_id=body.batch_id,
            code=body.code.upper(),
            name=body.name,
            faculty_id=body.faculty_id,
            category=body.category,
            sessions_per_week=body.sessions_per_week,
            duration=body.duration,
            room_type="LAB" if body.category == "LAB" else "LECTURE",
            required_capability=body.required_capability or None,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _subject_out(_ctx(db), row)


@router.patch("/subjects/{subject_id}", response_model=SubjectOut)
def patch_subject(
    subject_id: int,
    body: SubjectPatch,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> SubjectOut:
    ctx = _ctx(db)
    if body.faculty_id is not None and body.faculty_id not in ctx.base.faculty_by_id:
        raise HTTPException(status_code=404, detail=f"No faculty {body.faculty_id!r}")
    try:
        row = repo.update_subject(db, subject_id, **body.model_dump(exclude_unset=True))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _subject_out(_ctx(db), row)


@router.delete("/subjects/{subject_id}")
def remove_subject(
    subject_id: int, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> dict:
    try:
        repo.delete_subject(db, subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return {"deleted": subject_id}


# --------------------------------------------------------------------------
# Rules: base constraints and temporary overrides
# --------------------------------------------------------------------------


@router.get("/constraints", response_model=list[ConstraintOut])
def list_constraints(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> list[ConstraintOut]:
    ctx = _ctx(db)
    return [constraint_out(r, ctx.instance, ctx.today) for r in reversed(ctx.records)]


@router.post("/constraints", response_model=ConstraintOut)
def create_constraint(
    body: ConstraintIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> ConstraintOut:
    if body.kind == "RULE" and (body.rule_field is None or body.rule_value is None):
        raise HTTPException(status_code=400, detail="A rule needs a field and a value.")
    if body.kind != "RULE" and not body.target_id:
        raise HTTPException(status_code=400, detail="Name who or what is unavailable.")
    days = tuple(sorted({d for d in body.days if 0 <= d <= 4})) or WEEKDAYS
    rec = ConstraintRecord(
        category=body.category,
        kind=body.kind,
        target_id=body.target_id,
        days=days,
        start_time=body.start_time,
        end_time=body.end_time,
        start_date=body.start_date,
        end_date=body.end_date,
        rule_field=body.rule_field,
        rule_value=body.rule_value,
        reason=body.reason,
        created_by=user.username,
        status=ACTIVE,
    )
    with service_errors():
        row = scheduling.create_constraint(db, rec)
    ctx = _ctx(db)
    return constraint_out(repo.record_from_row(row), ctx.instance, ctx.today)


@router.patch("/constraints/{record_id}", response_model=ConstraintOut)
def patch_constraint(
    record_id: int,
    body: ConstraintPatch,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> ConstraintOut:
    with service_errors():
        row = scheduling.set_constraint_status(db, record_id, body.status)
    ctx = _ctx(db)
    return constraint_out(repo.record_from_row(row), ctx.instance, ctx.today)


@router.delete("/constraints/{record_id}")
def remove_constraint(
    record_id: int, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> dict:
    with service_errors():
        scheduling.delete_constraint(db, record_id)
    return {"deleted": record_id}


@router.post("/constraints/{record_id}/restore-preview", response_model=WhatIfOut)
def restore_preview(
    record_id: int, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> WhatIfOut:
    """After an override ends: preview going back towards the timetable it
    replaced. Nothing is reverted automatically."""
    track = ProgressLog()
    with service_errors():
        w = scheduling.restore_preview(
            db, record_id, weights=repo.get_weights(db), by=user.username, progress=track
        )
    return repair_out(
        story=w.story,
        descriptions=w.descriptions,
        affected=[],
        result=w.result,
        instance=w.disrupted,
        stages=track.as_list(),
        proposal=w.proposal,
        diff=w.diff,
    )


# --------------------------------------------------------------------------
# Locks and coordinator moves
# --------------------------------------------------------------------------


@router.get("/locks", response_model=list[ConstraintOut])
def list_locks(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> list[ConstraintOut]:
    ctx = _ctx(db)
    return [
        constraint_out(r, ctx.instance, ctx.today)
        for r in ctx.records
        if r.kind == LOCK and r.status == ACTIVE
    ]


@router.post("/locks", response_model=ConstraintOut)
def create_lock(
    body: LockIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> ConstraintOut:
    """Pin a session where it is published. Every later solve must keep it."""
    with service_errors():
        row = scheduling.lock_session(
            db,
            session_id=body.session_id,
            keep_room=body.keep_room,
            reason=body.reason,
            by=user.username,
        )
    ctx = _ctx(db)
    return constraint_out(repo.record_from_row(row), ctx.instance, ctx.today)


@router.delete("/locks/{session_id}")
def remove_lock(
    session_id: str, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> dict:
    with service_errors():
        count = scheduling.unlock_session(db, session_id)
    return {"unlocked": session_id, "records": count}


@router.post("/moves/check", response_model=MoveCheckOut)
def move_check(
    body: MoveIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> MoveCheckOut:
    """Which rules stand in the way of a requested move -- before solving."""
    ctx = _ctx(db)
    try:
        check = check_move(
            ctx.instance, ctx.published, body.session_id, body.timeslot_id, body.room_id
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MoveCheckOut(
        session_id=check.session_id,
        target_label=check.target_label,
        room_id=check.room_id,
        allowed=check.allowed,
        hard=[BlockerOut(rule=b.rule, message=b.message) for b in check.hard],
        resolvable=[BlockerOut(rule=b.rule, message=b.message) for b in check.resolvable],
    )


def _move_payload(db: DB, body: MoveIn, user: m.UserRow, track: ProgressLog) -> WhatIfOut:
    ctx = _ctx(db)
    with service_errors():
        preview = scheduling.run_move_preview(
            db,
            ctx,
            session_id=body.session_id,
            timeslot_id=body.timeslot_id,
            room_id=body.room_id,
            weights=repo.get_weights(db),
            by=user.username,
            progress=track,
        )
    if preview.result is None:
        return WhatIfOut(
            feasible=False,
            status="REFUSED",
            reason="; ".join(b.message for b in preview.check.hard),
            story=preview.story,
            disruptions=[preview.story],
            directly_affected=[],
            stages=[],
        )
    return repair_out(
        story=preview.story,
        descriptions=[preview.story],
        affected=[],
        result=preview.result,
        instance=preview.instance,
        stages=track.as_list(),
        proposal=preview.proposal,
    )


@router.post("/moves/preview", response_model=WhatIfOut)
def move_preview(
    body: MoveIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> WhatIfOut:
    """Treat the requested placement as locked and repair around it."""
    return _move_payload(db, body, user, ProgressLog())


@router.post("/moves/preview/stream")
def move_preview_stream(
    body: MoveIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> StreamingResponse:
    ctx = _ctx(db)
    if body.session_id not in ctx.instance.session_by_id:
        raise HTTPException(status_code=404, detail=f"No session {body.session_id!r}")
    username = user.username

    def work(track: ProgressLog) -> WhatIfOut:
        with DATABASE.session() as wdb:
            actor = repo.user_by_username(wdb, username)
            return _move_payload(wdb, body, actor, track)

    return StreamingResponse(
        stream_solve(work), media_type="text/event-stream", headers=SSE_HEADERS
    )


# --------------------------------------------------------------------------
# Versions, analytics, settings, recovery
# --------------------------------------------------------------------------


@router.get("/versions", response_model=list[VersionOut])
def list_versions(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> list[VersionOut]:
    return [version_out(v) for v in repo.list_versions(db, 100)]


@router.get("/analytics")
def analytics(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> dict:
    return scheduling.analytics(db, _ctx(db))


@router.get("/settings/weights", response_model=WeightsOut)
def get_weights(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> WeightsOut:
    return weights_out(repo.get_weights(db))


@router.put("/settings/weights", response_model=WeightsOut)
def put_weights(
    body: WeightsIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> WeightsOut:
    repo.set_weights(db, body.to_weights())
    db.commit()
    return weights_out(repo.get_weights(db))


@router.post("/admin/restore-rehearsed", response_model=VersionOut)
def restore(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> VersionOut:
    """Republish the rehearsed demo baseline as a new version. History is kept."""
    return version_out(restore_rehearsed(db, user.username))
