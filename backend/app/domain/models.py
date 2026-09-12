"""Core domain entities for ChronoSolve.

The atomic scheduling unit is a `Session`: one meeting of one subject, for one
batch, taught by one faculty member, occupying `duration` consecutive periods.
A 2-hour lab is a single Session with duration=2 -- not two linked sessions --
so contiguity is guaranteed by construction rather than by extra constraints.

Every division of every year lives in one `Instance` and draws on one pool of
faculty and rooms. That is deliberate: two independently solved timetables can
each be perfect and still double-book a teacher who serves both years.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class RoomType(str, Enum):
    LECTURE = "LECTURE"
    LAB = "LAB"


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
PERIODS_PER_DAY = 8
PERIOD_START = ("09:00", "10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00")
PERIOD_END = ("10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00")
LUNCH_PERIOD = 4  # 13:00-14:00 is protected campus-wide


@dataclass(frozen=True, slots=True)
class TimeSlot:
    id: int
    day: int
    period: int

    @property
    def is_lunch(self) -> bool:
        return self.period == LUNCH_PERIOD

    @property
    def label(self) -> str:
        return f"{DAYS[self.day]} {PERIOD_START[self.period]}"

    @property
    def long_label(self) -> str:
        return f"{DAYS[self.day]} {PERIOD_START[self.period]}-{PERIOD_END[self.period]}"


class Calendar:
    """The weekly grid. 5 days x 8 periods = 40 timeslots, 5 of them lunch."""

    def __init__(self, days: int = len(DAYS), periods: int = PERIODS_PER_DAY):
        self.days = days
        self.periods = periods
        self.slots: tuple[TimeSlot, ...] = tuple(
            TimeSlot(id=d * periods + p, day=d, period=p)
            for d in range(days)
            for p in range(periods)
        )
        self.by_id: dict[int, TimeSlot] = {s.id: s for s in self.slots}

    def __len__(self) -> int:
        return len(self.slots)

    @property
    def teaching_slots(self) -> tuple[TimeSlot, ...]:
        return tuple(s for s in self.slots if not s.is_lunch)

    def slots_on(self, day: int) -> tuple[TimeSlot, ...]:
        return tuple(s for s in self.slots if s.day == day)

    def span(self, start_id: int, duration: int) -> tuple[int, ...] | None:
        """Timeslot ids a session of `duration` occupies if started at `start_id`.

        Returns None when the block would cross a day boundary or a lunch period,
        which is how "contiguous labs must not cross lunch or day boundaries"
        is enforced -- such placements are never created as variables at all.
        """
        start = self.by_id.get(start_id)
        if start is None:
            return None
        if start.period + duration > self.periods:  # crosses into the next day
            return None
        ids = tuple(start_id + k for k in range(duration))
        if any(self.by_id[i].is_lunch for i in ids):  # crosses/lands on lunch
            return None
        return ids


@dataclass(frozen=True, slots=True)
class Room:
    id: str
    name: str
    capacity: int
    room_type: RoomType
    unavailable: frozenset[int] = field(default_factory=frozenset)
    # Where it is, what it is equipped for, and whether it is in service. An
    # inactive room is never offered to the solver.
    building: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    active: bool = True


@dataclass(frozen=True, slots=True)
class Faculty:
    id: str
    name: str
    unavailable: frozenset[int] = field(default_factory=frozenset)
    max_daily_load: int = 5
    max_weekly_load: int = 18
    max_consecutive: int = 3
    department: str = ""
    # Soft: periods the teacher would rather keep free. Unlike `unavailable`,
    # the solver may still use them, but every use costs the objective.
    preferred_off: frozenset[int] = field(default_factory=frozenset)
    # Subject codes this person is qualified to teach.
    subjects: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Batch:
    id: str
    name: str
    strength: int
    # Periods the division cannot attend class at all -- an assembly, an
    # industry seminar, an exam. Distinct from a room or a teacher being busy:
    # everyone else may carry on as normal.
    unavailable: frozenset[int] = field(default_factory=frozenset)
    # Where the division sits in the institution. Descriptive for the solver:
    # every division shares one faculty and room pool regardless.
    department: str = ""
    program: str = ""
    year: int = 0
    year_label: str = ""
    semester: int = 0


@dataclass(frozen=True, slots=True)
class Session:
    """One scheduled meeting. `elective_group` marks sessions that must run in
    parallel: same timeslot, different rooms, students of the batch split
    between them -- so they consume only one of the batch's slots."""

    id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_id: str
    duration: int = 1
    room_type: RoomType = RoomType.LECTURE
    elective_group: str | None = None
    headcount: int | None = None  # None -> the whole batch; electives seat fewer
    # A lab may need particular equipment ("networking"); only rooms carrying
    # that capability are offered to it.
    required_capability: str | None = None
    # THEORY | TUTORIAL | LAB. Empty means "infer from the room type".
    category: str = ""

    @property
    def is_lab(self) -> bool:
        return self.room_type is RoomType.LAB

    @property
    def kind(self) -> str:
        if self.category:
            return self.category
        return "LAB" if self.is_lab else "THEORY"


@dataclass(frozen=True, slots=True)
class Placement:
    """A session pinned to a starting timeslot and a room."""

    session_id: str
    timeslot_id: int
    room_id: str


@dataclass(frozen=True, slots=True)
class Lock:
    """A coordinator's decision that a session must stay put.

    Enforced as a hard constraint: the solver is offered no other start time,
    and -- when a room is named -- no other room. A lock that the current
    rules cannot honour makes the problem infeasible and is named as the cause.
    """

    session_id: str
    timeslot_id: int
    room_id: str | None = None
    reason: str = ""


@dataclass(slots=True)
class Instance:
    """A complete scheduling problem: entities plus the calendar they live in."""

    name: str
    calendar: Calendar
    rooms: list[Room]
    faculty: list[Faculty]
    batches: list[Batch]
    sessions: list[Session]
    locks: dict[str, Lock] = field(default_factory=dict)

    room_by_id: dict[str, Room] = field(init=False, repr=False)
    faculty_by_id: dict[str, Faculty] = field(init=False, repr=False)
    batch_by_id: dict[str, Batch] = field(init=False, repr=False)
    session_by_id: dict[str, Session] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.room_by_id = {r.id: r for r in self.rooms}
        self.faculty_by_id = {f.id: f for f in self.faculty}
        self.batch_by_id = {b.id: b for b in self.batches}
        self.session_by_id = {s.id: s for s in self.sessions}

    def derive(self, **changes) -> Instance:
        """A copy with some parts replaced; everything else, locks included,
        carries over. Every transformation of an instance goes through here so
        that no field is silently dropped along the way."""
        return replace(self, **changes)

    @property
    def total_contact_hours(self) -> int:
        return sum(s.duration for s in self.sessions)

    def seats_needed(self, session: Session) -> int:
        """Students who must physically fit in the room for this session."""
        if session.headcount is not None:
            return session.headcount
        return self.batch_by_id[session.batch_id].strength

    def elective_groups(self) -> dict[str, list[Session]]:
        groups: dict[str, list[Session]] = {}
        for s in self.sessions:
            if s.elective_group:
                groups.setdefault(s.elective_group, []).append(s)
        return groups

    def describe(self) -> str:
        return (
            f"{self.name}: {len(self.sessions)} sessions "
            f"({self.total_contact_hours} contact hours), "
            f"{len(self.batches)} batches, {len(self.faculty)} faculty, "
            f"{len(self.rooms)} rooms, {len(self.calendar)} timeslots"
        )


@dataclass(frozen=True, slots=True)
class Timetable:
    """A solved schedule plus the solver evidence behind it."""

    placements: dict[str, Placement]
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | UNKNOWN
    objective: float | None = None
    best_bound: float | None = None
    solve_seconds: float = 0.0

    def __len__(self) -> int:
        return len(self.placements)

    @property
    def is_solved(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")
