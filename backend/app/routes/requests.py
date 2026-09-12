"""Teacher unavailability requests.

A teacher reports an absence and sees ranked repair options computed by the
solver; they pick one, or Auto-select Best, and submit it. Only a coordinator
can approve it, and only approval publishes anything. A teacher's request is a
temporary operational constraint -- it never rewrites the institution's
timetable on its own.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as DB

from ..db import models as m
from ..db import repository as repo
from ..deps import (
    ADMIN_ONLY,
    DATABASE,
    SSE_HEADERS,
    STAFF,
    get_db,
    service_errors,
    stream_solve,
)
from ..progress import ProgressLog
from ..schemas import (
    AffectedOut,
    DecisionIn,
    OptionOut,
    RequestIn,
    RequestOut,
    SolverOut,
    SubmitIn,
    VersionOut,
    WhatIfOut,
    changes_out,
    iso,
    quality_out,
    validation_out,
    version_out,
)
from ..services import scheduling
from ..services.auth import ADMIN, FACULTY

router = APIRouter(prefix="/api/requests", tags=["requests"])


class ApprovalOut(BaseModel):
    request: RequestOut
    version: VersionOut


def request_out(db: DB, row: m.RequestRow) -> RequestOut:
    options, failure = scheduling.request_options(row)
    teacher = db.get(m.FacultyRow, row.faculty_id)
    fields = set(OptionOut.model_fields)
    return RequestOut(
        id=row.id,
        faculty_id=row.faculty_id,
        faculty_name=teacher.name if teacher else row.faculty_id,
        status=row.status,
        reason=row.reason or "",
        start_date=row.start_date.isoformat(),
        end_date=row.end_date.isoformat(),
        start_time=row.start_time,
        end_time=row.end_time,
        window_label=scheduling.window_label(row),
        created_by=row.created_by,
        created_at=iso(row.created_at),
        submitted_at=iso(row.submitted_at),
        decided_by=row.decided_by,
        decided_at=iso(row.decided_at),
        decision_note=row.decision_note,
        base_version_id=row.base_version_id,
        result_version_id=row.result_version_id,
        stale=scheduling.is_stale(row, repo.current_version(db)),
        affected=[AffectedOut(**a) for a in json.loads(row.affected_json or "[]")],
        options=[OptionOut(**{k: v for k, v in o.items() if k in fields}) for o in options],
        failure=failure,
        selected_rank=row.selected_rank,
        auto_selected=bool(row.auto_selected),
    )


def _load(db: DB, request_id: int) -> m.RequestRow:
    row = repo.request_by_id(db, request_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No request {request_id}")
    return row


def _check_visible(user: m.UserRow, row: m.RequestRow) -> None:
    if user.role == ADMIN:
        return
    if user.role == FACULTY and user.faculty_id == row.faculty_id:
        return
    raise HTTPException(status_code=403, detail="You can only see your own requests.")


def _teacher_for(user: m.UserRow, requested: str | None) -> str:
    if user.role == FACULTY:
        if not user.faculty_id:
            raise HTTPException(status_code=403, detail="This account is not linked to a teacher.")
        if requested and requested != user.faculty_id:
            raise HTTPException(
                status_code=403,
                detail="Teachers can only report their own unavailability.",
            )
        return user.faculty_id
    if not requested:
        raise HTTPException(status_code=400, detail="Name the teacher this request is for.")
    return requested


@router.post("", response_model=RequestOut)
def create_request(
    body: RequestIn, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> RequestOut:
    """Report unavailability and compute ranked repair options in one step."""
    teacher = _teacher_for(user, body.faculty_id)
    with service_errors():
        row = scheduling.create_request(
            db,
            faculty_id=teacher,
            start_date=body.start_date,
            end_date=body.end_date,
            start_time=body.start_time,
            end_time=body.end_time,
            reason=body.reason,
            by=user.username,
        )
        row = scheduling.compute_options(db, row.id)
    return request_out(db, row)


def _options_stream(request_id: int) -> StreamingResponse:
    def work(track: ProgressLog) -> RequestOut:
        with DATABASE.session() as db:
            row = scheduling.compute_options(db, request_id, progress=track)
            return request_out(db, row)

    return StreamingResponse(
        stream_solve(work), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/stream")
def create_request_stream(
    body: RequestIn, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> StreamingResponse:
    """The same, reporting each repair alternative's stages as they finish.

    The request is validated and recorded before the stream opens, so a bad
    request is refused with its real status rather than a 200.
    """
    teacher = _teacher_for(user, body.faculty_id)
    with service_errors():
        row = scheduling.create_request(
            db,
            faculty_id=teacher,
            start_date=body.start_date,
            end_date=body.end_date,
            start_time=body.start_time,
            end_time=body.end_time,
            reason=body.reason,
            by=user.username,
        )
    return _options_stream(row.id)


@router.get("", response_model=list[RequestOut])
def list_requests(
    status: str | None = None,
    user: m.UserRow = Depends(STAFF),
    db: DB = Depends(get_db),
) -> list[RequestOut]:
    """Coordinators see every request; teachers see their own."""
    statuses = tuple(s.strip().upper() for s in status.split(",") if s.strip()) if status else None
    teacher = None if user.role == ADMIN else (user.faculty_id or "-")
    return [
        request_out(db, row)
        for row in repo.list_requests(db, faculty_id=teacher, statuses=statuses)
    ]


@router.get("/{request_id}", response_model=RequestOut)
def get_request(
    request_id: int, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> RequestOut:
    row = _load(db, request_id)
    _check_visible(user, row)
    return request_out(db, row)


@router.post("/{request_id}/recompute", response_model=RequestOut)
def recompute(
    request_id: int, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> RequestOut:
    """Recompute options against the timetable as it is published now."""
    row = _load(db, request_id)
    _check_visible(user, row)
    with service_errors():
        row = scheduling.compute_options(db, row.id)
    return request_out(db, row)


@router.post("/{request_id}/recompute/stream")
def recompute_stream(
    request_id: int, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> StreamingResponse:
    row = _load(db, request_id)
    _check_visible(user, row)
    if row.status not in (scheduling.DRAFT, scheduling.STALE):
        raise HTTPException(
            status_code=409,
            detail=f"Request {row.id} is {row.status.lower()}; its options can no longer change.",
        )
    return _options_stream(row.id)


@router.post("/{request_id}/submit", response_model=RequestOut)
def submit(
    request_id: int,
    body: SubmitIn,
    user: m.UserRow = Depends(STAFF),
    db: DB = Depends(get_db),
) -> RequestOut:
    """Choose an option -- or Auto-select Best -- and send it for approval."""
    row = _load(db, request_id)
    _check_visible(user, row)
    with service_errors():
        row = scheduling.submit_request(db, row.id, rank=body.rank, auto=body.auto)
    return request_out(db, row)


@router.post("/{request_id}/withdraw", response_model=RequestOut)
def withdraw(
    request_id: int, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> RequestOut:
    row = _load(db, request_id)
    _check_visible(user, row)
    with service_errors():
        row = scheduling.withdraw_request(
            db, row.id, faculty_id=user.faculty_id if user.role == FACULTY else row.faculty_id
        )
    return request_out(db, row)


@router.get("/{request_id}/review", response_model=WhatIfOut)
def review(
    request_id: int,
    rank: int | None = None,
    user: m.UserRow = Depends(STAFF),
    db: DB = Depends(get_db),
) -> WhatIfOut:
    """The chosen option as a what-if against today's published timetable:
    old vs proposed, every change with the rule that forced it, retention,
    and an independent validation. Publishes nothing."""
    row = _load(db, request_id)
    _check_visible(user, row)
    with service_errors():
        ctx, disrupted, tt, diff, option = scheduling.request_preview(db, row, rank)
    teacher = ctx.base.faculty_by_id.get(row.faculty_id)
    name = teacher.name if teacher else row.faculty_id
    story = f"{name} unavailable {scheduling.window_label(row)}"
    return WhatIfOut(
        feasible=True,
        status=option.get("status", "FEASIBLE"),
        story=story,
        disruptions=[story + (f" — {row.reason}" if row.reason else "")],
        directly_affected=[AffectedOut(**a) for a in json.loads(row.affected_json or "[]")],
        total=diff.total,
        unchanged=diff.unchanged,
        changed=diff.changed,
        time_moves=diff.time_moves,
        room_only_moves=diff.room_only_moves,
        retention_pct=round(diff.retention_pct, 1),
        time_retention_pct=round(diff.time_retention_pct, 1),
        changes=changes_out(disrupted, tt, diff),
        validation=validation_out(disrupted, tt),
        quality=quality_out(disrupted, tt),
        solver=SolverOut(
            status=option.get("status", "FEASIBLE"),
            objective=option.get("objective"),
            best_bound=None,
            solve_seconds=option.get("solve_seconds", 0.0),
        ),
        total_seconds=option.get("solve_seconds", 0.0),
        phase1_status=option.get("phase1_status", "UNKNOWN"),
        phase2_status=option.get("phase2_status", "SKIPPED"),
        minimal_proven=bool(option.get("minimal_proven")),
    )


@router.post("/{request_id}/approve", response_model=ApprovalOut)
def approve(
    request_id: int,
    body: DecisionIn,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> ApprovalOut:
    """Publish the chosen repair as a new timetable version."""
    _load(db, request_id)
    with service_errors():
        version = scheduling.approve_request(db, request_id, by=user.username, note=body.note)
    return ApprovalOut(request=request_out(db, _load(db, request_id)), version=version_out(version))


@router.post("/{request_id}/reject", response_model=RequestOut)
def reject(
    request_id: int,
    body: DecisionIn,
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> RequestOut:
    _load(db, request_id)
    with service_errors():
        row = scheduling.reject_request(db, request_id, by=user.username, note=body.note)
    return request_out(db, row)
