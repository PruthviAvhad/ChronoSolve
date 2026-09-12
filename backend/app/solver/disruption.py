"""Real-world disruptions to an already-published timetable.

A disruption never edits the published schedule. It produces a *new* problem
instance with tightened availability; the published schedule is then handed to
the repair solver as a reference to stay as close to as possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..domain.models import DAYS, PERIOD_START, Instance, Timetable

FACULTY_UNAVAILABLE = "FACULTY_UNAVAILABLE"
ROOM_UNAVAILABLE = "ROOM_UNAVAILABLE"
BATCH_UNAVAILABLE = "BATCH_UNAVAILABLE"


RULE_FIELDS = ("max_consecutive", "max_daily_load", "max_weekly_load")


@dataclass(frozen=True, slots=True)
class Disruption:
    kind: str
    target_id: str
    timeslots: frozenset[int]
    description: str


@dataclass(frozen=True, slots=True)
class RuleChange:
    """A change to an institutional workload rule rather than to availability.

    Nobody becomes unavailable; the published schedule simply stops being legal
    under the new rule, and repair has to make it legal again with the fewest
    moves it can manage.
    """

    field: str
    value: int
    faculty_id: str | None = None  # None applies the change to everyone
    description: str = ""


@dataclass(frozen=True, slots=True)
class Scenario:
    """A rehearsed what-if: availability changes, rule changes, or both."""

    story: str
    disruptions: list[Disruption] = field(default_factory=list)
    rule_changes: list[RuleChange] = field(default_factory=list)

    def __iter__(self):
        """Unpack as (story, disruptions) for callers that predate rules."""
        return iter((self.story, self.disruptions))


def rule_change(
    instance: Instance, rule: str, value: int, faculty: str | None = None
) -> RuleChange:
    """Build a validated workload-rule change, for everyone or one teacher."""
    if rule not in RULE_FIELDS:
        raise ValueError(f"Unknown rule {rule!r}; expected one of {RULE_FIELDS}")
    if value < 1:
        raise ValueError(f"{rule} must be at least 1")

    target = None
    if faculty:
        target = next(
            (
                f
                for f in instance.faculty
                if f.id == faculty or f.name.lower() == faculty.lower()
            ),
            None,
        )
        if target is None:
            raise ValueError(f"No faculty named {faculty!r}")

    who = target.name if target else "every teacher"
    readable = rule.replace("max_", "maximum ").replace("_", " ")
    return RuleChange(
        field=rule,
        value=value,
        faculty_id=target.id if target else None,
        description=f"{who}: {readable} set to {value}",
    )


def apply_rule_changes(instance: Instance, changes: list[RuleChange]) -> Instance:
    """Return a new instance with the workload rules rewritten."""
    if not changes:
        return instance

    faculty = []
    for f in instance.faculty:
        updates = {
            c.field: c.value
            for c in changes
            if c.faculty_id is None or c.faculty_id == f.id
        }
        faculty.append(replace(f, **updates) if updates else f)

    # derive() carries everything else -- locks included -- across unchanged.
    return instance.derive(faculty=faculty)


def periods_in_window(start_time: str, end_time: str) -> list[int]:
    """Period indices whose teaching hour begins inside [start_time, end_time)."""
    return [p for p, begins in enumerate(PERIOD_START) if start_time <= begins < end_time]


def _slot_ids(instance: Instance, day: int, periods: list[int]) -> frozenset[int]:
    cal = instance.calendar
    return frozenset(
        sl.id for sl in cal.slots_on(day) if sl.period in periods and not sl.is_lunch
    )


def faculty_unavailable(
    instance: Instance, faculty: str, day: int, start_time: str, end_time: str
) -> Disruption:
    """e.g. faculty_unavailable(inst, "Prof. Mehta", 2, "11:00", "14:00")."""
    match = next(
        (
            f
            for f in instance.faculty
            if f.id == faculty or f.name.lower() == faculty.lower()
        ),
        None,
    )
    if match is None:
        raise ValueError(f"No faculty named {faculty!r}")

    slots = _slot_ids(instance, day, periods_in_window(start_time, end_time))
    return Disruption(
        kind=FACULTY_UNAVAILABLE,
        target_id=match.id,
        timeslots=slots,
        description=f"{match.name} unavailable {DAYS[day]} {start_time}-{end_time}",
    )


def room_unavailable(
    instance: Instance,
    room_id: str,
    day: int,
    start_time: str = PERIOD_START[0],
    end_time: str = "23:59",
) -> Disruption:
    """Room or laboratory taken out of service for part or all of a day."""
    if room_id not in instance.room_by_id:
        raise ValueError(f"No room {room_id!r}")

    slots = _slot_ids(instance, day, periods_in_window(start_time, end_time))
    whole_day = start_time == PERIOD_START[0] and end_time == "23:59"
    window = "all day" if whole_day else f"{start_time}-{end_time}"
    return Disruption(
        kind=ROOM_UNAVAILABLE,
        target_id=room_id,
        timeslots=slots,
        description=f"{room_id} unavailable {DAYS[day]} {window}",
    )


def builtin_scenarios(instance: Instance) -> dict[str, Scenario]:
    """Rehearsed demo scenarios, shared by the console demo and the API.

    Each is tuned against the *published* baseline in data/, so the affected
    sessions are real rather than contrived.
    """
    return {
        "faculty": Scenario(
            "Prof. Mehta is unavailable on Friday afternoon",
            [faculty_unavailable(instance, "Prof. Mehta", 4, "12:00", "17:00")],
        ),
        "wednesday": Scenario(
            "Prof. Mehta is unavailable on Wednesday afternoon",
            [faculty_unavailable(instance, "Prof. Mehta", 2, "14:00", "17:00")],
        ),
        "lab": Scenario(
            "Computer Lab 3 is closed for maintenance on Friday",
            [room_unavailable(instance, "CL3", 4)],
        ),
        # Deliberately impossible: shows what the system does when no repair
        # exists, rather than hanging or returning an invalid timetable.
        "leave": Scenario(
            "Prof. Mehta is on leave for the whole week",
            [
                faculty_unavailable(instance, "Prof. Mehta", day, "09:00", "23:59")
                for day in range(instance.calendar.days)
            ],
        ),
        "seminar": Scenario(
            "Second-year divisions attend an industry seminar on Wednesday",
            [
                batch_unavailable(instance, b.id, 2, "14:00", "17:00")
                for b in instance.batches
                if b.id.startswith("SE")
            ],
        ),
        "tighter-hours": Scenario(
            "New policy: nobody teaches more than 2 hours back to back",
            rule_changes=[rule_change(instance, "max_consecutive", 2)],
        ),
        "labs-closed": Scenario(
            "All computer labs are closed on Monday and Tuesday",
            [
                room_unavailable(instance, room.id, day)
                for room in instance.rooms
                if room.room_type.value == "LAB"
                for day in (0, 1)
            ],
        ),
    }


def batch_unavailable(
    instance: Instance, batch: str, day: int, start_time: str, end_time: str
) -> Disruption:
    """A division is away from class entirely -- a seminar, assembly or exam.

    Unlike a room or teacher block, this frees nobody else: other divisions
    carry on, and the rooms and teachers involved become available to them.
    """
    match = next(
        (
            b
            for b in instance.batches
            if b.id.lower() == batch.lower() or b.name.lower() == batch.lower()
        ),
        None,
    )
    if match is None:
        raise ValueError(f"No batch named {batch!r}")

    slots = _slot_ids(instance, day, periods_in_window(start_time, end_time))
    return Disruption(
        kind=BATCH_UNAVAILABLE,
        target_id=match.id,
        timeslots=slots,
        description=f"{match.id} unavailable {DAYS[day]} {start_time}-{end_time}",
    )


def _blocked(disruptions: list[Disruption]) -> tuple[dict, dict, dict]:
    blocked: dict[str, dict[str, set[int]]] = {
        FACULTY_UNAVAILABLE: {},
        ROOM_UNAVAILABLE: {},
        BATCH_UNAVAILABLE: {},
    }
    for d in disruptions:
        blocked[d.kind].setdefault(d.target_id, set()).update(d.timeslots)
    return (
        blocked[FACULTY_UNAVAILABLE],
        blocked[ROOM_UNAVAILABLE],
        blocked[BATCH_UNAVAILABLE],
    )


def apply_disruptions(instance: Instance, disruptions: list[Disruption]) -> Instance:
    """Return a new instance with the disrupted availability applied."""
    blocked_faculty, blocked_rooms, blocked_batches = _blocked(disruptions)

    faculty = [
        replace(f, unavailable=f.unavailable | blocked_faculty[f.id])
        if f.id in blocked_faculty
        else f
        for f in instance.faculty
    ]
    rooms = [
        replace(r, unavailable=r.unavailable | blocked_rooms[r.id])
        if r.id in blocked_rooms
        else r
        for r in instance.rooms
    ]

    batches = [
        replace(b, unavailable=b.unavailable | blocked_batches[b.id])
        if b.id in blocked_batches
        else b
        for b in instance.batches
    ]

    return instance.derive(rooms=rooms, faculty=faculty, batches=batches)


def directly_affected(
    instance: Instance, timetable: Timetable, disruptions: list[Disruption]
) -> list[str]:
    """Sessions the published schedule can no longer honour as-is.

    These are the sessions a coordinator would notice immediately. Everything
    else that moves during repair moves only as a knock-on consequence.
    """
    blocked_faculty, blocked_rooms, blocked_batches = _blocked(disruptions)

    hit: list[str] = []
    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue
        span = {p.timeslot_id + k for k in range(s.duration)}
        if (
            span & blocked_faculty.get(s.faculty_id, set())
            or span & blocked_rooms.get(p.room_id, set())
            or span & blocked_batches.get(s.batch_id, set())
        ):
            hit.append(s.id)
    return hit
