"""Application service: every scheduling operation, end to end.

Each function loads what it needs through the repository, hands plain domain
objects to the solver, and records the outcome. HTTP never reaches in here and
SQL never leaks out. The rules that matter most are enforced here, not in the
UI: a proposal is published only by explicit approval, an approval is refused
when the timetable it was computed against has since changed, and a request
cannot publish anything by itself.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import date

from sqlalchemy import update
from sqlalchemy.orm import Session as DB

from ..db import models as m
from ..db import repository as repo
from ..db.models import utcnow
from ..domain.models import (
    LUNCH_PERIOD,
    PERIOD_END,
    PERIOD_START,
    Calendar,
    Instance,
    Lock,
    Placement,
    Timetable,
)
from ..progress import ProgressLog
from ..solver.diagnose import diagnose
from ..solver.disruption import (
    FACULTY_UNAVAILABLE,
    Disruption,
    RuleChange,
    apply_disruptions,
    apply_rule_changes,
    directly_affected,
)
from ..solver.engine import SolveOutcome, generate
from ..solver.locks import lock_blockers
from ..solver.metrics import (
    ScheduleDiff,
    diff_schedules,
    faculty_workload,
    room_usage,
    schedule_metrics,
)
from ..solver.model import ObjectiveWeights
from ..solver.moves import MoveCheck, preview_move
from ..solver.options import OptionSet, RepairOption, repair_options
from ..solver.repair import RepairResult, repair
from ..solver.validate import sessions_breaking_load_rules, validate
from .constraints import (
    ACTIVE,
    BASE,
    EXPIRED,
    INACTIVE,
    LOCK,
    PENDING,
    REJECTED,
    RULE,
    TEMPORARY,
    Compiled,
    ConstraintRecord,
    compile_instance,
    disruptions_for,
)

# Free hosting tiers give far less CPU than a laptop, so the solver budget is
# configurable rather than baked in. Defaults suit local development.
SOLVER_WORKERS = int(os.getenv("CHRONOSOLVE_WORKERS", "8"))
GENERATE_LIMIT = float(os.getenv("CHRONOSOLVE_GENERATE_SECONDS", "20"))
REPAIR_LIMIT = float(os.getenv("CHRONOSOLVE_REPAIR_SECONDS", "10"))
OPTION_COUNT = int(os.getenv("CHRONOSOLVE_REPAIR_OPTIONS", "3"))

# Request lifecycle.
DRAFT = "DRAFT"  # options computed, nothing chosen yet
SUBMITTED = "SUBMITTED"  # a teacher chose an option; awaiting the coordinator
APPROVED = "APPROVED"
DECLINED = "REJECTED"
WITHDRAWN = "WITHDRAWN"
STALE = "STALE"  # the published timetable moved on; options must be recomputed
OPEN_REQUESTS = (DRAFT, SUBMITTED, STALE)


class ServiceError(Exception):
    """A refusal with an HTTP status and a reason a person can act on."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def today() -> date:
    """The scheduling date. CHRONOSOLVE_TODAY pins it for rehearsed demos."""
    fixed = os.getenv("CHRONOSOLVE_TODAY")
    return date.fromisoformat(fixed) if fixed else date.today()


# --------------------------------------------------------------------------
# Context: what is true right now
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Context:
    base: Instance  # configuration as stored
    records: list[ConstraintRecord]
    compiled: Compiled  # base + every rule in force today
    version: m.VersionRow  # the published version
    published: Timetable
    published_instance: Instance  # what that version was solved for
    today: date

    @property
    def instance(self) -> Instance:
        return self.compiled.instance


def load_context(db: DB, when: date | None = None) -> Context:
    version = repo.current_version(db)
    if version is None:
        raise ServiceError(503, "No timetable has been published yet.")
    day = when or today()
    base = repo.load_instance(db)
    records = repo.records(db)
    return Context(
        base=base,
        records=records,
        compiled=compile_instance(base, records, day),
        version=version,
        published=repo.version_timetable(db, version),
        published_instance=repo.version_instance(version),
        today=day,
    )


def open_proposal(db: DB, ctx: Context) -> m.VersionRow | None:
    """The coordinator's pending proposal, if it still applies."""
    proposal = repo.open_proposal(db)
    if proposal is None or proposal.source_version_id != ctx.version.id:
        return None
    return proposal


# --------------------------------------------------------------------------
# Turning solver inputs into records
# --------------------------------------------------------------------------


def _runs(slot_ids: frozenset[int], calendar: Calendar) -> list[tuple[int, int, int]]:
    """Contiguous blocked runs as (day, first period, last period).

    A block never contains the lunch period, so lunch does not split a run:
    a 12:00-17:00 block is stored as one window, not two.
    """
    by_day: dict[int, list[int]] = defaultdict(list)
    for sid in sorted(slot_ids):
        slot = calendar.by_id[sid]
        by_day[slot.day].append(slot.period)
    out = []
    for day in sorted(by_day):
        periods = sorted(by_day[day])
        first = prev = periods[0]
        for p in periods[1:]:
            bridged = p == prev + 2 and prev + 1 == LUNCH_PERIOD
            if p == prev + 1 or bridged:
                prev = p
                continue
            out.append((day, first, prev))
            first = prev = p
        out.append((day, first, prev))
    return out


def records_from_changes(
    disruptions: list[Disruption],
    rules: list[RuleChange],
    calendar: Calendar,
    *,
    by: str,
    reason: str,
) -> list[ConstraintRecord]:
    """The base rules a what-if assumed, to be recorded if it is approved."""
    out = []
    for d in disruptions:
        for day, first, last in _runs(d.timeslots, calendar):
            out.append(
                ConstraintRecord(
                    category=BASE,
                    kind=d.kind,
                    target_id=d.target_id,
                    days=(day,),
                    start_time=PERIOD_START[first],
                    end_time=PERIOD_END[last],
                    reason=reason,
                    created_by=by,
                )
            )
    for r in rules:
        out.append(
            ConstraintRecord(
                category=BASE,
                kind=RULE,
                target_id=r.faculty_id,
                rule_field=r.field,
                rule_value=r.value,
                reason=reason,
                created_by=by,
            )
        )
    return out


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def generate_timetable(
    db: DB,
    *,
    weights: ObjectiveWeights,
    time_limit: float,
    publish: bool,
    by: str,
    progress: ProgressLog | None = None,
) -> tuple[SolveOutcome, m.VersionRow | None]:
    track = progress if progress is not None else ProgressLog()
    ctx = load_context(db)
    out = generate(
        ctx.instance,
        weights=weights,
        time_limit=min(time_limit, GENERATE_LIMIT),
        workers=SOLVER_WORKERS,
        progress=track,
    )
    if not out.is_solved:
        raise ServiceError(409, f"No feasible timetable found (status {out.status})")

    repo.set_weights(db, weights)
    repo.discard_open_proposals(db)
    version = None
    if publish:
        with track.stage("publishing", "Publishing the new timetable version") as step:
            diff = diff_schedules(ctx.instance, ctx.published, out.timetable)
            version = repo.add_version(
                db,
                instance=ctx.instance,
                timetable=out.timetable,
                status=repo.PROPOSED,
                label="published-generated",
                created_by=by,
                reason="Generated from scratch",
                source=ctx.version,
                diff=diff,
            )
            repo.publish(db, version)
            db.commit()
            step.note(
                f"version {version.number} published; {diff.changed} of "
                f"{diff.total} sessions differ from version {ctx.version.number}"
            )
    else:
        # Kept as a proposal: a coordinator reviews it and publishes it, or
        # discards it. Nothing goes live from here.
        version = repo.add_version(
            db,
            instance=ctx.instance,
            timetable=out.timetable,
            status=repo.PROPOSED,
            label="proposed-generated",
            created_by=by,
            reason="Generated from scratch, awaiting review",
            source=ctx.version,
            diff=diff_schedules(ctx.instance, ctx.published, out.timetable),
        )
    db.commit()
    return out, version


# --------------------------------------------------------------------------
# What-if, re-optimisation and approval
# --------------------------------------------------------------------------


@dataclass(slots=True)
class WhatIf:
    story: str
    descriptions: list[str]
    disrupted: Instance
    affected: list[str]
    result: RepairResult
    proposal: m.VersionRow | None
    diff: ScheduleDiff | None = None


def run_what_if(
    db: DB,
    ctx: Context,
    *,
    story: str,
    disruptions: list[Disruption],
    rules: list[RuleChange],
    weights: ObjectiveWeights,
    phase1_limit: float,
    phase2_limit: float,
    by: str,
    progress: ProgressLog | None = None,
) -> WhatIf:
    """Preview a change as a minimum-disruption repair. Publishes nothing.

    A solvable result is stored as a PROPOSED version; the rules it assumed are
    kept with it and become constraint records only if it is approved.
    """
    disrupted = apply_rule_changes(apply_disruptions(ctx.instance, disruptions), rules)
    # A tightened rule breaks no single placement; it makes a *run* illegal.
    # Both kinds of impact count as directly affected.
    affected = sorted(
        set(directly_affected(ctx.instance, ctx.published, disruptions))
        | set(sessions_breaking_load_rules(disrupted, ctx.published))
    )
    result = repair(
        disrupted,
        ctx.published,
        disruptions,
        weights=weights,
        phase1_limit=min(phase1_limit, REPAIR_LIMIT),
        phase2_limit=min(phase2_limit, REPAIR_LIMIT),
        workers=SOLVER_WORKERS,
        progress=progress,
    )
    repo.discard_open_proposals(db)
    proposal = None
    if result.solved and result.diff is not None:
        proposal = repo.add_version(
            db,
            instance=disrupted,
            timetable=result.timetable,
            status=repo.PROPOSED,
            label="proposed-repair",
            created_by=by,
            reason=story,
            source=ctx.version,
            diff=result.diff,
            pending_rules=records_from_changes(
                disruptions, rules, ctx.instance.calendar, by=by, reason=story
            ),
        )
    db.commit()
    return WhatIf(
        story=story,
        descriptions=[d.description for d in disruptions] + [r.description for r in rules],
        disrupted=disrupted,
        affected=affected,
        result=result,
        proposal=proposal,
    )


def apply_proposal(db: DB, *, by: str) -> m.VersionRow:
    """Publish the pending proposal -- the only way a what-if goes live."""
    current = repo.current_version(db)
    proposal = repo.open_proposal(db)
    if proposal is None:
        raise ServiceError(400, "No pending repair to apply")
    if current is not None and proposal.source_version_id != current.id:
        proposal.status = repo.STALE
        db.commit()
        raise ServiceError(
            409,
            "The published timetable changed after this proposal was made. "
            "Run the what-if again.",
        )
    for rec in repo.version_pending_rules(proposal):
        if rec.kind == LOCK and rec.session_id:
            _deactivate_locks(db, rec.session_id)
        repo.add_record(db, replace(rec, status=ACTIVE, created_by=by))
    if proposal.label == "proposed-generated":
        proposal.label = "published-generated"
    elif proposal.label.startswith("proposed"):
        proposal.label = "published-repaired"
    repo.publish(db, proposal)
    db.commit()
    return proposal


def discard_proposal(db: DB) -> None:
    repo.discard_open_proposals(db)
    db.commit()


# --------------------------------------------------------------------------
# Locks and coordinator moves
# --------------------------------------------------------------------------


def _deactivate_locks(db: DB, session_id: str) -> int:
    ids = [
        r.id
        for r in repo.record_rows(db)
        if r.kind == LOCK and r.session_id == session_id and r.status == ACTIVE
    ]
    repo.set_records_status(db, ids, INACTIVE)
    return len(ids)


def lock_session(
    db: DB, *, session_id: str, keep_room: bool, reason: str, by: str
) -> m.ConstraintRow:
    """Pin a session where it is published. Enforced as a hard constraint."""
    ctx = load_context(db)
    session = ctx.instance.session_by_id.get(session_id)
    if session is None:
        raise ServiceError(404, f"No session {session_id!r}")
    placed = ctx.published.placements.get(session_id)
    if placed is None:
        raise ServiceError(409, f"{session_id} is not in the published timetable")
    lock = Lock(session_id, placed.timeslot_id, placed.room_id if keep_room else None)
    others = {k: v for k, v in ctx.instance.locks.items() if k != session_id}
    problems = lock_blockers(ctx.instance.derive(locks=others), lock)
    if problems:
        raise ServiceError(409, "Cannot lock here: " + "; ".join(problems))
    _deactivate_locks(db, session_id)
    row = repo.add_record(
        db,
        ConstraintRecord(
            category=BASE,
            kind=LOCK,
            session_id=session_id,
            lock_timeslot=placed.timeslot_id,
            lock_room=lock.room_id,
            reason=reason or "Locked by the coordinator",
            created_by=by,
        ),
    )
    db.commit()
    return row


def unlock_session(db: DB, session_id: str) -> int:
    count = _deactivate_locks(db, session_id)
    if count == 0:
        raise ServiceError(404, f"{session_id} is not locked")
    db.commit()
    return count


@dataclass(slots=True)
class MovePreview:
    check: MoveCheck
    story: str
    result: RepairResult | None
    instance: Instance
    proposal: m.VersionRow | None


def run_move_preview(
    db: DB,
    ctx: Context,
    *,
    session_id: str,
    timeslot_id: int,
    room_id: str | None,
    weights: ObjectiveWeights,
    by: str,
    progress: ProgressLog | None = None,
) -> MovePreview:
    """Check a coordinator's move; if allowed, repair around it as a lock."""
    try:
        check, result, pinned = preview_move(
            ctx.instance,
            ctx.published,
            session_id,
            timeslot_id,
            room_id,
            weights=weights,
            phase1_limit=REPAIR_LIMIT,
            phase2_limit=min(3.0, REPAIR_LIMIT),
            workers=SOLVER_WORKERS,
            progress=progress,
        )
    except KeyError as exc:
        raise ServiceError(404, str(exc)) from exc
    except ValueError as exc:
        raise ServiceError(400, str(exc)) from exc

    s = ctx.instance.session_by_id[session_id]
    room = f" in {room_id}" if room_id else ""
    story = f"Coordinator move: {s.subject_code} ({s.batch_id}) to {check.target_label}{room}"
    proposal = None
    if result is not None:
        repo.discard_open_proposals(db)
        if result.solved and result.diff is not None:
            proposal = repo.add_version(
                db,
                instance=pinned,
                timetable=result.timetable,
                status=repo.PROPOSED,
                label="proposed-move",
                created_by=by,
                reason=story,
                source=ctx.version,
                diff=result.diff,
                pending_rules=[
                    ConstraintRecord(
                        category=BASE,
                        kind=LOCK,
                        session_id=session_id,
                        lock_timeslot=timeslot_id,
                        lock_room=room_id,
                        reason="Placed by the coordinator",
                        created_by=by,
                    )
                ],
            )
        db.commit()
    return MovePreview(check=check, story=story, result=result, instance=pinned, proposal=proposal)


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def create_constraint(db: DB, rec: ConstraintRecord) -> m.ConstraintRow:
    """Record a base rule or temporary override after checking it applies."""
    ctx = load_context(db)
    if rec.category == TEMPORARY and not (rec.start_date and rec.end_date):
        raise ServiceError(400, "A temporary override needs a start and end date.")
    if rec.start_date and rec.end_date and rec.end_date < rec.start_date:
        raise ServiceError(400, "The override ends before it starts.")
    trial = compile_instance(ctx.base, [replace(rec, status=ACTIVE)], ctx.today)
    if trial.skipped:
        raise ServiceError(400, trial.skipped[0][1])
    row = repo.add_record(db, rec)
    db.commit()
    return row


def set_constraint_status(db: DB, record_id: int, status: str) -> m.ConstraintRow:
    row = db.get(m.ConstraintRow, record_id)
    if row is None:
        raise ServiceError(404, f"No rule {record_id}")
    row.status = status
    db.commit()
    return row


def delete_constraint(db: DB, record_id: int) -> None:
    row = db.get(m.ConstraintRow, record_id)
    if row is None:
        raise ServiceError(404, f"No rule {record_id}")
    if row.request_id is not None:
        raise ServiceError(409, "This override belongs to a teacher request; decide the request instead.")
    db.delete(row)
    db.commit()


def restore_preview(
    db: DB,
    record_id: int,
    *,
    weights: ObjectiveWeights,
    by: str,
    progress: ProgressLog | None = None,
) -> WhatIf:
    """After an override ends, preview returning towards the timetable it replaced.

    Nothing is reverted automatically. This is the smallest change that brings
    the timetable back towards the version published before the override,
    under the rules in force today; it becomes a proposal like any other.
    """
    ctx = load_context(db)
    row = db.get(m.ConstraintRow, record_id)
    if row is None:
        raise ServiceError(404, f"No rule {record_id}")
    rec = repo.record_from_row(row)
    if rec.category != TEMPORARY:
        raise ServiceError(409, "Only a temporary override ends; this rule is permanent.")
    if rec.lifecycle(ctx.today) != EXPIRED:
        raise ServiceError(
            409, f"This override is still in force until {rec.end_date}. Nothing to restore yet."
        )
    produced = None
    if rec.request_id is not None:
        request = repo.request_by_id(db, rec.request_id)
        if request is not None and request.result_version_id is not None:
            produced = repo.version_by_id(db, request.result_version_id)
    original = (
        repo.version_by_id(db, produced.source_version_id)
        if produced is not None and produced.source_version_id is not None
        else None
    )
    if original is None:
        raise ServiceError(
            409,
            "No timetable from before this override is recorded, so there is "
            "nothing to restore towards.",
        )

    target = repo.version_timetable(db, original)
    result = repair(
        ctx.instance,
        target,
        None,
        weights=weights,
        phase1_limit=REPAIR_LIMIT,
        phase2_limit=min(3.0, REPAIR_LIMIT),
        workers=SOLVER_WORKERS,
        progress=progress,
    )
    story = (
        f"Restore towards version {original.number} now that "
        f"'{rec.reason or 'the override'}' has ended"
    )
    repo.discard_open_proposals(db)
    proposal = None
    diff_now = None
    if result.solved and result.timetable is not None:
        # What people would actually see change: against today's timetable.
        diff_now = diff_schedules(ctx.instance, ctx.published, result.timetable)
        proposal = repo.add_version(
            db,
            instance=ctx.instance,
            timetable=result.timetable,
            status=repo.PROPOSED,
            label="proposed-restoration",
            created_by=by,
            reason=story,
            source=ctx.version,
            diff=diff_now,
        )
    db.commit()
    return WhatIf(
        story=story,
        descriptions=[f"override #{record_id} ended on {rec.end_date}"],
        disrupted=ctx.instance,
        affected=[],
        result=result,
        proposal=proposal,
        diff=diff_now,
    )


# --------------------------------------------------------------------------
# Teacher requests
# --------------------------------------------------------------------------


def _request(db: DB, request_id: int) -> m.RequestRow:
    row = repo.request_by_id(db, request_id)
    if row is None:
        raise ServiceError(404, f"No request {request_id}")
    return row


def _override(db: DB, row: m.RequestRow) -> ConstraintRecord:
    crow = db.get(m.ConstraintRow, row.constraint_id) if row.constraint_id else None
    if crow is None:
        raise ServiceError(409, f"Request {row.id} has lost its unavailability record")
    return repo.record_from_row(crow)


def create_request(
    db: DB,
    *,
    faculty_id: str,
    start_date: date,
    end_date: date,
    start_time: str,
    end_time: str,
    reason: str,
    by: str,
) -> m.RequestRow:
    """Record a teacher's temporary unavailability as a pending override."""
    ctx = load_context(db)
    if faculty_id not in ctx.base.faculty_by_id:
        raise ServiceError(404, f"No faculty {faculty_id!r}")
    if end_date < start_date:
        raise ServiceError(400, "The unavailability ends before it starts.")
    if end_date < ctx.today:
        raise ServiceError(400, "That period is already over.")
    if start_time >= end_time:
        raise ServiceError(400, "The time window ends before it starts.")

    rec = ConstraintRecord(
        category=TEMPORARY,
        kind=FACULTY_UNAVAILABLE,
        target_id=faculty_id,
        start_time=start_time,
        end_time=end_time,
        start_date=start_date,
        end_date=end_date,
        status=PENDING,
        reason=reason,
        created_by=by,
    )
    if not disruptions_for(ctx.instance, rec):
        raise ServiceError(
            400,
            f"{start_date} to {end_date}, {start_time}-{end_time}, covers no "
            f"teaching period, so there is nothing to reschedule.",
        )
    row = repo.add_request(
        db,
        faculty_id=faculty_id,
        created_by=by,
        status=DRAFT,
        reason=reason,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        base_version_id=ctx.version.id,
    )
    rec.request_id = row.id
    row.constraint_id = repo.add_record(db, rec).id
    db.commit()
    return row


def _option_payload(o: RepairOption, inst: Instance) -> dict:
    from ..schemas import changes_out  # local: schemas imports this package's peers

    d = o.result.diff
    tt = o.result.timetable
    assert d is not None and tt is not None
    return {
        "rank": o.rank,
        "label": o.label,
        "recommended": o.recommended,
        "moves": [{**asdict(mv), "moved_time": mv.moved_time} for mv in o.affected_moves],
        "knock_on": o.knock_on,
        "changed": d.changed,
        "unchanged": d.unchanged,
        "total": d.total,
        "time_moves": d.time_moves,
        "room_only_moves": d.room_only_moves,
        "retention_pct": round(d.retention_pct, 1),
        "time_retention_pct": round(d.time_retention_pct, 1),
        "hard_violations": o.hard_violations,
        "soft_cost": o.soft_cost,
        "quality": asdict(o.quality),
        "quality_delta": o.quality_delta,
        "status": o.result.status,
        "objective": tt.objective,
        "phase1_status": o.result.phase1_status,
        "phase2_status": o.result.phase2_status,
        "minimal_proven": o.result.minimal_proven,
        "solve_seconds": round(o.result.total_seconds, 2),
        "changes": [c.model_dump() for c in changes_out(inst, tt, d)],
        "placements": [[p.session_id, p.timeslot_id, p.room_id] for p in tt.placements.values()],
    }


def _failure_payload(result: RepairResult) -> dict:
    from ..schemas import diagnosis_out

    diag = diagnosis_out(result.diagnosis)
    return {
        "status": result.status,
        "reason": result.reason,
        "diagnosis": diag.model_dump() if diag else None,
    }


def compute_options(
    db: DB,
    request_id: int,
    *,
    weights: ObjectiveWeights | None = None,
    progress: ProgressLog | None = None,
) -> m.RequestRow:
    """Find ranked repair options for a request against today's timetable."""
    row = _request(db, request_id)
    if row.status not in (DRAFT, STALE):
        raise ServiceError(
            409, f"Request {row.id} is {row.status.lower()}; its options can no longer change."
        )
    ctx = load_context(db)
    rec = replace(_override(db, row), status=ACTIVE)
    blocks = disruptions_for(ctx.instance, rec)
    disrupted = apply_disruptions(ctx.instance, blocks)
    affected = directly_affected(ctx.instance, ctx.published, blocks)
    found: OptionSet = repair_options(
        disrupted,
        ctx.published,
        blocks,
        affected=affected,
        count=OPTION_COUNT,
        weights=weights or repo.get_weights(db),
        phase1_limit=min(6.0, REPAIR_LIMIT),
        phase2_limit=min(2.0, REPAIR_LIMIT),
        workers=SOLVER_WORKERS,
        progress=progress,
    )
    from ..schemas import affected_out

    row.affected_json = json.dumps(
        [a.model_dump() for a in affected_out(ctx.instance, ctx.published, affected)]
    )
    row.options_json = json.dumps(
        {
            "options": [_option_payload(o, disrupted) for o in found.options],
            "failure": _failure_payload(found.failure) if found.failure else None,
        }
    )
    row.base_version_id = ctx.version.id
    row.status = DRAFT
    row.selected_rank = None
    row.auto_selected = False
    db.commit()
    return row


def request_options(row: m.RequestRow) -> tuple[list[dict], dict | None]:
    if not row.options_json:
        return [], None
    data = json.loads(row.options_json)
    return data.get("options", []), data.get("failure")


def _option(row: m.RequestRow, rank: int | None = None) -> dict:
    options, _ = request_options(row)
    wanted = rank or row.selected_rank or 1
    for option in options:
        if option["rank"] == wanted:
            return option
    raise ServiceError(404, f"Request {row.id} has no option {wanted}")


def submit_request(db: DB, request_id: int, *, rank: int | None, auto: bool) -> m.RequestRow:
    """A teacher picks an option -- or Auto-select Best -- for approval.

    Auto-select Best is the top-ranked option: the solver's own lexicographic
    optimum, not a heuristic pick.
    """
    row = _request(db, request_id)
    if row.status != DRAFT:
        raise ServiceError(409, f"Request {row.id} is {row.status.lower()} and cannot be submitted.")
    options, failure = request_options(row)
    if not options:
        raise ServiceError(
            409,
            "There is no valid repair to submit"
            + (f": {failure['reason']}" if failure and failure.get("reason") else "."),
        )
    chosen = 1 if auto or rank is None else rank
    _option(row, chosen)  # raises if it does not exist
    row.selected_rank = chosen
    row.auto_selected = auto or rank is None
    row.status = SUBMITTED
    row.submitted_at = utcnow()
    db.commit()
    return row


def _placements(option: dict) -> dict[str, Placement]:
    return {sid: Placement(sid, t, r) for sid, t, r in option["placements"]}


def request_preview(
    db: DB, row: m.RequestRow, rank: int | None = None
) -> tuple[Context, Instance, Timetable, ScheduleDiff, dict]:
    """The timetable a request option would publish, against today's."""
    ctx = load_context(db)
    option = _option(row, rank)
    rec = replace(_override(db, row), status=ACTIVE)
    disrupted = apply_disruptions(ctx.instance, disruptions_for(ctx.instance, rec))
    tt = Timetable(
        placements=_placements(option),
        status=option.get("status", "FEASIBLE"),
        objective=option.get("objective"),
    )
    return ctx, disrupted, tt, diff_schedules(disrupted, ctx.published, tt), option


def approve_request(db: DB, request_id: int, *, by: str, note: str = "") -> m.VersionRow:
    """Publish the chosen repair. Only a coordinator reaches this."""
    row = _request(db, request_id)
    if row.status != SUBMITTED:
        raise ServiceError(
            409, f"Only a submitted request can be approved; this one is {row.status.lower()}."
        )
    ctx = load_context(db)
    if row.base_version_id != ctx.version.id:
        row.status = STALE
        db.commit()
        raise ServiceError(
            409,
            "The published timetable changed after these options were computed. "
            "Recompute the options, then approve.",
        )
    option = _option(row)
    crow = db.get(m.ConstraintRow, row.constraint_id)
    crow.status = ACTIVE
    db.flush()
    after = load_context(db)  # now includes the teacher's unavailability

    tt = Timetable(
        placements=_placements(option),
        status=option.get("status", "FEASIBLE"),
        objective=option.get("objective"),
    )
    check = validate(after.instance, tt)
    if not check.is_clean:
        db.rollback()
        raise ServiceError(
            409,
            f"The chosen repair no longer satisfies every rule ({check.total} "
            f"violations). Recompute the options.",
        )
    diff = diff_schedules(after.instance, ctx.published, tt)
    teacher = after.base.faculty_by_id.get(row.faculty_id)
    name = teacher.name if teacher else row.faculty_id
    version = repo.add_version(
        db,
        instance=after.instance,
        timetable=tt,
        status=repo.PROPOSED,
        label=f"published-request-{row.id}",
        created_by=by,
        reason=f"{name} unavailable {window_label(row)}"
        + (f": {row.reason}" if row.reason else ""),
        source=ctx.version,
        diff=diff,
        request_id=row.id,
        effective_until=row.end_date,
    )
    repo.publish(db, version)
    row.status = APPROVED
    row.decided_by = by
    row.decided_at = utcnow()
    row.decision_note = note
    row.result_version_id = version.id
    db.commit()
    return version


def reject_request(db: DB, request_id: int, *, by: str, note: str = "") -> m.RequestRow:
    row = _request(db, request_id)
    if row.status not in OPEN_REQUESTS:
        raise ServiceError(409, f"Request {row.id} is already {row.status.lower()}.")
    row.status = DECLINED
    row.decided_by = by
    row.decided_at = utcnow()
    row.decision_note = note
    if row.constraint_id:
        repo.set_records_status(db, [row.constraint_id], REJECTED)
    db.commit()
    return row


def withdraw_request(db: DB, request_id: int, *, faculty_id: str) -> m.RequestRow:
    row = _request(db, request_id)
    if row.faculty_id != faculty_id:
        raise ServiceError(403, "You can only withdraw your own requests.")
    if row.status not in OPEN_REQUESTS:
        raise ServiceError(409, f"Request {row.id} is already {row.status.lower()}.")
    row.status = WITHDRAWN
    if row.constraint_id:
        repo.set_records_status(db, [row.constraint_id], INACTIVE)
    db.commit()
    return row


def window_label(row: m.RequestRow) -> str:
    if row.start_date == row.end_date:
        day = row.start_date.strftime("%a %d %b %Y")
    else:
        day = f"{row.start_date.strftime('%d %b')} – {row.end_date.strftime('%d %b %Y')}"
    return f"{day}, {row.start_time}–{row.end_time}"


def is_stale(row: m.RequestRow, current: m.VersionRow | None) -> bool:
    return row.status in (DRAFT, SUBMITTED, STALE) and (
        row.status == STALE
        or (current is not None and row.base_version_id != current.id)
    )


# --------------------------------------------------------------------------
# Import and restore
# --------------------------------------------------------------------------


def import_department(
    db: DB,
    instance: Instance,
    *,
    weights: ObjectiveWeights,
    time_limit: float,
    by: str,
    filename: str,
) -> m.VersionRow:
    """Replace the whole configuration from a workbook, then solve and publish."""
    out = generate(
        instance,
        weights=weights,
        time_limit=min(time_limit, GENERATE_LIMIT),
        workers=SOLVER_WORKERS,
    )
    if not out.is_solved:
        diagnosis = diagnose(instance)
        detail = "; ".join(f.message for f in diagnosis.blocking) or (
            f"no feasible timetable found (status {out.status})"
        )
        raise ServiceError(409, detail)

    from ..db.seed import ensure_demo_users
    from .auth import demo_login_enabled

    current = repo.current_version(db)
    repo.discard_open_proposals(db)
    # Existing rules may name rooms or teachers the new data no longer has.
    db.execute(
        update(m.ConstraintRow)
        .where(m.ConstraintRow.status.in_((ACTIVE, PENDING)))
        .values(status=INACTIVE)
    )
    repo.replace_config(db, instance)
    version = repo.add_version(
        db,
        instance=instance,
        timetable=out.timetable,
        status=repo.PROPOSED,
        label="published-imported",
        created_by=by,
        reason=f"Imported {filename}",
        source=current,
    )
    repo.publish(db, version)
    if demo_login_enabled():
        ensure_demo_users(db, instance)
    db.commit()
    return version


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------


def structure(ctx: Context) -> dict:
    """Department -> programme -> year -> semester -> division, with the
    resources the years share."""
    tree: dict = {}
    counts: dict[str, tuple[int, int]] = {}
    for s in ctx.base.sessions:
        n, h = counts.get(s.batch_id, (0, 0))
        counts[s.batch_id] = (n + 1, h + s.duration)
    for b in ctx.base.batches:
        dept = tree.setdefault(b.department or "Unassigned", {})
        prog = dept.setdefault(b.program or b.department or "Unassigned", {})
        year = prog.setdefault((b.year, b.year_label or f"Year {b.year}"), {})
        sem = year.setdefault(b.semester, [])
        n, h = counts.get(b.id, (0, 0))
        sem.append(
            {"id": b.id, "name": b.name, "strength": b.strength, "sessions": n, "contact_hours": h}
        )

    years_of_faculty: dict[str, set[int]] = defaultdict(set)
    years_of_room: dict[str, set[int]] = defaultdict(set)
    for s in ctx.instance.sessions:
        batch = ctx.instance.batch_by_id[s.batch_id]
        years_of_faculty[s.faculty_id].add(batch.year)
        p = ctx.published.placements.get(s.id)
        if p is not None:
            years_of_room[p.room_id].add(batch.year)

    return {
        "departments": [
            {
                "name": dept,
                "programs": [
                    {
                        "name": prog,
                        "years": [
                            {
                                "number": number,
                                "label": label,
                                "semesters": [
                                    {"number": sem, "batches": batches}
                                    for sem, batches in sorted(semesters.items())
                                ],
                            }
                            for (number, label), semesters in sorted(years.items())
                        ],
                    }
                    for prog, years in programs.items()
                ],
            }
            for dept, programs in tree.items()
        ],
        "shared_faculty": sum(1 for ys in years_of_faculty.values() if len(ys) > 1),
        "shared_rooms": sum(1 for ys in years_of_room.values() if len(ys) > 1),
        "faculty": len(ctx.base.faculty),
        "rooms": len(ctx.base.rooms),
        "labs": sum(1 for r in ctx.base.rooms if r.room_type.value == "LAB"),
    }


def analytics(db: DB, ctx: Context) -> dict:
    """Every figure recomputed from the published timetable and today's rules."""
    report = validate(ctx.instance, ctx.published)
    quality = schedule_metrics(ctx.instance, ctx.published)
    versions = repo.list_versions(db, 30)
    requests = repo.list_requests(db)
    temporary = [r for r in ctx.records if r.category == TEMPORARY and r.status == ACTIVE]
    return {
        "validation": {
            "total": report.total,
            "counts": report.counts,
            "families": len(report.counts),
        },
        "quality": asdict(quality),
        "solver": {
            "status": ctx.published.status,
            "objective": ctx.published.objective,
            "best_bound": ctx.published.best_bound,
            "solve_seconds": round(ctx.published.solve_seconds, 2),
        },
        "totals": {
            "sessions": len(ctx.instance.sessions),
            "scheduled": sum(
                1 for s in ctx.instance.sessions if s.id in ctx.published.placements
            ),
            "contact_hours": ctx.instance.total_contact_hours,
            "locked": len(ctx.instance.locks),
        },
        "faculty": faculty_workload(ctx.instance, ctx.published),
        "rooms": room_usage(ctx.instance, ctx.published),
        "versions": [
            {
                "number": v.number,
                "label": v.label,
                "status": v.status,
                "changed": v.changed_count,
                "retention_pct": v.retention_pct,
                "created_at": v.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            for v in versions
            if v.status in (repo.PUBLISHED, repo.SUPERSEDED)
        ],
        "requests": dict(Counter(r.status for r in requests)),
        "overrides": dict(Counter(r.lifecycle(ctx.today) for r in temporary)),
    }
