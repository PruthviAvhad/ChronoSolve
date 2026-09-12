"""Constraint-aware explainability.

Answers the two questions a coordinator actually asks:

    "Why did this class move?"        -> why it could not stay where it was
    "Why can't it go there instead?"  -> what blocks a specific alternative

Explanations are derived from the same structured hard-constraint data used to
build the solver model, evaluated against the rest of the current schedule held
fixed. This is deliberately NOT a solver unsatisfiable core: it does not claim
to be a minimal conflicting set, only an accurate report of which rules reject a
given placement.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.models import Instance, Session, Timetable

# Rule keys, kept stable so the UI can group and colour them.
SPAN_DAY_BOUNDARY = "day_boundary"
SPAN_LUNCH = "lunch"
FACULTY_UNAVAILABLE = "faculty_unavailable"
FACULTY_BUSY = "faculty_busy"
BATCH_BUSY = "batch_busy"
BATCH_UNAVAILABLE = "batch_unavailable"
MAX_CONSECUTIVE = "max_consecutive"
DAILY_LOAD = "daily_load"
ROOM_TYPE = "room_type"
ROOM_CAPACITY = "room_capacity"
ROOM_UNAVAILABLE = "room_unavailable"
ROOM_BUSY = "room_busy"
ROOM_CAPABILITY = "room_capability"
ROOM_INACTIVE = "room_inactive"
LOCKED = "locked"
NO_ROOM = "no_room"
ELECTIVE_ROOMS = "elective_rooms"
ELECTIVE_PARALLEL = "elective_parallel"
NOT_FORCED = "not_forced"

# Blockers that no rearrangement of *other* sessions can remove: the rule is a
# property of the session, its teacher, its division or the room itself. The
# rest (someone else is already there, a load cap is reached) can in principle
# be cleared by moving other sessions -- which is what a repair does.
HARD_RULES = frozenset(
    {
        SPAN_DAY_BOUNDARY,
        SPAN_LUNCH,
        FACULTY_UNAVAILABLE,
        BATCH_UNAVAILABLE,
        ROOM_TYPE,
        ROOM_CAPACITY,
        ROOM_UNAVAILABLE,
        ROOM_CAPABILITY,
        ROOM_INACTIVE,
        LOCKED,
        NO_ROOM,
    }
)


@dataclass(frozen=True, slots=True)
class Blocker:
    rule: str
    message: str


@dataclass(slots=True)
class SlotOption:
    timeslot_id: int
    label: str
    feasible: bool
    room_id: str | None = None
    blockers: list[Blocker] = field(default_factory=list)


@dataclass(slots=True)
class Explanation:
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_name: str
    duration: int
    current_label: str | None
    current_room: str | None
    options: list[SlotOption]
    headline: str
    locked: bool = False

    @property
    def feasible_count(self) -> int:
        return sum(1 for o in self.options if o.feasible)


class PlacementProbe:
    """Evaluates candidate placements for one session against a fixed schedule.

    The session under test (and, for an elective option, its parallel siblings)
    is lifted out of the occupancy maps first -- otherwise a session would
    always be reported as blocking itself.
    """

    def __init__(self, instance: Instance, timetable: Timetable, session: Session):
        self.inst = instance
        self.cal = instance.calendar
        self.tt = timetable
        self.session = session
        self.faculty = instance.faculty_by_id[session.faculty_id]
        self.batch = instance.batch_by_id[session.batch_id]
        self.seats = instance.seats_needed(session)
        self.lock = instance.locks.get(session.id)

        # Sessions that move together with this one, and are therefore not
        # obstacles to it.
        self.group = (
            [s for s in instance.sessions if s.elective_group == session.elective_group]
            if session.elective_group
            else [session]
        )
        self.group_ids = {s.id for s in self.group}

        self.faculty_at: dict[int, list[str]] = defaultdict(list)
        self.room_at: dict[tuple[str, int], list[str]] = defaultdict(list)
        self.batch_at: dict[int, list[str]] = defaultdict(list)

        for s in instance.sessions:
            if s.id in self.group_ids:
                continue
            p = timetable.placements.get(s.id)
            if p is None:
                continue
            for k in range(s.duration):
                u = p.timeslot_id + k
                if s.faculty_id == session.faculty_id:
                    self.faculty_at[u].append(s.id)
                self.room_at[(p.room_id, u)].append(s.id)
                if s.batch_id == session.batch_id:
                    self.batch_at[u].append(s.id)

        self.eligible_rooms = [
            r
            for r in instance.rooms
            if r.active
            and r.room_type is session.room_type
            and r.capacity >= self.seats
            and (
                not session.required_capability
                or session.required_capability in r.capabilities
            )
        ]

    # -- helpers -------------------------------------------------------

    def _label(self, session_id: str) -> str:
        s = self.inst.session_by_id[session_id]
        return f"{s.subject_code} ({s.batch_id})"

    def _faculty_day_load(self, day: int) -> set[int]:
        """Periods this faculty is already committed to on `day`."""
        return {
            self.cal.by_id[u].period
            for u in self.faculty_at
            if self.cal.by_id[u].day == day
        }

    # -- slot-level rules ---------------------------------------------

    def slot_blockers(self, start_id: int) -> tuple[list[Blocker], tuple[int, ...]]:
        """Rules that reject a start time regardless of which room is used."""
        out: list[Blocker] = []
        start = self.cal.by_id[start_id]
        dur = self.session.duration

        span = self.cal.span(start_id, dur)
        if span is None:
            if start.period + dur > self.cal.periods:
                out.append(
                    Blocker(
                        SPAN_DAY_BOUNDARY,
                        f"a {dur}-hour block starting {start.label} would run past "
                        f"the end of the teaching day",
                    )
                )
            else:
                out.append(
                    Blocker(
                        SPAN_LUNCH,
                        f"a {dur}-hour block starting {start.label} would cross the "
                        f"protected lunch period",
                    )
                )
            return out, ()

        if self.lock is not None and self.lock.timeslot_id != start_id:
            locked_at = self.cal.by_id[self.lock.timeslot_id].label
            out.append(
                Blocker(LOCKED, f"the coordinator locked this session at {locked_at}")
            )

        busy_faculty = sorted({sid for u in span for sid in self.faculty_at.get(u, [])})
        busy_batch = sorted({sid for u in span for sid in self.batch_at.get(u, [])})

        if any(u in self.faculty.unavailable for u in span):
            out.append(
                Blocker(
                    FACULTY_UNAVAILABLE,
                    f"{self.faculty.name} is marked unavailable at {start.label}",
                )
            )
        if busy_faculty:
            out.append(
                Blocker(
                    FACULTY_BUSY,
                    f"{self.faculty.name} already teaches "
                    f"{', '.join(self._label(s) for s in busy_faculty)} then",
                )
            )
        if any(u in self.batch.unavailable for u in span):
            out.append(
                Blocker(
                    BATCH_UNAVAILABLE,
                    f"{self.session.batch_id} is not available at {start.label}",
                )
            )
        if busy_batch:
            out.append(
                Blocker(
                    BATCH_BUSY,
                    f"{self.session.batch_id} already has "
                    f"{', '.join(self._label(s) for s in busy_batch)} then",
                )
            )

        # Workload rules, evaluated as if the session were placed here.
        periods = self._faculty_day_load(start.day) | {
            self.cal.by_id[u].period for u in span
        }
        if len(periods) > self.faculty.max_daily_load:
            out.append(
                Blocker(
                    DAILY_LOAD,
                    f"{self.faculty.name} would teach {len(periods)} hours that day "
                    f"(limit {self.faculty.max_daily_load})",
                )
            )

        longest = run = 0
        for p in range(self.cal.periods):
            run = run + 1 if p in periods else 0
            longest = max(longest, run)
        if longest > self.faculty.max_consecutive:
            out.append(
                Blocker(
                    MAX_CONSECUTIVE,
                    f"{self.faculty.name} would teach {longest} hours back-to-back "
                    f"(limit {self.faculty.max_consecutive})",
                )
            )

        return out, span

    # -- room-level rules ---------------------------------------------

    def room_blockers(self, room_id: str, span: tuple[int, ...]) -> list[Blocker]:
        room = self.inst.room_by_id[room_id]
        out: list[Blocker] = []

        if self.lock is not None and self.lock.room_id and room.id != self.lock.room_id:
            out.append(
                Blocker(
                    LOCKED, f"the coordinator locked this session to {self.lock.room_id}"
                )
            )
        if not room.active:
            out.append(Blocker(ROOM_INACTIVE, f"{room.id} is out of service"))
        if room.room_type is not self.session.room_type:
            out.append(
                Blocker(
                    ROOM_TYPE,
                    f"{room.id} is a {room.room_type.value.lower()} room but this "
                    f"session needs a {self.session.room_type.value.lower()} room",
                )
            )
        if room.capacity < self.seats:
            out.append(
                Blocker(
                    ROOM_CAPACITY,
                    f"{room.id} seats {room.capacity} but {self.seats} students attend",
                )
            )
        need = self.session.required_capability
        if need and need not in room.capabilities:
            out.append(
                Blocker(ROOM_CAPABILITY, f"{room.id} is not equipped for '{need}'")
            )
        if any(u in room.unavailable for u in span):
            out.append(
                Blocker(ROOM_UNAVAILABLE, f"{room.id} is unavailable at that time")
            )

        occupants = sorted(
            {sid for u in span for sid in self.room_at.get((room.id, u), [])}
        )
        if occupants:
            out.append(
                Blocker(
                    ROOM_BUSY,
                    f"{room.id} is taken by "
                    f"{', '.join(self._label(s) for s in occupants)}",
                )
            )
        return out

    def free_rooms(self, span: tuple[int, ...]) -> list[str]:
        # Tightest fit first, so the suggested room is the best-suited free one.
        ordered = sorted(self.eligible_rooms, key=lambda r: (r.capacity, r.id))
        return [r.id for r in ordered if not self.room_blockers(r.id, span)]

    # -- public --------------------------------------------------------

    def evaluate(self, start_id: int) -> SlotOption:
        label = self.cal.by_id[start_id].label
        blockers, span = self.slot_blockers(start_id)
        if blockers:
            return SlotOption(start_id, label, False, None, blockers)

        free = self.free_rooms(span)
        needed = len(self.group)

        if len(free) >= needed:
            return SlotOption(start_id, label, True, free[0], [])

        if not self.eligible_rooms:
            need = self.session.required_capability
            equipped = f" equipped for '{need}'" if need else ""
            return SlotOption(
                start_id,
                label,
                False,
                None,
                [
                    Blocker(
                        NO_ROOM,
                        f"no {self.session.room_type.value.lower()} room{equipped} "
                        f"in service seats {self.seats} students",
                    )
                ],
            )

        if needed > 1:
            reason = Blocker(
                ELECTIVE_ROOMS,
                f"the {needed} parallel elective options need {needed} suitable "
                f"rooms at the same time; only {len(free)} are free",
            )
        else:
            detail = "; ".join(
                b.message
                for r in self.eligible_rooms[:3]
                for b in self.room_blockers(r.id, span)[:1]
            )
            reason = Blocker(
                NO_ROOM,
                f"all {len(self.eligible_rooms)} suitable rooms are taken or "
                f"unavailable — {detail}",
            )
        return SlotOption(start_id, label, False, None, [reason])


def explain_session(
    instance: Instance, timetable: Timetable, session_id: str
) -> Explanation:
    """Where else could this session go, and what blocks the rest?"""
    session = instance.session_by_id.get(session_id)
    if session is None:
        raise KeyError(f"No session {session_id!r}")

    probe = PlacementProbe(instance, timetable, session)
    current = timetable.placements.get(session_id)
    options = [probe.evaluate(slot.id) for slot in instance.calendar.slots]

    feasible = sum(1 for o in options if o.feasible)
    headline = (
        f"{feasible} of {len(options)} start times can host this session "
        f"with the rest of the timetable unchanged."
    )
    if probe.lock is not None:
        headline += " It is locked by the coordinator."

    return Explanation(
        session_id=session_id,
        subject_code=session.subject_code,
        subject_name=session.subject_name,
        batch_id=session.batch_id,
        faculty_name=instance.faculty_by_id[session.faculty_id].name,
        duration=session.duration,
        current_label=(
            instance.calendar.by_id[current.timeslot_id].label if current else None
        ),
        current_room=current.room_id if current else None,
        options=options,
        headline=headline,
        locked=probe.lock is not None,
    )


def explain_move(
    instance: Instance,
    repaired: Timetable,
    session_id: str,
    original_timeslot: int,
    original_room: str,
) -> list[Blocker]:
    """Why a moved session could not stay where it was published.

    Evaluated against the repaired timetable, so the answer accounts for the
    disruption itself and for anything that took the old slot.
    """
    session = instance.session_by_id[session_id]
    probe = PlacementProbe(instance, repaired, session)

    blockers, span = probe.slot_blockers(original_timeslot)
    if blockers:
        return blockers

    # A parallel elective is dragged by its group: it cannot hold a slot its
    # siblings had to leave, even though nothing blocks it directly.
    if session.elective_group:
        label = instance.calendar.by_id[original_timeslot].label
        moved_siblings = [
            s
            for s in probe.group
            if s.id != session_id
            and s.id in repaired.placements
            and repaired.placements[s.id].timeslot_id != original_timeslot
        ]
        if moved_siblings:
            names = ", ".join(
                f"{s.subject_name} ({s.batch_id})" for s in moved_siblings
            )
            return [
                Blocker(
                    ELECTIVE_PARALLEL,
                    f"this elective must run at the same time as {names}, which "
                    f"could not stay at {label}",
                )
            ]

    if original_room in instance.room_by_id:
        room_reasons = probe.room_blockers(original_room, span)
        if room_reasons:
            return room_reasons

    return [
        Blocker(
            NOT_FORCED,
            "no hard constraint blocks the original placement; the solver reached "
            "an equally minimal repair that happens to place it elsewhere",
        )
    ]
