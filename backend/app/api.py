"""FastAPI application for ChronoSolve.

The state model mirrors how a coordinator works:

  published   the timetable version students and faculty are living with
  proposed    a repair computed and stored, but NOT yet published
  approve     the only operation that turns a proposal into the published version

What-if analysis never touches the published version. Every change of state is
a timetable version in the database; nothing lives only in memory, and nothing
is ever written back to the JSON fixtures the database is seeded from.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session as DB

from .data.excel import read_workbook, to_workbook
from .data.synthetic import enrich_metadata
from .db import models as m
from .db import repository as repo
from .deps import (
    ADMIN_ONLY,
    DATABASE,
    SSE_HEADERS,
    STAFF,
    current_user,
    get_db,
    service_errors,
    stream_solve,
)
from .domain.models import DAYS, Instance, Timetable
from .export import to_ics, to_pdf, to_xlsx
from .nl import parse as parse_rule
from .progress import ProgressLog
from .routes import admin as admin_routes
from .routes import auth as auth_routes
from .routes import requests as request_routes
from .schemas import (
    DisruptionIn,
    EntitiesOut,
    EntityOut,
    ExplanationOut,
    GenerateIn,
    GridOut,
    ImportIssueOut,
    ImportOut,
    LockProposalOut,
    ParseIn,
    ParseOut,
    RuleChangeIn,
    RuleProposalOut,
    ScenarioOut,
    SlotOptionOut,
    BlockerOut,
    StageOut,
    StateOut,
    WhatIfIn,
    WhatIfOut,
    affected_out,
    calendar_out,
    grid_cells,
    iso,
    iso_date,
    quality_out,
    repair_out,
    solver_out,
    summary_out,
    validation_out,
    version_out,
    weights_out,
)
from .services import scheduling
from .services.auth import ADMIN
from .services.constraints import ACTIVE, EXPIRED, TEMPORARY
from .solver.disruption import (
    Disruption,
    RuleChange,
    batch_unavailable,
    builtin_scenarios,
    faculty_unavailable,
    room_unavailable,
    rule_change,
)
from .solver.explain import explain_session
from .solver.metrics import ScheduleDiff, diff_schedules

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Migrate and seed before the first request, so a health check that
    # passes means the database is ready. Tests configure it themselves.
    if DATABASE.factory is None:
        DATABASE.configure()
    yield


app = FastAPI(
    title="ChronoSolve API",
    description="Adaptive constraint-optimised academic scheduling",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(request_routes.router)
app.include_router(admin_routes.router)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _ctx(db: DB) -> scheduling.Context:
    with service_errors():
        return scheduling.load_context(db)


def build_state(
    db: DB, ctx: scheduling.Context | None = None, stages: list[dict] | None = None
) -> StateOut:
    ctx = ctx or _ctx(db)
    proposal = scheduling.open_proposal(db, ctx)
    overrides = sum(
        1
        for r in ctx.records
        if r.category == TEMPORARY and r.status == ACTIVE and r.lifecycle(ctx.today) != EXPIRED
    )
    return StateOut(
        summary=summary_out(ctx.instance),
        calendar=calendar_out(ctx.instance),
        published=solver_out(ctx.published),
        published_label=ctx.version.label,
        published_saved_at=iso(ctx.version.created_at),
        # Judged against the rules in force today, so a rule added after
        # publishing shows up here as a violation to repair.
        validation=validation_out(ctx.instance, ctx.published),
        quality=quality_out(ctx.instance, ctx.published),
        has_pending=proposal is not None,
        weights=weights_out(repo.get_weights(db)),
        stages=[StageOut(**s) for s in stages or []],
        version=version_out(ctx.version),
        proposal=version_out(proposal),
        today=ctx.today.isoformat(),
        pending_requests=len(repo.list_requests(db, statuses=(scheduling.SUBMITTED,))),
        active_overrides=overrides,
        locked_sessions=len(ctx.instance.locks),
    )


def _build_rule_changes(inst: Instance, items: list[RuleChangeIn]) -> list[RuleChange]:
    out: list[RuleChange] = []
    for item in items:
        try:
            out.append(rule_change(inst, item.rule, item.value, item.faculty))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return out


def _build_disruptions(inst: Instance, items: list[DisruptionIn]) -> list[Disruption]:
    out: list[Disruption] = []
    for d in items:
        try:
            builder = {
                "FACULTY_UNAVAILABLE": faculty_unavailable,
                "ROOM_UNAVAILABLE": room_unavailable,
                "BATCH_UNAVAILABLE": batch_unavailable,
            }[d.kind]
            built = builder(inst, d.target, d.day, d.start_time, d.end_time)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not built.timeslots:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{d.target}: {d.start_time}-{d.end_time} on "
                    f"{DAYS[d.day]} covers no teaching period, so it would "
                    f"block nothing."
                ),
            )
        out.append(built)
    return out


def _resolve_what_if(
    ctx: scheduling.Context, body: WhatIfIn
) -> tuple[str, list[Disruption], list[RuleChange]]:
    """Turn a request into the rules it asks for, or raise the right HTTP error.

    Separate from the computation so the streaming route can validate *before*
    it opens a stream: once SSE headers are sent the status can no longer be
    changed, and a rejected request would otherwise arrive as a 200.
    """
    if body.scenario:
        catalogue = builtin_scenarios(ctx.instance)
        if body.scenario not in catalogue:
            raise HTTPException(
                status_code=404, detail=f"Unknown scenario {body.scenario!r}"
            )
        chosen = catalogue[body.scenario]
        return chosen.story, list(chosen.disruptions), list(chosen.rule_changes)

    if body.disruptions or body.rule_changes:
        disruptions = _build_disruptions(ctx.instance, body.disruptions)
        rules = _build_rule_changes(ctx.instance, body.rule_changes)
        story = "; ".join(
            [d.description for d in disruptions] + [r.description for r in rules]
        )
        return story, disruptions, rules

    raise HTTPException(
        status_code=400,
        detail="Provide a scenario, a disruption, or a rule change",
    )


# ----------------------------------------------------------------------
# State and views
# ----------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "solver": "Google OR-Tools CP-SAT"}


@app.get("/api/state", response_model=StateOut)
def get_state(user: m.UserRow = Depends(current_user), db: DB = Depends(get_db)) -> StateOut:
    return build_state(db)


@app.get("/api/entities", response_model=EntitiesOut)
def get_entities(
    user: m.UserRow = Depends(current_user), db: DB = Depends(get_db)
) -> EntitiesOut:
    inst = _ctx(db).instance
    return EntitiesOut(
        batches=[
            EntityOut(
                id=b.id,
                label=b.id,
                detail=f"{b.name} ({b.strength})"
                + (f" · {b.year_label}, Semester {b.semester}" if b.year else ""),
            )
            for b in inst.batches
        ],
        faculty=[EntityOut(id=f.id, label=f.name, detail=f.id) for f in inst.faculty],
        rooms=[
            EntityOut(
                id=r.id,
                label=r.id,
                detail=f"{r.name} - {r.capacity} seats, {r.room_type.value}"
                + ("" if r.active else " (out of service)"),
            )
            for r in inst.rooms
        ],
    )


def _grid_source(
    db: DB,
    user: m.UserRow,
    ctx: scheduling.Context,
    source: str,
    version_id: int | None,
    request_id: int | None,
    rank: int | None,
) -> tuple[Instance, Timetable, ScheduleDiff | None, str, int | None]:
    """Which timetable a view shows: published, the coordinator's proposal, a
    past version, or a teacher request's option."""
    if request_id is not None:
        row = repo.request_by_id(db, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No request {request_id}")
        if user.role != ADMIN and user.faculty_id != row.faculty_id:
            raise HTTPException(status_code=403, detail="You can only see your own requests.")
        with service_errors():
            _, inst, tt, diff, option = scheduling.request_preview(db, row, rank)
        return inst, tt, diff, f"Request #{row.id}, option {option['label']}", None

    if version_id is not None:
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only coordinators browse past versions.")
        version = repo.version_by_id(db, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail=f"No version {version_id}")
        inst = repo.version_instance(version)
        tt = repo.version_timetable(db, version)
        source_row = (
            repo.version_by_id(db, version.source_version_id)
            if version.source_version_id
            else None
        )
        diff = (
            diff_schedules(inst, repo.version_timetable(db, source_row), tt)
            if source_row is not None
            else None
        )
        return inst, tt, diff, f"Version {version.number} · {version.label}", version.number

    if source == "pending":
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only coordinators see proposals.")
        proposal = scheduling.open_proposal(db, ctx)
        if proposal is None:
            raise HTTPException(status_code=404, detail="No pending repair")
        inst = repo.version_instance(proposal)
        tt = repo.version_timetable(db, proposal)
        return inst, tt, diff_schedules(inst, ctx.published, tt), "Proposed repair", proposal.number

    return (
        ctx.published_instance,
        ctx.published,
        None,
        f"Published · version {ctx.version.number}",
        ctx.version.number,
    )


@app.get("/api/grid", response_model=GridOut)
def get_grid(
    view: Literal["batch", "faculty", "room"] = "batch",
    id: str = Query(...),
    source: Literal["published", "pending"] = "published",
    version_id: int | None = None,
    request_id: int | None = None,
    rank: int | None = None,
    user: m.UserRow = Depends(current_user),
    db: DB = Depends(get_db),
) -> GridOut:
    ctx = _ctx(db)
    inst, tt, diff, source_label, number = _grid_source(
        db, user, ctx, source, version_id, request_id, rank
    )

    if view == "batch":
        if id not in inst.batch_by_id:
            raise HTTPException(status_code=404, detail=f"No batch {id!r}")
        b = inst.batch_by_id[id]
        label = f"{b.id} - {b.name} ({b.strength} students)"

        def keep(s, p):
            return s.batch_id == id
    elif view == "faculty":
        if id not in inst.faculty_by_id:
            raise HTTPException(status_code=404, detail=f"No faculty {id!r}")
        f = inst.faculty_by_id[id]
        label = f"{f.name} ({f.id})"

        def keep(s, p):
            return s.faculty_id == id
    else:
        if id not in inst.room_by_id:
            raise HTTPException(status_code=404, detail=f"No room {id!r}")
        r = inst.room_by_id[id]
        label = f"{r.id} - {r.name} ({r.capacity} seats, {r.room_type.value})"

        def keep(s, p):
            return p.room_id == id

    return GridOut(
        view=view,
        entity_id=id,
        entity_label=label,
        calendar=calendar_out(inst),
        cells=grid_cells(inst, tt, keep, diff, locked=frozenset(ctx.instance.locks)),
        source_label=source_label,
        version_number=number,
    )


@app.get("/api/scenarios", response_model=list[ScenarioOut])
def get_scenarios(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> list[ScenarioOut]:
    ctx = _ctx(db)
    return [
        ScenarioOut(
            key=key,
            story=s.story,
            descriptions=[d.description for d in s.disruptions]
            + [r.description for r in s.rule_changes],
            kind=(
                "mixed"
                if s.disruptions and s.rule_changes
                else "rule"
                if s.rule_changes
                else "availability"
            ),
        )
        for key, s in builtin_scenarios(ctx.instance).items()
    ]


@app.post("/api/parse", response_model=ParseOut)
def post_parse(
    body: ParseIn, user: m.UserRow = Depends(STAFF), db: DB = Depends(get_db)
) -> ParseOut:
    """Turn a sentence into a candidate structured rule for confirmation.

    This endpoint only *proposes*. It never changes the timetable and never
    runs the solver -- the structured rule is applied only after a person
    confirms the reading.
    """
    ctx = _ctx(db)
    rule = parse_rule(body.text, ctx.instance, today=ctx.today)
    return ParseOut(
        text=rule.text,
        understood=rule.understood,
        summary=rule.summary,
        kind=rule.kind,
        target_id=rule.target_id,
        target_label=rule.target_label,
        days=rule.days,
        day_labels=[DAYS[d] for d in rule.days],
        start_time=rule.start_time,
        end_time=rule.end_time,
        issues=rule.issues,
        assumptions=rule.assumptions,
        disruptions=[DisruptionIn(**d) for d in rule.to_disruptions()],
        category=rule.category,
        scope=rule.scope,
        start_date=iso_date(rule.start_date),
        end_date=iso_date(rule.end_date),
        rule_changes=[RuleProposalOut(**c) for c in rule.rule_changes],
        locks=[LockProposalOut(**lock) for lock in rule.locks],
    )


# ----------------------------------------------------------------------
# What-if and repair
# ----------------------------------------------------------------------


def _run_what_if(db: DB, body: WhatIfIn, track: ProgressLog, username: str) -> WhatIfOut:
    """The whole what-if, shared by the plain and streaming routes so the two
    can never drift apart -- the stream reports on exactly this work."""
    ctx = _ctx(db)
    story, disruptions, rules = _resolve_what_if(ctx, body)
    weights = body.weights.to_weights() if body.weights else repo.get_weights(db)
    with service_errors():
        w = scheduling.run_what_if(
            db,
            ctx,
            story=story,
            disruptions=disruptions,
            rules=rules,
            weights=weights,
            phase1_limit=body.phase1_limit,
            phase2_limit=body.phase2_limit,
            by=username,
            progress=track,
        )
    return repair_out(
        story=w.story,
        descriptions=w.descriptions,
        affected=affected_out(ctx.instance, ctx.published, w.affected),
        result=w.result,
        instance=w.disrupted,
        stages=track.as_list(),
        proposal=w.proposal,
    )


@app.post("/api/what-if", response_model=WhatIfOut)
def post_what_if(
    body: WhatIfIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> WhatIfOut:
    """Preview a disruption. Never touches the published timetable."""
    return _run_what_if(db, body, ProgressLog(), user.username)


@app.post("/api/what-if/stream")
def post_what_if_stream(
    body: WhatIfIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> StreamingResponse:
    """`POST /api/what-if`, with the stages reported as they complete."""
    # Reject a bad request with its real status before the stream opens.
    _resolve_what_if(_ctx(db), body)
    username = user.username

    def work(track: ProgressLog) -> WhatIfOut:
        with DATABASE.session() as wdb:
            return _run_what_if(wdb, body, track, username)

    return StreamingResponse(
        stream_solve(work), media_type="text/event-stream", headers=SSE_HEADERS
    )


def _run_reoptimize(db: DB, track: ProgressLog, username: str) -> WhatIfOut:
    ctx = _ctx(db)
    with service_errors():
        w = scheduling.run_what_if(
            db,
            ctx,
            story="Re-optimise the published timetable around today's rules and locks",
            disruptions=[],
            rules=[],
            weights=repo.get_weights(db),
            phase1_limit=scheduling.REPAIR_LIMIT,
            phase2_limit=min(5.0, scheduling.REPAIR_LIMIT),
            by=username,
            progress=track,
        )
    return repair_out(
        story=w.story,
        descriptions=["current rules, overrides and locks"],
        affected=affected_out(ctx.instance, ctx.published, w.affected),
        result=w.result,
        instance=w.disrupted,
        stages=track.as_list(),
        proposal=w.proposal,
    )


@app.post("/api/reoptimize", response_model=WhatIfOut)
def post_reoptimize(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> WhatIfOut:
    """Minimum-disruption repair of the published timetable under the rules in
    force today -- after a configuration edit, a new rule or a lock."""
    return _run_reoptimize(db, ProgressLog(), user.username)


@app.post("/api/reoptimize/stream")
def post_reoptimize_stream(
    user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> StreamingResponse:
    username = user.username

    def work(track: ProgressLog) -> WhatIfOut:
        with DATABASE.session() as wdb:
            return _run_reoptimize(wdb, track, username)

    return StreamingResponse(
        stream_solve(work), media_type="text/event-stream", headers=SSE_HEADERS
    )


@app.get("/api/explain", response_model=ExplanationOut)
def get_explain(
    session_id: str = Query(...),
    source: Literal["published", "pending"] = "published",
    user: m.UserRow = Depends(STAFF),
    db: DB = Depends(get_db),
) -> ExplanationOut:
    """Which rules block every alternative placement for one session.

    Derived from the same structured constraint data that builds the solver
    model -- not a solver unsatisfiable core, and not claimed to be minimal.
    """
    ctx = _ctx(db)
    if source == "pending":
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only coordinators see proposals.")
        proposal = scheduling.open_proposal(db, ctx)
        if proposal is None:
            raise HTTPException(status_code=404, detail="No pending repair")
        inst, tt = repo.version_instance(proposal), repo.version_timetable(db, proposal)
    else:
        inst, tt = ctx.instance, ctx.published

    try:
        ex = explain_session(inst, tt, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ExplanationOut(
        session_id=ex.session_id,
        subject_code=ex.subject_code,
        subject_name=ex.subject_name,
        batch_id=ex.batch_id,
        faculty_name=ex.faculty_name,
        duration=ex.duration,
        current_label=ex.current_label,
        current_room=ex.current_room,
        headline=ex.headline,
        feasible_count=ex.feasible_count,
        options=[
            SlotOptionOut(
                timeslot_id=o.timeslot_id,
                label=o.label,
                feasible=o.feasible,
                room_id=o.room_id,
                blockers=[BlockerOut(rule=b.rule, message=b.message) for b in o.blockers],
            )
            for o in ex.options
        ],
        locked=ex.locked,
    )


# ----------------------------------------------------------------------
# Exports
# ----------------------------------------------------------------------


def _export_target(
    db: DB, user: m.UserRow, source: str, version_id: int | None
) -> tuple[Instance, Timetable, ScheduleDiff | None, str]:
    ctx = _ctx(db)
    if version_id is not None:
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only coordinators export past versions.")
        version = repo.version_by_id(db, version_id)
        if version is None:
            raise HTTPException(status_code=404, detail=f"No version {version_id}")
        return (
            repo.version_instance(version),
            repo.version_timetable(db, version),
            None,
            f"v{version.number}",
        )
    if source == "pending":
        if user.role != ADMIN:
            raise HTTPException(status_code=403, detail="Only coordinators see proposals.")
        proposal = scheduling.open_proposal(db, ctx)
        if proposal is None:
            raise HTTPException(status_code=404, detail="No pending repair to export")
        inst = repo.version_instance(proposal)
        tt = repo.version_timetable(db, proposal)
        return inst, tt, diff_schedules(inst, ctx.published, tt), "proposed-repair"
    return ctx.published_instance, ctx.published, None, ctx.version.label or "published"


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/api/export/xlsx")
def export_xlsx(
    source: Literal["published", "pending"] = "published",
    version_id: int | None = None,
    user: m.UserRow = Depends(current_user),
    db: DB = Depends(get_db),
) -> Response:
    """Workbook: summary, a grid per division, all sessions, and any changes."""
    inst, tt, diff, label = _export_target(db, user, source, version_id)
    return Response(
        content=to_xlsx(inst, tt, diff, label=label),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="chronosolve-{label}.xlsx"'},
    )


@app.get("/api/export/pdf")
def export_pdf(
    source: Literal["published", "pending"] = "published",
    version_id: int | None = None,
    user: m.UserRow = Depends(current_user),
    db: DB = Depends(get_db),
) -> Response:
    """Printable timetable: one page per division plus a verification page."""
    inst, tt, diff, label = _export_target(db, user, source, version_id)
    return Response(
        content=to_pdf(inst, tt, diff, label=label),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="chronosolve-{label}.pdf"'},
    )


@app.get("/api/export/ics")
def export_ics(
    source: Literal["published", "pending"] = "published",
    view: Literal["all", "batch", "faculty", "room"] = "all",
    id: str | None = None,
    weeks: int = Query(default=14, ge=1, le=52),
    version_id: int | None = None,
    user: m.UserRow = Depends(current_user),
    db: DB = Depends(get_db),
) -> Response:
    """Weekly-recurring calendar feed, optionally for one batch/faculty/room."""
    inst, tt, _, _ = _export_target(db, user, source, version_id)

    if view == "all" or not id:
        keep = None
        scope = "all"
    elif view == "batch":
        keep = lambda s, p: s.batch_id == id  # noqa: E731
        scope = id
    elif view == "faculty":
        keep = lambda s, p: s.faculty_id == id  # noqa: E731
        scope = inst.faculty_by_id[id].name if id in inst.faculty_by_id else id
    else:
        keep = lambda s, p: p.room_id == id  # noqa: E731
        scope = id

    text = to_ics(inst, tt, keep=keep, weeks=weeks, calendar_name=f"ChronoSolve — {scope}")
    safe = str(scope).replace(" ", "-").replace(".", "")
    return Response(
        content=text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chronosolve-{safe}.ics"'},
    )


@app.get("/api/import/template")
def export_template(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> Response:
    """The current configuration in the import format.

    Download, edit, re-import: the template is generated from live data, so it
    cannot drift out of step with what the importer accepts.
    """
    return Response(
        content=to_workbook(_ctx(db).base),
        media_type=XLSX,
        headers={"Content-Disposition": 'attachment; filename="chronosolve-template.xlsx"'},
    )


@app.post("/api/import", response_model=ImportOut)
def post_import(
    file: UploadFile = File(...),
    time_limit: float = Query(default=20.0, gt=0, le=120),
    user: m.UserRow = Depends(ADMIN_ONLY),
    db: DB = Depends(get_db),
) -> ImportOut:
    """Replace the configuration from a workbook, then solve and publish it.

    Nothing is applied unless the whole workbook validates -- a partially
    imported department would be worse than a rejected one.
    """
    payload = file.file.read()
    instance, report = read_workbook(payload, name=file.filename or "Imported")

    def issues(items):
        return [
            ImportIssueOut(severity=i.severity, sheet=i.sheet, row=i.row, message=i.message)
            for i in items
        ]

    if not report.ok or instance is None:
        return ImportOut(
            accepted=False,
            counts=report.counts,
            errors=issues(report.errors),
            warnings=issues(report.warnings),
        )

    with service_errors():
        scheduling.import_department(
            db,
            enrich_metadata(instance),
            weights=repo.get_weights(db),
            time_limit=time_limit,
            by=user.username,
            filename=file.filename or "workbook",
        )
    return ImportOut(
        accepted=True,
        counts=report.counts,
        errors=[],
        warnings=issues(report.warnings),
        state=build_state(db),
    )


# ----------------------------------------------------------------------
# Publishing
# ----------------------------------------------------------------------


@app.post("/api/apply", response_model=StateOut)
def post_apply(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> StateOut:
    """Publish the pending proposal as the new timetable version."""
    with service_errors():
        scheduling.apply_proposal(db, by=user.username)
    return build_state(db)


@app.post("/api/discard", response_model=StateOut)
def post_discard(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> StateOut:
    """Throw away the pending proposal; the published version stands."""
    scheduling.discard_proposal(db)
    return build_state(db)


def _run_generate(db: DB, body: GenerateIn, track: ProgressLog, username: str) -> StateOut:
    weights = body.weights.to_weights() if body.weights else repo.get_weights(db)
    with service_errors():
        scheduling.generate_timetable(
            db,
            weights=weights,
            time_limit=body.time_limit,
            publish=body.publish,
            by=username,
            progress=track,
        )
    return build_state(db, stages=track.as_list())


@app.post("/api/generate", response_model=StateOut)
def post_generate(
    body: GenerateIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> StateOut:
    """Solve a fresh timetable from scratch, ignoring the published one."""
    return _run_generate(db, body, ProgressLog(), user.username)


@app.post("/api/generate/stream")
def post_generate_stream(
    body: GenerateIn, user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)
) -> StreamingResponse:
    """`POST /api/generate`, with the stages reported as they complete."""
    username = user.username

    def work(track: ProgressLog) -> StateOut:
        with DATABASE.session() as wdb:
            return _run_generate(wdb, body, track, username)

    return StreamingResponse(
        stream_solve(work), media_type="text/event-stream", headers=SSE_HEADERS
    )


@app.post("/api/reset", response_model=StateOut)
def post_reset(user: m.UserRow = Depends(ADMIN_ONLY), db: DB = Depends(get_db)) -> StateOut:
    """Drop any pending proposal and return to the published version."""
    scheduling.discard_proposal(db)
    return build_state(db)


# ----------------------------------------------------------------------
# Static frontend
# ----------------------------------------------------------------------

# Mounted last so it never shadows an /api route. When the bundle is absent
# (ordinary development, where Vite serves the UI) the API runs on its own.
if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
