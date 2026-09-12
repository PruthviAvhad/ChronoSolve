"""Schedule comparison and quality measurement.

Every number here is derived from actual placements. Nothing is assumed,
defaulted or hard-coded -- if the solver did not produce it, it is not reported.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ..domain.models import Instance, Timetable


@dataclass(frozen=True, slots=True)
class SessionChange:
    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    faculty_name: str
    from_slot: int | None
    to_slot: int
    from_room: str | None
    to_room: str
    from_label: str
    to_label: str

    @property
    def moved_time(self) -> bool:
        return self.from_slot != self.to_slot

    @property
    def moved_room(self) -> bool:
        return self.from_room != self.to_room


@dataclass(slots=True)
class ScheduleDiff:
    total: int
    changes: list[SessionChange] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.changes)

    @property
    def unchanged(self) -> int:
        return self.total - self.changed

    @property
    def retention_pct(self) -> float:
        """Share of sessions holding the exact published timeslot *and* room."""
        if self.total == 0:
            return 0.0
        return self.unchanged / self.total * 100.0

    @property
    def time_moves(self) -> int:
        return sum(1 for c in self.changes if c.moved_time)

    @property
    def room_only_moves(self) -> int:
        return sum(1 for c in self.changes if not c.moved_time and c.moved_room)

    @property
    def time_retention_pct(self) -> float:
        """Share of sessions students still find at their published time."""
        if self.total == 0:
            return 0.0
        return (self.total - self.time_moves) / self.total * 100.0


def diff_schedules(
    instance: Instance, baseline: Timetable, revised: Timetable
) -> ScheduleDiff:
    """Compare a revised timetable against the published baseline."""
    cal = instance.calendar
    changes: list[SessionChange] = []

    for s in instance.sessions:
        new = revised.placements.get(s.id)
        if new is None:
            continue
        old = baseline.placements.get(s.id)
        if (
            old is not None
            and old.timeslot_id == new.timeslot_id
            and old.room_id == new.room_id
        ):
            continue

        changes.append(
            SessionChange(
                session_id=s.id,
                subject_code=s.subject_code,
                subject_name=s.subject_name,
                batch_id=s.batch_id,
                faculty_name=instance.faculty_by_id[s.faculty_id].name,
                from_slot=old.timeslot_id if old else None,
                to_slot=new.timeslot_id,
                from_room=old.room_id if old else None,
                to_room=new.room_id,
                from_label=cal.by_id[old.timeslot_id].label if old else "-",
                to_label=cal.by_id[new.timeslot_id].label,
            )
        )

    changes.sort(key=lambda c: (c.batch_id, c.from_slot or 0))
    return ScheduleDiff(total=len(instance.sessions), changes=changes)


@dataclass(slots=True)
class QualityMetrics:
    student_idle_hours: int
    faculty_idle_hours: int
    room_utilisation_pct: float
    faculty_load_spread: int
    busiest_faculty_hours: int
    last_slot_sessions: int
    # Room suitability, measured per meeting from the rooms actually assigned.
    wasted_seats: int = 0
    seat_efficiency_pct: float = 0.0
    oversized_sessions: int = 0
    # Teaching placed in periods a teacher asked to keep free (soft rule).
    preference_hits: int = 0

    def render(self) -> str:
        return (
            f"  student idle hours          {self.student_idle_hours}\n"
            f"  faculty idle hours          {self.faculty_idle_hours}\n"
            f"  room utilisation            {self.room_utilisation_pct:.1f}%\n"
            f"  seat efficiency             {self.seat_efficiency_pct:.1f}% "
            f"({self.wasted_seats} empty seats, {self.oversized_sessions} "
            f"oversized rooms)\n"
            f"  faculty load spread         {self.faculty_load_spread}h "
            f"(busiest {self.busiest_faculty_hours}h)\n"
            f"  last-period sessions        {self.last_slot_sessions}"
        )


# A room at least this much bigger than the class it hosts counts as oversized.
OVERSIZED_RATIO = 1.5


def _idle_hours(occupied_by_day: dict[int, list[int]]) -> int:
    """Free periods sandwiched between the first and last busy period of a day."""
    total = 0
    for busy in occupied_by_day.values():
        if len(busy) < 2:
            continue
        total += (max(busy) - min(busy) + 1) - len(busy)
    return total


def schedule_metrics(instance: Instance, timetable: Timetable) -> QualityMetrics:
    """Recompute schedule quality straight from placements."""
    cal = instance.calendar
    periods = cal.periods

    batch_days: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
    faculty_days: dict[str, dict[int, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )
    faculty_hours: dict[str, int] = defaultdict(int)
    room_hours = 0
    last_slot_sessions = 0
    wasted_seats = 0
    seat_hours_needed = 0
    seat_hours_offered = 0
    oversized = 0
    preference_hits = 0

    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue
        start = cal.by_id[p.timeslot_id]
        fac = instance.faculty_by_id[s.faculty_id]

        for k in range(s.duration):
            slot = cal.by_id.get(start.id + k)
            if slot is None:
                continue
            # Parallel elective options occupy one batch period between them, so
            # count batch occupancy once per group rather than once per option.
            if slot.period not in batch_days[s.batch_id][slot.day]:
                batch_days[s.batch_id][slot.day].append(slot.period)
            if slot.period not in faculty_days[s.faculty_id][slot.day]:
                faculty_days[s.faculty_id][slot.day].append(slot.period)
            faculty_hours[s.faculty_id] += 1
            room_hours += 1
            if slot.id in fac.preferred_off:
                preference_hits += 1

        room = instance.room_by_id.get(p.room_id)
        if room is not None:
            need = instance.seats_needed(s)
            wasted_seats += max(0, room.capacity - need)
            seat_hours_needed += need * s.duration
            seat_hours_offered += room.capacity * s.duration
            if room.capacity >= OVERSIZED_RATIO * need:
                oversized += 1

        if start.period == periods - 1:
            last_slot_sessions += 1

    student_idle = sum(_idle_hours(d) for d in batch_days.values())
    faculty_idle = sum(_idle_hours(d) for d in faculty_days.values())

    active_rooms = [r for r in instance.rooms if r.active]
    teaching_capacity = len(active_rooms) * len(cal.teaching_slots)
    utilisation = room_hours / teaching_capacity * 100.0 if teaching_capacity else 0.0

    loads = list(faculty_hours.values()) or [0]

    return QualityMetrics(
        student_idle_hours=student_idle,
        faculty_idle_hours=faculty_idle,
        room_utilisation_pct=utilisation,
        faculty_load_spread=max(loads) - min(loads),
        busiest_faculty_hours=max(loads),
        last_slot_sessions=last_slot_sessions,
        wasted_seats=wasted_seats,
        seat_efficiency_pct=(
            seat_hours_needed / seat_hours_offered * 100.0 if seat_hours_offered else 0.0
        ),
        oversized_sessions=oversized,
        preference_hits=preference_hits,
    )


def faculty_workload(instance: Instance, timetable: Timetable) -> list[dict]:
    """Per-teacher teaching hours, busiest day and cross-year reach."""
    cal = instance.calendar
    hours: dict[str, int] = defaultdict(int)
    per_day: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    years: dict[str, set[str]] = defaultdict(set)

    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue
        day = cal.by_id[p.timeslot_id].day
        hours[s.faculty_id] += s.duration
        per_day[s.faculty_id][day] += s.duration
        batch = instance.batch_by_id[s.batch_id]
        years.setdefault(s.faculty_id, set()).add(batch.year_label or batch.id)

    return [
        {
            "faculty_id": f.id,
            "name": f.name,
            "weekly_hours": hours.get(f.id, 0),
            "weekly_cap": f.max_weekly_load,
            "busiest_day_hours": max(per_day[f.id].values(), default=0),
            "daily_cap": f.max_daily_load,
            "years_taught": sorted(years.get(f.id, set())),
        }
        for f in instance.faculty
    ]


def room_usage(instance: Instance, timetable: Timetable) -> list[dict]:
    """Per-room booked hours and how well the booked classes fit it."""
    teaching = len(instance.calendar.teaching_slots)
    booked: dict[str, int] = defaultdict(int)
    fill: dict[str, list[float]] = defaultdict(list)

    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None:
            continue
        room = instance.room_by_id.get(p.room_id)
        if room is None:
            continue
        booked[room.id] += s.duration
        fill[room.id].append(instance.seats_needed(s) / room.capacity * 100.0)

    return [
        {
            "room_id": r.id,
            "name": r.name,
            "room_type": r.room_type.value,
            "capacity": r.capacity,
            "active": r.active,
            "booked_hours": booked.get(r.id, 0),
            "utilisation_pct": booked.get(r.id, 0) / teaching * 100.0 if teaching else 0.0,
            "avg_fill_pct": (sum(fill[r.id]) / len(fill[r.id])) if fill.get(r.id) else 0.0,
        }
        for r in instance.rooms
    ]
