"""Locked sessions: a coordinator pins a session and the solver must obey.

`lock_blockers` is the one place that decides whether a lock can be honoured
under the current rules. The model builder refuses to build around an illegal
lock and the diagnosis names it -- both by calling this, so the two can never
disagree about why a locked timetable is impossible.
"""

from __future__ import annotations

from ..domain.models import Instance, Lock, Room, Session


def room_problems(instance: Instance, session: Session, room: Room) -> list[str]:
    """Reasons `room` can never host `session`, whatever the time."""
    out: list[str] = []
    seats = instance.seats_needed(session)
    if not room.active:
        out.append(f"{room.id} is out of service")
    if room.room_type is not session.room_type:
        out.append(
            f"{room.id} is a {room.room_type.value.lower()} room but the session "
            f"needs a {session.room_type.value.lower()} room"
        )
    if room.capacity < seats:
        out.append(f"{room.id} seats {room.capacity} but {seats} students attend")
    if (
        session.required_capability
        and session.required_capability not in room.capabilities
    ):
        out.append(
            f"{room.id} is not equipped for '{session.required_capability}'"
        )
    return out


def lock_blockers(instance: Instance, lock: Lock) -> list[str]:
    """Every rule that stops `lock` being honoured. Empty means it can be."""
    session = instance.session_by_id.get(lock.session_id)
    if session is None:
        return [f"session {lock.session_id} is no longer part of the timetable"]

    cal = instance.calendar
    start = cal.by_id.get(lock.timeslot_id)
    if start is None:
        return [f"timeslot {lock.timeslot_id} does not exist"]

    span = cal.span(lock.timeslot_id, session.duration)
    if span is None:
        return [
            f"a {session.duration}-hour block starting {start.label} would cross "
            f"lunch or the end of the day"
        ]

    out: list[str] = []
    fac = instance.faculty_by_id[session.faculty_id]
    batch = instance.batch_by_id[session.batch_id]
    if any(u in fac.unavailable for u in span):
        out.append(f"{fac.name} is unavailable at {start.label}")
    if any(u in batch.unavailable for u in span):
        out.append(f"{batch.id} is unavailable at {start.label}")

    if lock.room_id is not None:
        room = instance.room_by_id.get(lock.room_id)
        if room is None:
            out.append(f"room {lock.room_id} no longer exists")
        else:
            out.extend(room_problems(instance, session, room))
            if any(u in room.unavailable for u in span):
                out.append(f"{room.id} is unavailable at {start.label}")
    elif not lock_placements(instance, lock):
        out.append(f"no suitable room is available at {start.label}")

    return out


def lock_placements(instance: Instance, lock: Lock) -> list[tuple[int, str]]:
    """The only (start, room) pairs a locked session may take.

    A lock that names a room pins both; a time-only lock leaves the room to the
    solver among every suitable one, deliberately ignoring the tight-room
    shortlist so a coordinator's time choice is never refused for want of it.
    """
    session = instance.session_by_id[lock.session_id]
    span = instance.calendar.span(lock.timeslot_id, session.duration) or ()
    if lock.room_id is not None:
        room = instance.room_by_id.get(lock.room_id)
        rooms = [room] if room is not None else []
    else:
        rooms = sorted(
            (r for r in instance.rooms if not room_problems(instance, session, r)),
            key=lambda r: (r.capacity, r.id),
        )
    return [
        (lock.timeslot_id, r.id)
        for r in rooms
        if not room_problems(instance, session, r)
        and not any(u in r.unavailable for u in span)
    ]
