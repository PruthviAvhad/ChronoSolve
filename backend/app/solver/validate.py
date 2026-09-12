"""Independent hard-constraint verification.

This module deliberately shares *no* logic with the CP-SAT builder. It replays a
finished timetable against the raw entity data and counts violations from
scratch. If the model were wrong, this is what would catch it -- which is the
only honest basis for putting "faculty conflicts: 0" on a slide.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.models import Instance, Timetable


@dataclass(frozen=True, slots=True)
class Violation:
    kind: str
    detail: str


@dataclass(slots=True)
class ValidationReport:
    counts: dict[str, int] = field(default_factory=dict)
    violations: list[Violation] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def is_clean(self) -> bool:
        return self.total == 0

    def render(self) -> str:
        lines = [
            f"  {'OK  ' if v == 0 else 'FAIL'} {k:<30} {v}"
            for k, v in self.counts.items()
        ]
        if self.violations:
            lines.append("")
            for v in self.violations[:12]:
                lines.append(f"       - [{v.kind}] {v.detail}")
            if len(self.violations) > 12:
                lines.append(f"       ... {len(self.violations) - 12} more")
        return "\n".join(lines)


def validate(instance: Instance, timetable: Timetable) -> ValidationReport:
    """Recount every hard constraint directly from the placements."""
    inst = instance
    cal = inst.calendar
    report = ValidationReport()
    violations: list[Violation] = []

    counts = {
        "unscheduled_sessions": 0,
        "faculty_conflicts": 0,
        "room_conflicts": 0,
        "batch_conflicts": 0,
        "room_type_violations": 0,
        "capacity_violations": 0,
        "capability_violations": 0,
        "inactive_room_violations": 0,
        "faculty_availability": 0,
        "batch_availability": 0,
        "room_availability": 0,
        "lab_contiguity": 0,
        "lunch_violations": 0,
        "max_consecutive_violations": 0,
        "daily_load_violations": 0,
        "weekly_load_violations": 0,
        "elective_parallel_violations": 0,
        "lock_violations": 0,
    }

    # --- every session placed exactly once -------------------------------
    for s in inst.sessions:
        if s.id not in timetable.placements:
            counts["unscheduled_sessions"] += 1
            violations.append(Violation("unscheduled", f"{s.id} has no placement"))

    faculty_busy: dict[tuple[str, int], list[str]] = defaultdict(list)
    room_busy: dict[tuple[str, int], list[str]] = defaultdict(list)
    batch_busy: dict[tuple[str, int], set[str]] = defaultdict(set)

    for s in inst.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue

        room = inst.room_by_id.get(p.room_id)
        fac = inst.faculty_by_id[s.faculty_id]
        batch = inst.batch_by_id[s.batch_id]

        if room is None:
            violations.append(Violation("unknown_room", f"{s.id} -> {p.room_id}"))
            counts["room_type_violations"] += 1
            continue

        # --- contiguity / lunch / day boundary ---------------------------
        start = cal.by_id.get(p.timeslot_id)
        if start is None:
            counts["lab_contiguity"] += 1
            violations.append(
                Violation("bad_timeslot", f"{s.id} -> slot {p.timeslot_id}")
            )
            continue

        if start.period + s.duration > cal.periods:
            counts["lab_contiguity"] += 1
            violations.append(
                Violation(
                    "day_boundary",
                    f"{s.id} ({s.duration}h) starting {start.label} runs past day end",
                )
            )
            span = tuple(
                start.id + k
                for k in range(min(s.duration, cal.periods - start.period))
            )
        else:
            span = tuple(start.id + k for k in range(s.duration))

        if any(cal.by_id[u].is_lunch for u in span):
            counts["lunch_violations"] += 1
            violations.append(
                Violation("lunch", f"{s.id} occupies protected lunch at {start.label}")
            )

        # --- room suitability -------------------------------------------
        if room.room_type is not s.room_type:
            counts["room_type_violations"] += 1
            violations.append(
                Violation(
                    "room_type",
                    f"{s.id} needs {s.room_type.value}, got {room.id} "
                    f"({room.room_type.value})",
                )
            )

        seats = inst.seats_needed(s)
        if room.capacity < seats:
            counts["capacity_violations"] += 1
            violations.append(
                Violation(
                    "capacity",
                    f"{s.id} needs {seats} seats, {room.id} holds {room.capacity}",
                )
            )

        if s.required_capability and s.required_capability not in room.capabilities:
            counts["capability_violations"] += 1
            violations.append(
                Violation(
                    "capability",
                    f"{s.id} needs a room equipped for "
                    f"'{s.required_capability}'; {room.id} is not",
                )
            )

        if not room.active:
            counts["inactive_room_violations"] += 1
            violations.append(
                Violation(
                    "inactive_room",
                    f"{s.id} is placed in {room.id}, which is out of service",
                )
            )

        # --- availability ------------------------------------------------
        if any(u in fac.unavailable for u in span):
            counts["faculty_availability"] += 1
            violations.append(
                Violation(
                    "faculty_unavailable",
                    f"{fac.name} is unavailable at {start.label} but teaches {s.id}",
                )
            )
        if any(u in batch.unavailable for u in span):
            counts["batch_availability"] += 1
            violations.append(
                Violation(
                    "batch_unavailable",
                    f"{batch.id} is unavailable at {start.label} but has {s.id}",
                )
            )
        if any(u in room.unavailable for u in span):
            counts["room_availability"] += 1
            violations.append(
                Violation(
                    "room_unavailable",
                    f"{room.id} is unavailable at {start.label} but hosts {s.id}",
                )
            )

        for u in span:
            faculty_busy[(s.faculty_id, u)].append(s.id)
            room_busy[(p.room_id, u)].append(s.id)
            batch_busy[(s.batch_id, u)].add(s.elective_group or s.id)

    # --- clashes ---------------------------------------------------------
    for (fid, u), ids in faculty_busy.items():
        if len(ids) > 1:
            counts["faculty_conflicts"] += 1
            violations.append(
                Violation(
                    "faculty_clash",
                    f"{inst.faculty_by_id[fid].name} at {cal.by_id[u].label}: "
                    f"{', '.join(sorted(ids))}",
                )
            )
    for (rid, u), ids in room_busy.items():
        if len(ids) > 1:
            counts["room_conflicts"] += 1
            violations.append(
                Violation(
                    "room_clash",
                    f"{rid} at {cal.by_id[u].label}: {', '.join(sorted(ids))}",
                )
            )
    for (bid, u), units in batch_busy.items():
        if len(units) > 1:
            counts["batch_conflicts"] += 1
            violations.append(
                Violation(
                    "batch_clash",
                    f"{bid} at {cal.by_id[u].label}: {', '.join(sorted(units))}",
                )
            )

    # --- faculty workload rules -----------------------------------------
    for f in inst.faculty:
        weekly = 0
        for d in range(cal.days):
            busy = [
                1 if faculty_busy.get((f.id, sl.id)) else 0 for sl in cal.slots_on(d)
            ]
            daily = sum(busy)
            weekly += daily

            if daily > f.max_daily_load:
                counts["daily_load_violations"] += 1
                violations.append(
                    Violation(
                        "daily_load",
                        f"{f.name} teaches {daily}h on day {d} "
                        f"(cap {f.max_daily_load})",
                    )
                )

            run = longest = 0
            for flag in busy:
                run = run + 1 if flag else 0
                longest = max(longest, run)
            if longest > f.max_consecutive:
                counts["max_consecutive_violations"] += 1
                violations.append(
                    Violation(
                        "consecutive",
                        f"{f.name} teaches {longest} in a row on day {d} "
                        f"(cap {f.max_consecutive})",
                    )
                )

        if weekly > f.max_weekly_load:
            counts["weekly_load_violations"] += 1
            violations.append(
                Violation(
                    "weekly_load",
                    f"{f.name} teaches {weekly}h/week (cap {f.max_weekly_load})",
                )
            )

    # --- elective parallelism -------------------------------------------
    for gid, members in inst.elective_groups().items():
        placed = [timetable.placements.get(s.id) for s in members]
        if any(p is None for p in placed):
            continue
        slots = {p.timeslot_id for p in placed}
        rooms = {p.room_id for p in placed}
        if len(slots) > 1:
            counts["elective_parallel_violations"] += 1
            violations.append(
                Violation(
                    "elective_parallel",
                    f"{gid} options are not simultaneous: slots {sorted(slots)}",
                )
            )
        if len(rooms) < len(placed):
            counts["elective_parallel_violations"] += 1
            violations.append(Violation("elective_room", f"{gid} options share a room"))

    # --- coordinator locks -----------------------------------------------
    # A missing placement is already counted as unscheduled above.
    for sid, lock in inst.locks.items():
        p = timetable.placements.get(sid)
        if p is None or sid not in inst.session_by_id:
            continue
        moved_time = p.timeslot_id != lock.timeslot_id
        moved_room = lock.room_id is not None and p.room_id != lock.room_id
        if moved_time or moved_room:
            counts["lock_violations"] += 1
            where = cal.by_id[lock.timeslot_id].label if lock.timeslot_id in cal.by_id else lock.timeslot_id
            violations.append(
                Violation(
                    "lock",
                    f"{sid} is locked at {where}"
                    f"{f' in {lock.room_id}' if lock.room_id else ''} but placed at "
                    f"{cal.by_id[p.timeslot_id].label} in {p.room_id}",
                )
            )

    report.counts = counts
    report.violations = violations
    return report


def sessions_breaking_load_rules(
    instance: Instance, timetable: Timetable
) -> list[str]:
    """Sessions implicated in a workload rule the timetable no longer satisfies.

    Tightening a rule -- "no more than two hours back to back" -- does not make
    any single placement illegal on its own; it makes a *run* of them illegal.
    This reports the sessions inside those runs, so a coordinator can see what a
    proposed rule change would actually disturb before committing to it.
    """
    cal = instance.calendar
    at: dict[tuple[str, int], list[str]] = defaultdict(list)
    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue
        for k in range(s.duration):
            at[(s.faculty_id, p.timeslot_id + k)].append(s.id)

    offending: set[str] = set()
    for f in instance.faculty:
        weekly: list[str] = []
        for d in range(cal.days):
            busy = [at.get((f.id, sl.id), []) for sl in cal.slots_on(d)]
            on_day = [sid for cell in busy for sid in cell]
            weekly.extend(on_day)

            if len(on_day) > f.max_daily_load:
                offending.update(on_day)

            # A run is measured in teaching hours, exactly as validate() counts
            # it: a two-hour lab is two consecutive hours on its own.
            run: list[str] = []
            hours = 0
            for cell in busy + [[]]:
                if cell:
                    run.extend(cell)
                    hours += 1
                    continue
                if hours > f.max_consecutive:
                    offending.update(run)
                run, hours = [], 0

        if len(weekly) > f.max_weekly_load:
            offending.update(weekly)

    return sorted(offending)
