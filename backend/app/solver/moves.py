"""Coordinator-requested moves: check first, then re-optimise around them.

"Put DBMS for TE-A on Monday at 10" is checked before anything is solved:

* hard blockers -- rules no rearrangement can remove: the teacher is away, the
  block would cross lunch, the room is the wrong type, too small, unequipped or
  out of service -- refuse the move outright and name the rule;
* resolvable conflicts -- another session holds the slot or the room, a load
  cap would be hit -- are reported, then cleared by a minimum-disruption repair
  that treats the requested placement as locked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Instance, Lock, Timetable
from ..progress import ProgressLog
from .explain import HARD_RULES, NO_ROOM, ROOM_BUSY, Blocker, PlacementProbe
from .model import ObjectiveWeights
from .repair import RepairResult, repair


@dataclass(slots=True)
class MoveCheck:
    session_id: str
    target_label: str
    room_id: str | None
    hard: list[Blocker] = field(default_factory=list)
    resolvable: list[Blocker] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return not self.hard


def check_move(
    instance: Instance,
    timetable: Timetable,
    session_id: str,
    timeslot_id: int,
    room_id: str | None = None,
) -> MoveCheck:
    """Classify everything that stands between a session and a placement."""
    session = instance.session_by_id.get(session_id)
    if session is None:
        raise KeyError(f"No session {session_id!r}")
    slot = instance.calendar.by_id.get(timeslot_id)
    if slot is None:
        raise ValueError(f"No timeslot {timeslot_id}")
    if room_id is not None and room_id not in instance.room_by_id:
        raise ValueError(f"No room {room_id!r}")

    # The session's own lock is the thing being revised, so it is not an
    # obstacle to itself; every other lock still is.
    others = {k: v for k, v in instance.locks.items() if k != session_id}
    probe = PlacementProbe(instance.derive(locks=others), timetable, session)

    blockers, span = probe.slot_blockers(timeslot_id)
    if span:
        if room_id is not None:
            blockers.extend(probe.room_blockers(room_id, span))
        elif not probe.eligible_rooms:
            blockers.append(
                Blocker(
                    NO_ROOM,
                    f"no suitable room in service seats {probe.seats} students",
                )
            )
        elif len(probe.free_rooms(span)) < len(probe.group):
            blockers.append(
                Blocker(
                    ROOM_BUSY,
                    f"every suitable room is taken at {slot.label}; the sessions "
                    f"holding them would have to move",
                )
            )

    check = MoveCheck(session_id=session_id, target_label=slot.label, room_id=room_id)
    for b in blockers:
        (check.hard if b.rule in HARD_RULES else check.resolvable).append(b)
    return check


def preview_move(
    instance: Instance,
    baseline: Timetable,
    session_id: str,
    timeslot_id: int,
    room_id: str | None = None,
    *,
    weights: ObjectiveWeights | None = None,
    phase1_limit: float = 10.0,
    phase2_limit: float = 3.0,
    workers: int = 8,
    progress: ProgressLog | None = None,
) -> tuple[MoveCheck, RepairResult | None, Instance]:
    """Check a move, and if allowed, repair the timetable around it.

    Returns the check, the repair (None when refused), and the instance the
    repair ran on -- which carries the move as a lock, so approving the result
    keeps the session where the coordinator put it.
    """
    check = check_move(instance, baseline, session_id, timeslot_id, room_id)
    if not check.allowed:
        return check, None, instance

    pinned = instance.derive(
        locks={
            **instance.locks,
            session_id: Lock(
                session_id=session_id,
                timeslot_id=timeslot_id,
                room_id=room_id,
                reason="placed by the coordinator",
            ),
        }
    )
    result = repair(
        pinned,
        baseline,
        None,
        weights=weights,
        phase1_limit=phase1_limit,
        phase2_limit=phase2_limit,
        workers=workers,
        progress=progress,
    )
    return check, result, pinned
