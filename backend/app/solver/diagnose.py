"""Infeasibility diagnosis.

When no valid timetable exists, returning "INFEASIBLE" is useless to a
coordinator. This module runs targeted counting checks over the rule categories
and reports which ones cannot be satisfied, with concrete relaxations.

Every check here is a *necessary condition*: if it fails, no timetable can
exist, and the finding is stated as fact. Passing every check does not prove
feasibility -- the interaction between rules can still be unsatisfiable -- so a
clean report says only that no single-category cause was found. That distinction
is kept explicit rather than glossed over.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.models import Instance, RoomType, Session
from .locks import lock_blockers

BLOCKING = "blocking"  # proven impossible
TIGHT = "tight"  # satisfiable but with no slack; a likely culprit

# Root causes first: a faculty member being unavailable all week *produces* the
# stranded sessions and the elective clash, so leading with the budget checks
# gives a coordinator the one line they can act on.
CATEGORY_ORDER = (
    # A lock is a coordinator's own decision, so when it is the conflict it is
    # the most actionable line of all: unlock one session.
    "Locked sessions",
    "Faculty availability",
    "Division availability",
    "Weekly teaching load",
    "Daily teaching load",
    "Division timetable capacity",
    "Laboratory room supply",
    "Lecture room supply",
    "Sessions with no legal placement",
    "Parallel electives",
)


@dataclass(frozen=True, slots=True)
class Finding:
    category: str
    severity: str
    message: str
    suggestions: tuple[str, ...] = ()


@dataclass(slots=True)
class DiagnosisReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCKING]

    @property
    def proven_infeasible(self) -> bool:
        return bool(self.blocking)

    @property
    def headline(self) -> str:
        if self.blocking:
            n = len(self.blocking)
            return (
                f"{n} rule {'category' if n == 1 else 'categories'} cannot be "
                f"satisfied. No timetable exists until one of them is relaxed."
            )
        if self.findings:
            return (
                "No single rule category is provably impossible, but the "
                "categories below have no slack and are the likely cause."
            )
        return (
            "No single-category cause found. The conflict comes from the "
            "interaction of several rules rather than one of them alone."
        )

    def render(self) -> str:
        lines = [f"  {self.headline}", ""]
        for f in self.findings:
            tag = "BLOCKING" if f.severity == BLOCKING else "tight   "
            lines.append(f"  [{tag}] {f.category}")
            lines.append(f"      {f.message}")
            for s in f.suggestions:
                lines.append(f"      -> {s}")
            lines.append("")
        return "\n".join(lines)


def _legal_starts(instance: Instance, session: Session) -> list[int]:
    """Start slots that fit the day, miss lunch, and suit faculty and division."""
    cal = instance.calendar
    fac = instance.faculty_by_id[session.faculty_id]
    batch = instance.batch_by_id[session.batch_id]
    out = []
    for slot in cal.slots:
        span = cal.span(slot.id, session.duration)
        if span is None:
            continue
        if any(u in fac.unavailable for u in span):
            continue
        if any(u in batch.unavailable for u in span):
            continue
        out.append(slot.id)
    return out


def _eligible_rooms(instance: Instance, session: Session):
    seats = instance.seats_needed(session)
    return [
        r
        for r in instance.rooms
        if r.active
        and r.room_type is session.room_type
        and r.capacity >= seats
        and (
            not session.required_capability
            or session.required_capability in r.capabilities
        )
    ]


def candidate_count(instance: Instance, session: Session) -> int:
    """How many (start, room) placements survive the single-placement rules."""
    cal = instance.calendar
    rooms = _eligible_rooms(instance, session)
    total = 0
    for start in _legal_starts(instance, session):
        span = cal.span(start, session.duration)
        assert span is not None
        total += sum(1 for r in rooms if not any(u in r.unavailable for u in span))
    return total


def diagnose(instance: Instance) -> DiagnosisReport:
    """Find rule categories that make the instance impossible or nearly so."""
    cal = instance.calendar
    report = DiagnosisReport()
    teaching = len(cal.teaching_slots)

    # --- 0. coordinator locks the current rules cannot honour ------------
    for sid, lock in instance.locks.items():
        problems = lock_blockers(instance, lock)
        if not problems:
            continue
        s = instance.session_by_id.get(sid)
        what = f"{s.subject_code} ({s.batch_id})" if s else sid
        where = (
            cal.by_id[lock.timeslot_id].label
            if lock.timeslot_id in cal.by_id
            else f"slot {lock.timeslot_id}"
        )
        room = f" in {lock.room_id}" if lock.room_id else ""
        report.findings.append(
            Finding(
                category="Locked sessions",
                severity=BLOCKING,
                message=f"{what} is locked at {where}{room}, but "
                f"{'; '.join(problems)}.",
                suggestions=(
                    f"unlock {what} so the solver may move it",
                    "move the lock to a slot the current rules allow",
                ),
            )
        )

    # --- 1. sessions with nowhere legal to go -------------------------
    stranded: list[tuple[Session, str]] = []
    for s in instance.sessions:
        if s.id in instance.locks or candidate_count(instance, s) > 0:
            continue
        fac = instance.faculty_by_id[s.faculty_id]
        rooms = _eligible_rooms(instance, s)
        seats = instance.seats_needed(s)
        if not rooms:
            equipped = (
                f" equipped for '{s.required_capability}'"
                if s.required_capability
                else ""
            )
            why = (
                f"no {s.room_type.value.lower()} room{equipped} in service "
                f"seats {seats} students"
            )
        elif not _legal_starts(instance, s):
            batch = instance.batch_by_id[s.batch_id]
            why = (
                f"{s.batch_id} has no available block of {s.duration}h"
                if batch.unavailable
                else f"{fac.name} has no available block of {s.duration}h"
            )
        else:
            why = "every suitable room is unavailable whenever the faculty is free"
        stranded.append((s, why))

    if stranded:
        by_reason: dict[str, list[Session]] = defaultdict(list)
        for s, why in stranded:
            by_reason[why].append(s)
        for why, sessions in by_reason.items():
            names = ", ".join(f"{s.subject_code} ({s.batch_id})" for s in sessions[:4])
            more = f" and {len(sessions) - 4} more" if len(sessions) > 4 else ""
            faculty = {instance.faculty_by_id[s.faculty_id].name for s in sessions}
            report.findings.append(
                Finding(
                    category="Sessions with no legal placement",
                    severity=BLOCKING,
                    message=f"{len(sessions)} session(s) cannot be placed anywhere: "
                    f"{names}{more} - {why}.",
                    suggestions=(
                        f"add an availability window for {', '.join(sorted(faculty))}",
                        "assign another qualified faculty member to these sessions",
                        "add a suitable room, or relax the room capacity/type rule",
                    ),
                )
            )

    # --- 2. faculty time budget ---------------------------------------
    load: dict[str, int] = defaultdict(int)
    for s in instance.sessions:
        load[s.faculty_id] += s.duration

    for f in instance.faculty:
        needed = load.get(f.id, 0)
        if needed == 0:
            continue
        free = teaching - len({u for u in f.unavailable if not cal.by_id[u].is_lunch})
        if needed > free:
            report.findings.append(
                Finding(
                    category="Faculty availability",
                    severity=BLOCKING,
                    message=f"{f.name} must teach {needed}h but is available for "
                    f"only {free} teaching periods.",
                    suggestions=(
                        f"free up at least {needed - free} more period(s) for {f.name}",
                        "reassign some of these sessions to another faculty member",
                    ),
                )
            )
        elif needed > f.max_weekly_load:
            report.findings.append(
                Finding(
                    category="Weekly teaching load",
                    severity=BLOCKING,
                    message=f"{f.name} is assigned {needed}h but the weekly cap is "
                    f"{f.max_weekly_load}h.",
                    suggestions=(
                        f"raise {f.name}'s weekly cap to at least {needed}h",
                        "move a subject to another faculty member",
                    ),
                )
            )
        elif needed > f.max_daily_load * cal.days:
            report.findings.append(
                Finding(
                    category="Daily teaching load",
                    severity=BLOCKING,
                    message=f"{f.name} needs {needed}h but {f.max_daily_load}h/day "
                    f"over {cal.days} days allows only "
                    f"{f.max_daily_load * cal.days}h.",
                    suggestions=(f"raise {f.name}'s daily cap",),
                )
            )
        elif free - needed <= 1:
            report.findings.append(
                Finding(
                    category="Faculty availability",
                    severity=TIGHT,
                    message=f"{f.name} needs {needed}h and has {free} free periods "
                    f"— no slack.",
                    suggestions=(f"add an availability window for {f.name}",),
                )
            )

    # --- 3. batch time budget -----------------------------------------
    batch_hours: dict[str, int] = defaultdict(int)
    for s in instance.sessions:
        # Parallel elective options share one period of the batch's week.
        if s.elective_group:
            continue
        batch_hours[s.batch_id] += s.duration
    for members in instance.elective_groups().values():
        batch_hours[members[0].batch_id] += members[0].duration

    for b in instance.batches:
        needed = batch_hours.get(b.id, 0)
        free = teaching - len({u for u in b.unavailable if not cal.by_id[u].is_lunch})
        # An event that blocks the division out is a separate cause from simply
        # asking for more hours than a week holds, so report it separately.
        if free < teaching and needed > free:
            report.findings.append(
                Finding(
                    category="Division availability",
                    severity=BLOCKING,
                    message=f"{b.id} needs {needed} periods but is free for only "
                    f"{free} after being blocked out.",
                    suggestions=(
                        f"shorten the event that blocks {b.id}",
                        "reduce this division's weekly contact hours",
                    ),
                )
            )
        if needed > teaching:
            report.findings.append(
                Finding(
                    category="Division timetable capacity",
                    severity=BLOCKING,
                    message=f"{b.id} needs {needed} periods but the week has only "
                    f"{teaching} teaching periods.",
                    suggestions=(
                        "reduce weekly contact hours for this division",
                        "extend the teaching day or week",
                    ),
                )
            )
        elif teaching - needed <= 2:
            report.findings.append(
                Finding(
                    category="Division timetable capacity",
                    severity=TIGHT,
                    message=f"{b.id} fills {needed} of {teaching} periods — almost "
                    f"no room to move anything.",
                    suggestions=("reduce contact hours or extend the week",),
                )
            )

    # --- 4. room supply by type ---------------------------------------
    # Only rooms in service count towards supply.
    for room_type in (RoomType.LAB, RoomType.LECTURE):
        demand = sum(s.duration for s in instance.sessions if s.room_type is room_type)
        if demand == 0:
            continue
        supply = 0
        for r in instance.rooms:
            if r.room_type is not room_type or not r.active:
                continue
            supply += teaching - len(
                {u for u in r.unavailable if not cal.by_id[u].is_lunch}
            )
        label = "laboratory" if room_type is RoomType.LAB else "lecture"
        if demand > supply:
            report.findings.append(
                Finding(
                    category=f"{label.title()} room supply",
                    severity=BLOCKING,
                    message=f"{demand} {label} room-hours are required but only "
                    f"{supply} are available.",
                    suggestions=(
                        f"bring another {label} room into service",
                        f"shorten or reduce {label} sessions",
                    ),
                )
            )
        elif supply and demand / supply > 0.9:
            report.findings.append(
                Finding(
                    category=f"{label.title()} room supply",
                    severity=TIGHT,
                    message=f"{label} rooms are {demand / supply * 100:.0f}% "
                    f"committed ({demand}h of {supply}h).",
                    suggestions=(f"free up or add a {label} room",),
                )
            )

    # --- 5. parallel electives need simultaneous rooms -----------------
    # One unavailable teacher breaks every group they appear in, so report the
    # pattern once rather than repeating it per group.
    no_shared_slot: list[str] = []
    too_few_rooms: list[str] = []

    for gid, members in instance.elective_groups().items():
        shared = set(_legal_starts(instance, members[0]))
        for s in members[1:]:
            shared &= set(_legal_starts(instance, s))
        if not shared:
            no_shared_slot.append(gid)
            continue
        if min(len(_eligible_rooms(instance, s)) for s in members) < len(members):
            too_few_rooms.append(gid)

    if no_shared_slot:
        report.findings.append(
            Finding(
                category="Parallel electives",
                severity=BLOCKING,
                message=f"{len(no_shared_slot)} elective group(s) have no timeslot "
                f"where every option's teacher is free "
                f"(e.g. {no_shared_slot[0]}).",
                suggestions=(
                    "align the availability of the elective teachers",
                    "run the options at different times instead of in parallel",
                ),
            )
        )
    if too_few_rooms:
        report.findings.append(
            Finding(
                category="Parallel electives",
                severity=BLOCKING,
                message=f"{len(too_few_rooms)} elective group(s) need more "
                f"simultaneous suitable rooms than exist "
                f"(e.g. {too_few_rooms[0]}).",
                suggestions=("add a suitable room for the elective cohorts",),
            )
        )

    def rank(f: Finding) -> tuple[int, int]:
        order = (
            CATEGORY_ORDER.index(f.category)
            if f.category in CATEGORY_ORDER
            else len(CATEGORY_ORDER)
        )
        return (0 if f.severity == BLOCKING else 1, order)

    report.findings.sort(key=rank)
    return report
