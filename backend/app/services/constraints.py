"""Base rules and temporary overrides.

BASE rules are set by a coordinator and persist until edited: an institutional
policy ("TE-A has no lectures after 16:00"), a workload cap, a locked session.
Regular faculty availability, room capacity and room type are base data held
on the entities themselves.

TEMPORARY overrides are bounded by dates and normally come from operational
disruptions: leave, a lab under maintenance, an event. An override counts from
the moment it is recorded until its last day, so tomorrow's leave shapes a
repair today. After it ends it stops constraining any future solve -- but
nothing is rewritten automatically. The published timetable stands until a
coordinator previews and approves a change.

A weekly timetable has no dates, so an override is mapped onto the weekdays
its date range covers, with its daily time window, for as long as it lasts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..domain.models import Instance, Lock
from ..solver.disruption import (
    BATCH_UNAVAILABLE,
    FACULTY_UNAVAILABLE,
    ROOM_UNAVAILABLE,
    Disruption,
    RuleChange,
    apply_disruptions,
    apply_rule_changes,
    batch_unavailable,
    faculty_unavailable,
    room_unavailable,
    rule_change,
)

BASE = "BASE"
TEMPORARY = "TEMPORARY"

RULE = "RULE"
LOCK = "LOCK"
AVAILABILITY_KINDS = (FACULTY_UNAVAILABLE, ROOM_UNAVAILABLE, BATCH_UNAVAILABLE)
KINDS = AVAILABILITY_KINDS + (RULE, LOCK)

# Administrative status. Only ACTIVE records constrain the solver.
ACTIVE = "ACTIVE"
PENDING = "PENDING"  # awaiting coordinator approval
REJECTED = "REJECTED"
INACTIVE = "INACTIVE"  # switched off by a coordinator

# Lifecycle, derived from dates rather than stored.
PERMANENT = "PERMANENT"
UPCOMING = "UPCOMING"
IN_EFFECT = "IN_EFFECT"
EXPIRED = "EXPIRED"

WEEKDAYS = (0, 1, 2, 3, 4)


def weekdays_in(start: date, end: date) -> list[int]:
    """Teaching weekdays (Mon=0 .. Fri=4) that a date range touches."""
    if end < start:
        return []
    found: set[int] = set()
    day = start
    # A week covers every weekday, so there is no need to walk further.
    for _ in range(min((end - start).days + 1, 7)):
        if day.weekday() < 5:
            found.add(day.weekday())
        day += timedelta(days=1)
    return sorted(found)


@dataclass(slots=True)
class ConstraintRecord:
    category: str
    kind: str
    target_id: str | None = None
    # BASE availability applies on these weekdays; TEMPORARY derives them
    # from its dates.
    days: tuple[int, ...] = WEEKDAYS
    start_time: str = "09:00"
    end_time: str = "23:59"
    start_date: date | None = None
    end_date: date | None = None
    rule_field: str | None = None
    rule_value: int | None = None
    session_id: str | None = None
    lock_timeslot: int | None = None
    lock_room: str | None = None
    status: str = ACTIVE
    reason: str = ""
    created_by: str = ""
    created_at: datetime | None = None
    request_id: int | None = None
    id: int | None = None

    def lifecycle(self, today: date) -> str:
        if self.category == BASE:
            return PERMANENT
        if self.end_date is not None and today > self.end_date:
            return EXPIRED
        if self.start_date is not None and today < self.start_date:
            return UPCOMING
        return IN_EFFECT

    def considered(self, today: date) -> bool:
        """Whether this record constrains a solve run on `today`."""
        return self.status == ACTIVE and self.lifecycle(today) != EXPIRED

    def weekdays(self) -> list[int]:
        if self.category == TEMPORARY and self.start_date and self.end_date:
            return weekdays_in(self.start_date, self.end_date)
        return sorted(set(self.days))


@dataclass(slots=True)
class Compiled:
    """The solving instance for a given day, and how it was arrived at."""

    instance: Instance
    disruptions: list[Disruption] = field(default_factory=list)
    rule_changes: list[RuleChange] = field(default_factory=list)
    applied: list[ConstraintRecord] = field(default_factory=list)
    # Records that exist but did not apply, each with the reason why --
    # expired, pending, rejected, switched off, or invalid.
    skipped: list[tuple[ConstraintRecord, str]] = field(default_factory=list)


_BUILDERS = {
    FACULTY_UNAVAILABLE: faculty_unavailable,
    ROOM_UNAVAILABLE: room_unavailable,
    BATCH_UNAVAILABLE: batch_unavailable,
}


def disruptions_for(base: Instance, record: ConstraintRecord) -> list[Disruption]:
    """The weekly availability blocks an availability record stands for."""
    builder = _BUILDERS[record.kind]
    out = []
    for day in record.weekdays():
        d = builder(base, record.target_id, day, record.start_time, record.end_time)
        if d.timeslots:
            out.append(d)
    return out


def compile_instance(
    base: Instance, records: list[ConstraintRecord], today: date
) -> Compiled:
    """Apply every record that is in force on `today` to the base instance."""
    disruptions: list[Disruption] = []
    rules: list[RuleChange] = []
    locks: dict[str, Lock] = dict(base.locks)
    applied: list[ConstraintRecord] = []
    skipped: list[tuple[ConstraintRecord, str]] = []

    for rec in records:
        if rec.status != ACTIVE:
            skipped.append((rec, rec.status))
            continue
        if rec.lifecycle(today) == EXPIRED:
            skipped.append((rec, EXPIRED))
            continue
        try:
            if rec.kind in AVAILABILITY_KINDS:
                blocks = disruptions_for(base, rec)
                if not blocks:
                    raise ValueError(
                        f"{rec.target_id}: {rec.start_time}-{rec.end_time} covers "
                        f"no teaching period on the days it names"
                    )
                disruptions.extend(blocks)
            elif rec.kind == RULE:
                rules.append(
                    rule_change(base, rec.rule_field or "", rec.rule_value or 0, rec.target_id)
                )
            elif rec.kind == LOCK:
                if rec.session_id not in base.session_by_id:
                    raise ValueError(f"No session {rec.session_id!r}")
                locks[rec.session_id] = Lock(
                    session_id=rec.session_id,
                    timeslot_id=int(rec.lock_timeslot or 0),
                    room_id=rec.lock_room,
                    reason=rec.reason,
                )
            else:
                raise ValueError(f"Unknown constraint kind {rec.kind!r}")
        except ValueError as exc:
            skipped.append((rec, str(exc)))
            continue
        applied.append(rec)

    instance = apply_rule_changes(apply_disruptions(base, disruptions), rules)
    instance = instance.derive(locks=locks)
    return Compiled(
        instance=instance,
        disruptions=disruptions,
        rule_changes=rules,
        applied=applied,
        skipped=skipped,
    )
