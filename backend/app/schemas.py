"""HTTP schemas, and the converters that fill them from domain objects.

Every number in these payloads is computed at request time from stored data or
solver output; nothing here holds a default that could pass for a measurement.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .db import models as m
from .domain.models import DAYS, LUNCH_PERIOD, PERIOD_END, PERIOD_START, Instance, Timetable
from .services.constraints import (
    AVAILABILITY_KINDS,
    LOCK,
    RULE,
    TEMPORARY,
    ConstraintRecord,
)
from .solver.diagnose import DiagnosisReport
from .solver.disruption import BATCH_UNAVAILABLE, FACULTY_UNAVAILABLE, ROOM_UNAVAILABLE
from .solver.explain import explain_move
from .solver.metrics import ScheduleDiff, schedule_metrics
from .solver.model import ObjectiveWeights
from .solver.repair import RepairResult
from .solver.validate import validate

TIME = r"^([01]\d|2[0-3]):[0-5]\d$"


# --------------------------------------------------------------------------
# Shapes shared by every view
# --------------------------------------------------------------------------


class CalendarOut(BaseModel):
    days: list[str]
    period_start: list[str]
    period_end: list[str]
    lunch_period: int


class SummaryOut(BaseModel):
    name: str
    sessions: int
    contact_hours: int
    batches: int
    faculty: int
    rooms: int
    timeslots: int
    teaching_slots: int
    elective_groups: int
    lab_hours: int
    years: int = 0


class SolverOut(BaseModel):
    status: str
    objective: float | None
    best_bound: float | None
    solve_seconds: float


class StageOut(BaseModel):
    """One completed step of a solve, with its measured duration.

    There is no percentage field on purpose: CP-SAT cannot report how much
    search remains, so anything of the sort would be invented.
    """

    key: str
    label: str
    seconds: float
    detail: str = ""


class ValidationOut(BaseModel):
    total_violations: int
    clean: bool
    counts: dict[str, int]
    samples: list[str]


class QualityOut(BaseModel):
    student_idle_hours: int
    faculty_idle_hours: int
    room_utilisation_pct: float
    faculty_load_spread: int
    busiest_faculty_hours: int
    last_slot_sessions: int
    wasted_seats: int = 0
    seat_efficiency_pct: float = 0.0
    oversized_sessions: int = 0
    preference_hits: int = 0


class WeightsOut(BaseModel):
    student_gaps: int
    faculty_gaps: int
    faculty_load_balance: int
    subject_spread: int
    last_slot: int
    room_wastage: int
    faculty_preferences: int
    faculty_daily_target: int


class VersionOut(BaseModel):
    id: int
    number: int
    label: str
    status: str
    is_current: bool
    created_at: str | None
    created_by: str
    reason: str
    solver_status: str
    objective: float | None
    changed_count: int | None
    unchanged_count: int | None
    retention_pct: float | None
    source_version_id: int | None
    request_id: int | None
    effective_until: str | None


class StateOut(BaseModel):
    summary: SummaryOut
    calendar: CalendarOut
    published: SolverOut
    published_label: str | None
    published_saved_at: str | None
    validation: ValidationOut
    quality: QualityOut
    has_pending: bool
    weights: WeightsOut
    # Populated only by a solve that produced this state; a plain GET /api/state
    # is reporting stored facts, not a solve, so it carries none.
    stages: list[StageOut] = []
    version: VersionOut | None = None
    proposal: VersionOut | None = None
    today: str = ""
    pending_requests: int = 0
    active_overrides: int = 0
    locked_sessions: int = 0


class EntityOut(BaseModel):
    id: str
    label: str
    detail: str = ""


class EntitiesOut(BaseModel):
    batches: list[EntityOut]
    faculty: list[EntityOut]
    rooms: list[EntityOut]


class CellOut(BaseModel):
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_id: str
    faculty_name: str
    room_id: str
    day: int
    period: int
    duration: int
    elective_group: str | None
    category: str = "THEORY"
    changed: bool = False
    moved_time: bool = False
    moved_room: bool = False
    from_label: str | None = None
    locked: bool = False


class GridOut(BaseModel):
    view: str
    entity_id: str
    entity_label: str
    calendar: CalendarOut
    cells: list[CellOut]
    source_label: str = ""
    version_number: int | None = None


class ScenarioOut(BaseModel):
    key: str
    story: str
    descriptions: list[str]
    kind: str  # availability | rule | mixed


class AffectedOut(BaseModel):
    session_id: str
    subject_name: str
    batch_id: str
    faculty_name: str
    slot_label: str
    room_id: str
    duration: int
    subject_code: str = ""


class BlockerOut(BaseModel):
    rule: str
    message: str


class ChangeOut(BaseModel):
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_name: str
    from_label: str
    to_label: str
    from_room: str | None
    to_room: str
    moved_time: bool
    moved_room: bool
    # Why this session could not stay where it was published.
    why: list[BlockerOut] = []


class SlotOptionOut(BaseModel):
    timeslot_id: int
    label: str
    feasible: bool
    room_id: str | None
    blockers: list[BlockerOut]


class ExplanationOut(BaseModel):
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_name: str
    duration: int
    current_label: str | None
    current_room: str | None
    headline: str
    feasible_count: int
    options: list[SlotOptionOut]
    locked: bool = False


class FindingOut(BaseModel):
    category: str
    severity: str
    message: str
    suggestions: list[str]


class DiagnosisOut(BaseModel):
    headline: str
    proven_infeasible: bool
    findings: list[FindingOut]


class WhatIfOut(BaseModel):
    feasible: bool
    status: str
    reason: str | None = None
    diagnosis: DiagnosisOut | None = None
    story: str
    disruptions: list[str]
    directly_affected: list[AffectedOut]
    total: int = 0
    unchanged: int = 0
    changed: int = 0
    time_moves: int = 0
    room_only_moves: int = 0
    retention_pct: float = 0.0
    time_retention_pct: float = 0.0
    changes: list[ChangeOut] = []
    validation: ValidationOut | None = None
    quality: QualityOut | None = None
    solver: SolverOut | None = None
    phase1_seconds: float = 0.0
    phase2_seconds: float = 0.0
    total_seconds: float = 0.0
    # Phase 1 proves the move count is minimal; phase 2 only improves quality
    # inside that budget. Only the first certifies minimality.
    phase1_status: str = "UNKNOWN"
    phase2_status: str = "SKIPPED"
    minimal_proven: bool = False
    stages: list[StageOut] = []
    proposal: VersionOut | None = None


# --------------------------------------------------------------------------
# Inputs to the scheduling routes
# --------------------------------------------------------------------------


class WeightsIn(BaseModel):
    student_gaps: int = Field(default=6, ge=0, le=50)
    faculty_gaps: int = Field(default=2, ge=0, le=50)
    faculty_load_balance: int = Field(default=2, ge=0, le=50)
    subject_spread: int = Field(default=3, ge=0, le=50)
    last_slot: int = Field(default=1, ge=0, le=50)
    room_wastage: int = Field(default=1, ge=0, le=50)
    faculty_preferences: int = Field(default=2, ge=0, le=50)
    faculty_daily_target: int = Field(default=3, ge=1, le=8)

    def to_weights(self) -> ObjectiveWeights:
        return ObjectiveWeights(**self.model_dump())


class DisruptionIn(BaseModel):
    kind: Literal["FACULTY_UNAVAILABLE", "ROOM_UNAVAILABLE", "BATCH_UNAVAILABLE"]
    target: str
    day: int = Field(ge=0, le=6)
    start_time: str = "09:00"
    end_time: str = "23:59"


class RuleChangeIn(BaseModel):
    rule: Literal["max_consecutive", "max_daily_load", "max_weekly_load"]
    value: int = Field(ge=1, le=40)
    faculty: str | None = None


class WhatIfIn(BaseModel):
    scenario: str | None = None
    disruptions: list[DisruptionIn] = []
    rule_changes: list[RuleChangeIn] = []
    weights: WeightsIn | None = None
    phase1_limit: float = Field(default=10.0, gt=0, le=60)
    phase2_limit: float = Field(default=5.0, ge=0, le=60)


class ImportIssueOut(BaseModel):
    severity: str
    sheet: str
    row: int | None
    message: str


class ImportOut(BaseModel):
    accepted: bool
    counts: dict[str, int]
    errors: list[ImportIssueOut]
    warnings: list[ImportIssueOut]
    state: StateOut | None = None


class ParseIn(BaseModel):
    text: str = Field(max_length=500)


class LockProposalOut(BaseModel):
    session_id: str
    subject_code: str
    batch_id: str
    timeslot_id: int
    label: str
    room_id: str | None


class RuleProposalOut(BaseModel):
    rule: str
    value: int
    faculty: str | None
    faculty_label: str | None


class ParseOut(BaseModel):
    text: str
    understood: bool
    summary: str
    kind: str | None
    target_id: str | None
    target_label: str | None
    days: list[int]
    day_labels: list[str]
    start_time: str
    end_time: str
    issues: list[str]
    assumptions: list[str]
    # The structured rules a coordinator is being asked to confirm; these are
    # exactly what POST /api/what-if would receive.
    disruptions: list[DisruptionIn]
    # What kind of constraint the sentence proposes, and whether it is a
    # permanent rule or a dated override.
    category: str = "availability"  # availability | rule | lock
    scope: str = "BASE"  # BASE | TEMPORARY
    start_date: str | None = None
    end_date: str | None = None
    rule_changes: list[RuleProposalOut] = []
    locks: list[LockProposalOut] = []


class GenerateIn(BaseModel):
    weights: WeightsIn | None = None
    time_limit: float = Field(default=20.0, gt=0, le=120)
    publish: bool = True


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    faculty_id: str | None
    batch_id: str | None


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class DemoIn(BaseModel):
    role: Literal["ADMIN", "FACULTY", "STUDENT"]
    username: str | None = Field(default=None, max_length=60)


class DemoAccountOut(BaseModel):
    role: str
    username: str
    display_name: str


# --------------------------------------------------------------------------
# Rules, locks and moves
# --------------------------------------------------------------------------


class ConstraintIn(BaseModel):
    category: Literal["BASE", "TEMPORARY"]
    kind: Literal[
        "FACULTY_UNAVAILABLE", "ROOM_UNAVAILABLE", "BATCH_UNAVAILABLE", "RULE"
    ]
    target_id: str | None = Field(default=None, max_length=80)
    days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    start_time: str = Field(default="09:00", pattern=TIME)
    end_time: str = Field(default="23:59", pattern=TIME)
    start_date: date | None = None
    end_date: date | None = None
    rule_field: Literal["max_consecutive", "max_daily_load", "max_weekly_load"] | None = None
    rule_value: int | None = Field(default=None, ge=1, le=40)
    reason: str = Field(default="", max_length=300)


class ConstraintPatch(BaseModel):
    status: Literal["ACTIVE", "INACTIVE"]


class ConstraintOut(BaseModel):
    id: int
    category: str
    kind: str
    target_id: str | None
    summary: str
    days: list[int]
    start_time: str
    end_time: str
    start_date: str | None
    end_date: str | None
    rule_field: str | None
    rule_value: int | None
    session_id: str | None
    lock_timeslot: int | None
    lock_room: str | None
    status: str
    lifecycle: str
    reason: str
    created_by: str
    created_at: str | None
    request_id: int | None


class LockIn(BaseModel):
    session_id: str = Field(max_length=80)
    keep_room: bool = True
    reason: str = Field(default="", max_length=300)


class MoveIn(BaseModel):
    session_id: str = Field(max_length=80)
    timeslot_id: int = Field(ge=0, le=200)
    room_id: str | None = Field(default=None, max_length=40)


class MoveCheckOut(BaseModel):
    session_id: str
    target_label: str
    room_id: str | None
    allowed: bool
    hard: list[BlockerOut]
    resolvable: list[BlockerOut]


# --------------------------------------------------------------------------
# Teacher requests
# --------------------------------------------------------------------------


class RequestIn(BaseModel):
    faculty_id: str | None = Field(default=None, max_length=40)
    start_date: date
    end_date: date
    start_time: str = Field(default="09:00", pattern=TIME)
    end_time: str = Field(default="17:00", pattern=TIME)
    reason: str = Field(default="", max_length=300)


class SubmitIn(BaseModel):
    rank: int | None = Field(default=None, ge=1, le=8)
    auto: bool = False


class DecisionIn(BaseModel):
    note: str = Field(default="", max_length=300)


class OptionMoveOut(BaseModel):
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    from_label: str
    from_room: str
    to_label: str
    to_room: str
    moved_time: bool


class OptionOut(BaseModel):
    rank: int
    label: str
    recommended: bool
    moves: list[OptionMoveOut]
    knock_on: int
    changed: int
    unchanged: int
    total: int
    time_moves: int
    room_only_moves: int
    retention_pct: float
    time_retention_pct: float
    hard_violations: int
    soft_cost: int
    quality: QualityOut
    quality_delta: dict[str, float]
    status: str
    phase1_status: str
    phase2_status: str
    minimal_proven: bool
    solve_seconds: float
    changes: list[ChangeOut]


class RequestOut(BaseModel):
    id: int
    faculty_id: str
    faculty_name: str
    status: str
    reason: str
    start_date: str
    end_date: str
    start_time: str
    end_time: str
    window_label: str
    created_by: str
    created_at: str | None
    submitted_at: str | None
    decided_by: str | None
    decided_at: str | None
    decision_note: str | None
    base_version_id: int | None
    result_version_id: int | None
    stale: bool
    affected: list[AffectedOut]
    options: list[OptionOut]
    failure: dict[str, Any] | None
    selected_rank: int | None
    auto_selected: bool


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


class FacultyOut(BaseModel):
    id: str
    name: str
    department: str
    max_daily_load: int
    max_weekly_load: int
    max_consecutive: int
    unavailable: list[int]
    preferred_off: list[int]
    subjects: list[str]
    weekly_hours: int
    busiest_day_hours: int
    years_taught: list[str]
    session_count: int
    username: str | None


class FacultyPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    max_daily_load: int | None = Field(default=None, ge=1, le=8)
    max_weekly_load: int | None = Field(default=None, ge=1, le=40)
    max_consecutive: int | None = Field(default=None, ge=1, le=8)
    subjects: list[str] | None = None


class AvailabilityIn(BaseModel):
    unavailable: list[int] = []
    preferred_off: list[int] = []


class RoomOut(BaseModel):
    id: str
    name: str
    building: str
    room_type: str
    capacity: int
    active: bool
    capabilities: list[str]
    unavailable: list[int]
    booked_hours: int
    utilisation_pct: float
    avg_fill_pct: float


class RoomPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    building: str | None = Field(default=None, max_length=80)
    capacity: int | None = Field(default=None, ge=1, le=1000)
    active: bool | None = None
    capabilities: list[str] | None = None


class BlocksIn(BaseModel):
    unavailable: list[int] = []


class BatchOut(BaseModel):
    id: str
    name: str
    strength: int
    department: str
    program: str
    year: int
    year_label: str
    semester: int
    sessions: int
    contact_hours: int


class BatchIn(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    strength: int = Field(ge=1, le=500)
    department: str = Field(min_length=1, max_length=120)
    program: str = Field(default="", max_length=160)
    year: int = Field(ge=1, le=6)
    year_label: str = Field(default="", max_length=60)
    semester: int = Field(ge=1, le=12)


class BatchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    strength: int | None = Field(default=None, ge=1, le=500)


class SubjectOut(BaseModel):
    id: int
    batch_id: str
    code: str
    name: str
    faculty_id: str
    faculty_name: str
    category: str
    sessions_per_week: int
    duration: int
    room_type: str
    required_capability: str | None
    elective_key: str | None
    headcount: int | None


class SubjectIn(BaseModel):
    batch_id: str = Field(max_length=40)
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=160)
    faculty_id: str = Field(max_length=40)
    category: Literal["THEORY", "TUTORIAL", "LAB"] = "THEORY"
    sessions_per_week: int = Field(ge=1, le=10)
    duration: int = Field(default=1, ge=1, le=3)
    required_capability: str | None = Field(default=None, max_length=60)


class SubjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    faculty_id: str | None = Field(default=None, max_length=40)
    sessions_per_week: int | None = Field(default=None, ge=1, le=10)
    duration: int | None = Field(default=None, ge=1, le=3)
    required_capability: str | None = Field(default=None, max_length=60)


# --------------------------------------------------------------------------
# Converters
# --------------------------------------------------------------------------


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None


def iso_date(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def calendar_out(inst: Instance) -> CalendarOut:
    return CalendarOut(
        days=list(DAYS[: inst.calendar.days]),
        period_start=list(PERIOD_START[: inst.calendar.periods]),
        period_end=list(PERIOD_END[: inst.calendar.periods]),
        lunch_period=LUNCH_PERIOD,
    )


def summary_out(inst: Instance) -> SummaryOut:
    return SummaryOut(
        name=inst.name,
        sessions=len(inst.sessions),
        contact_hours=inst.total_contact_hours,
        batches=len(inst.batches),
        faculty=len(inst.faculty),
        rooms=len(inst.rooms),
        timeslots=len(inst.calendar),
        teaching_slots=len(inst.calendar.teaching_slots),
        elective_groups=len(inst.elective_groups()),
        lab_hours=sum(s.duration for s in inst.sessions if s.is_lab),
        years=len({b.year for b in inst.batches if b.year}),
    )


def solver_out(tt: Timetable) -> SolverOut:
    return SolverOut(
        status=tt.status,
        objective=tt.objective,
        best_bound=tt.best_bound,
        solve_seconds=round(tt.solve_seconds, 2),
    )


def validation_out(inst: Instance, tt: Timetable) -> ValidationOut:
    rep = validate(inst, tt)
    return ValidationOut(
        total_violations=rep.total,
        clean=rep.is_clean,
        counts=rep.counts,
        samples=[f"[{v.kind}] {v.detail}" for v in rep.violations[:10]],
    )


def quality_out(inst: Instance, tt: Timetable) -> QualityOut:
    q = schedule_metrics(inst, tt)
    return quality_from_metrics(asdict(q))


def quality_from_metrics(q: dict) -> QualityOut:
    return QualityOut(
        student_idle_hours=q["student_idle_hours"],
        faculty_idle_hours=q["faculty_idle_hours"],
        room_utilisation_pct=round(q["room_utilisation_pct"], 1),
        faculty_load_spread=q["faculty_load_spread"],
        busiest_faculty_hours=q["busiest_faculty_hours"],
        last_slot_sessions=q["last_slot_sessions"],
        wasted_seats=q.get("wasted_seats", 0),
        seat_efficiency_pct=round(q.get("seat_efficiency_pct", 0.0), 1),
        oversized_sessions=q.get("oversized_sessions", 0),
        preference_hits=q.get("preference_hits", 0),
    )


def weights_out(w: ObjectiveWeights) -> WeightsOut:
    return WeightsOut(**asdict(w))


def version_out(v: m.VersionRow | None) -> VersionOut | None:
    if v is None:
        return None
    return VersionOut(
        id=v.id,
        number=v.number,
        label=v.label,
        status=v.status,
        is_current=v.is_current,
        created_at=iso(v.created_at),
        created_by=v.created_by or "",
        reason=v.reason or "",
        solver_status=v.solver_status,
        objective=v.objective,
        changed_count=v.changed_count,
        unchanged_count=v.unchanged_count,
        retention_pct=v.retention_pct,
        source_version_id=v.source_version_id,
        request_id=v.request_id,
        effective_until=iso_date(v.effective_until),
    )


def grid_cells(
    inst: Instance,
    tt: Timetable,
    keep,
    diff: ScheduleDiff | None = None,
    locked: frozenset[str] | set[str] = frozenset(),
) -> list[CellOut]:
    cal = inst.calendar
    by_session = {c.session_id: c for c in (diff.changes if diff else [])}
    cells: list[CellOut] = []

    for s in inst.sessions:
        p = tt.placements.get(s.id)
        if p is None or not keep(s, p):
            continue
        slot = cal.by_id[p.timeslot_id]
        change = by_session.get(s.id)
        cells.append(
            CellOut(
                session_id=s.id,
                subject_code=s.subject_code,
                subject_name=s.subject_name,
                batch_id=s.batch_id,
                faculty_id=s.faculty_id,
                faculty_name=inst.faculty_by_id[s.faculty_id].name,
                room_id=p.room_id,
                day=slot.day,
                period=slot.period,
                duration=s.duration,
                elective_group=s.elective_group,
                category=s.kind,
                changed=change is not None,
                moved_time=bool(change and change.moved_time),
                moved_room=bool(change and change.moved_room),
                from_label=(
                    f"{change.from_label} {change.from_room}" if change else None
                ),
                locked=s.id in locked,
            )
        )

    cells.sort(key=lambda c: (c.day, c.period, c.subject_code))
    return cells


def diagnosis_out(report: DiagnosisReport | None) -> DiagnosisOut | None:
    if report is None:
        return None
    return DiagnosisOut(
        headline=report.headline,
        proven_infeasible=report.proven_infeasible,
        findings=[
            FindingOut(
                category=f.category,
                severity=f.severity,
                message=f.message,
                suggestions=list(f.suggestions),
            )
            for f in report.findings
        ],
    )


def affected_out(inst: Instance, published: Timetable, ids: list[str]) -> list[AffectedOut]:
    out = []
    for sid in ids:
        s = inst.session_by_id.get(sid)
        p = published.placements.get(sid)
        if s is None or p is None:
            continue
        out.append(
            AffectedOut(
                session_id=sid,
                subject_code=s.subject_code,
                subject_name=s.subject_name,
                batch_id=s.batch_id,
                faculty_name=inst.faculty_by_id[s.faculty_id].name,
                slot_label=inst.calendar.by_id[p.timeslot_id].long_label,
                room_id=p.room_id,
                duration=s.duration,
            )
        )
    return out


def changes_out(
    inst: Instance, revised: Timetable, diff: ScheduleDiff, *, explain: bool = True
) -> list[ChangeOut]:
    return [
        ChangeOut(
            session_id=c.session_id,
            subject_code=c.subject_code,
            subject_name=c.subject_name,
            batch_id=c.batch_id,
            faculty_name=c.faculty_name,
            from_label=c.from_label,
            to_label=c.to_label,
            from_room=c.from_room,
            to_room=c.to_room,
            moved_time=c.moved_time,
            moved_room=c.moved_room,
            why=[
                BlockerOut(rule=b.rule, message=b.message)
                for b in explain_move(inst, revised, c.session_id, c.from_slot, c.from_room)
            ]
            if explain
            and c.from_slot is not None
            and c.from_room is not None
            and c.session_id in inst.session_by_id
            else [],
        )
        for c in diff.changes
    ]


def repair_out(
    *,
    story: str,
    descriptions: list[str],
    affected: list[AffectedOut],
    result: RepairResult,
    instance: Instance,
    stages: list[dict],
    proposal: m.VersionRow | None,
    diff: ScheduleDiff | None = None,
) -> WhatIfOut:
    """The what-if payload, for any flow that ends in a proposed repair."""
    stage_models = [StageOut(**s) for s in stages]
    diff = diff if diff is not None else result.diff
    if not result.solved or diff is None or result.timetable is None:
        return WhatIfOut(
            feasible=False,
            status=result.status,
            reason=result.reason,
            diagnosis=diagnosis_out(result.diagnosis),
            story=story,
            disruptions=descriptions,
            directly_affected=affected,
            stages=stage_models,
        )
    return WhatIfOut(
        feasible=True,
        status=result.status,
        story=story,
        disruptions=descriptions,
        directly_affected=affected,
        total=diff.total,
        unchanged=diff.unchanged,
        changed=diff.changed,
        time_moves=diff.time_moves,
        room_only_moves=diff.room_only_moves,
        retention_pct=round(diff.retention_pct, 1),
        time_retention_pct=round(diff.time_retention_pct, 1),
        changes=changes_out(instance, result.timetable, diff),
        validation=validation_out(instance, result.timetable),
        quality=quality_out(instance, result.timetable),
        solver=solver_out(result.timetable),
        phase1_seconds=round(result.phase1_seconds, 2),
        phase2_seconds=round(result.phase2_seconds, 2),
        total_seconds=round(result.total_seconds, 2),
        phase1_status=result.phase1_status,
        phase2_status=result.phase2_status,
        minimal_proven=result.minimal_proven,
        stages=stage_models,
        proposal=version_out(proposal),
    )


READABLE_RULES = {
    "max_consecutive": "maximum consecutive teaching hours",
    "max_daily_load": "maximum teaching hours per day",
    "max_weekly_load": "maximum teaching hours per week",
}


def _window(start: str, end: str) -> str:
    if (start, end) == ("09:00", "23:59"):
        return "all day"
    if end == "23:59":
        return f"from {start}"
    return f"{start}–{end}"


def describe_record(rec: ConstraintRecord, inst: Instance) -> str:
    """A one-line reading of a rule, in the terms a coordinator used."""
    if rec.kind in AVAILABILITY_KINDS:
        if rec.kind == FACULTY_UNAVAILABLE:
            f = inst.faculty_by_id.get(rec.target_id or "")
            who = f.name if f else rec.target_id
            verb = "unavailable"
        elif rec.kind == ROOM_UNAVAILABLE:
            r = inst.room_by_id.get(rec.target_id or "")
            who = f"{r.id} ({r.name})" if r else rec.target_id
            verb = "out of use"
        else:
            who = rec.target_id
            verb = "has no classes" if rec.kind == BATCH_UNAVAILABLE else "unavailable"
        if rec.category == TEMPORARY and rec.start_date and rec.end_date:
            if rec.start_date == rec.end_date:
                when = rec.start_date.strftime("%a %d %b %Y")
            else:
                when = (
                    f"{rec.start_date.strftime('%d %b')} – "
                    f"{rec.end_date.strftime('%d %b %Y')}"
                )
        else:
            days = rec.weekdays()
            when = (
                "every weekday"
                if days == [0, 1, 2, 3, 4]
                else ", ".join(DAYS[d] for d in days)
            )
        return f"{who} {verb} {when}, {_window(rec.start_time, rec.end_time)}"
    if rec.kind == RULE:
        f = inst.faculty_by_id.get(rec.target_id or "") if rec.target_id else None
        who = f.name if f else "Every teacher"
        readable = READABLE_RULES.get(rec.rule_field or "", rec.rule_field or "rule")
        return f"{who}: {readable} set to {rec.rule_value}"
    if rec.kind == LOCK:
        s = inst.session_by_id.get(rec.session_id or "")
        what = f"{s.subject_code} {s.subject_name} ({s.batch_id})" if s else rec.session_id
        slot = inst.calendar.by_id.get(rec.lock_timeslot if rec.lock_timeslot is not None else -1)
        where = slot.label if slot else f"slot {rec.lock_timeslot}"
        room = f" in {rec.lock_room}" if rec.lock_room else ""
        return f"{what} locked at {where}{room}"
    return rec.kind


def constraint_out(rec: ConstraintRecord, inst: Instance, today: date) -> ConstraintOut:
    return ConstraintOut(
        id=rec.id or 0,
        category=rec.category,
        kind=rec.kind,
        target_id=rec.target_id,
        summary=describe_record(rec, inst),
        days=list(rec.weekdays()) if rec.kind in AVAILABILITY_KINDS else list(rec.days),
        start_time=rec.start_time,
        end_time=rec.end_time,
        start_date=iso_date(rec.start_date),
        end_date=iso_date(rec.end_date),
        rule_field=rec.rule_field,
        rule_value=rec.rule_value,
        session_id=rec.session_id,
        lock_timeslot=rec.lock_timeslot,
        lock_room=rec.lock_room,
        status=rec.status,
        lifecycle=rec.lifecycle(today),
        reason=rec.reason,
        created_by=rec.created_by,
        created_at=iso(rec.created_at),
        request_id=rec.request_id,
    )


def user_out(user: m.UserRow) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        faculty_id=user.faculty_id,
        batch_id=user.batch_id,
    )
