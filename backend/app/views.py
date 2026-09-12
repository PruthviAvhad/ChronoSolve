"""Synchronized batch / faculty / room views over one schedule state.

All three views read the same placements, so they cannot drift out of sync.
`changed` marks cells that moved since the published timetable -- the revision
highlighting a coordinator needs when reviewing a repair.
"""

from __future__ import annotations

from .domain.models import DAYS, PERIOD_START, Instance, Timetable

CELL_WIDTH = 22
CONTINUATION = "| (cont.)"


def _render(instance: Instance, title: str, grid: dict[tuple[int, int], str]) -> str:
    cal = instance.calendar
    head = "  time   " + "".join(f"{DAYS[d]:<{CELL_WIDTH}}" for d in range(cal.days))
    lines = [title, head, "  " + "-" * (7 + CELL_WIDTH * cal.days)]

    for p in range(cal.periods):
        row = f"  {PERIOD_START[p]}  "
        for d in range(cal.days):
            text = grid.get((d, p), "")
            if not text:
                slot = cal.by_id[d * cal.periods + p]
                text = "-- lunch --" if slot.is_lunch else "."
            row += f"{text[: CELL_WIDTH - 1]:<{CELL_WIDTH}}"
        lines.append(row.rstrip())

    return "\n".join(lines)


def _fill(
    instance: Instance,
    timetable: Timetable,
    keep,
    cell,
    changed: set[str] | None = None,
) -> dict[tuple[int, int], str]:
    cal = instance.calendar
    grid: dict[tuple[int, int], str] = {}
    changed = changed or set()

    for s in instance.sessions:
        p = timetable.placements.get(s.id)
        if p is None or not keep(s, p):
            continue
        start = cal.by_id[p.timeslot_id]
        marker = "*" if s.id in changed else " "
        existing = grid.get((start.day, start.period), "")
        text = marker + cell(s, p)
        # Parallel electives share one period for the batch; show both.
        grid[(start.day, start.period)] = (
            f"{existing.strip()} + {text.strip()}" if existing else text
        )
        for k in range(1, s.duration):
            slot = cal.by_id.get(start.id + k)
            if slot is not None:
                grid[(slot.day, slot.period)] = marker + CONTINUATION

    return grid


def batch_view(
    instance: Instance,
    timetable: Timetable,
    batch_id: str,
    changed: set[str] | None = None,
) -> str:
    """What a division sees. Parallel electives share one period."""
    batch = instance.batch_by_id[batch_id]
    grid = _fill(
        instance,
        timetable,
        keep=lambda s, p: s.batch_id == batch_id,
        cell=lambda s, p: f"{s.subject_code} {p.room_id}",
        changed=changed,
    )
    return _render(
        instance, f"\nBATCH VIEW  {batch.id} - {batch.name} ({batch.strength})", grid
    )


def faculty_view(
    instance: Instance,
    timetable: Timetable,
    faculty_id: str,
    changed: set[str] | None = None,
) -> str:
    fac = instance.faculty_by_id[faculty_id]
    grid = _fill(
        instance,
        timetable,
        keep=lambda s, p: s.faculty_id == faculty_id,
        cell=lambda s, p: f"{s.subject_code} {s.batch_id} {p.room_id}",
        changed=changed,
    )
    return _render(instance, f"\nFACULTY VIEW  {fac.name} ({fac.id})", grid)


def room_view(
    instance: Instance,
    timetable: Timetable,
    room_id: str,
    changed: set[str] | None = None,
) -> str:
    room = instance.room_by_id[room_id]
    grid = _fill(
        instance,
        timetable,
        keep=lambda s, p: p.room_id == room_id,
        cell=lambda s, p: f"{s.subject_code} {s.batch_id}",
        changed=changed,
    )
    return _render(
        instance,
        f"\nROOM VIEW  {room.id} - {room.name} "
        f"(seats {room.capacity}, {room.room_type.value})",
        grid,
    )
