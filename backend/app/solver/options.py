"""Ranked repair alternatives for a disruption.

When a teacher reports an absence they should see more than one way the
timetable could absorb it, ranked by the rules the solver itself uses. Each
option is a complete two-phase repair -- fewest moves first, then best quality
-- never a single-slot guess: moving one class can force others to move, and
every option says exactly how many.

The first alternative is the solver's own lexicographic optimum; Auto-select
Best picks whichever option ranks first. Every further alternative is the best
repair that starts at least one directly affected session at a time no earlier
alternative used, found by the same `repair()` run over a model with the
earlier answers cut off. There is one repair algorithm, not two.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.models import Instance, Timetable
from ..progress import ProgressLog
from .disruption import Disruption, directly_affected
from .metrics import QualityMetrics, schedule_metrics
from .model import ObjectiveWeights, TimetableModel
from .repair import RepairResult, repair
from .validate import validate

LETTERS = "ABCDEFGH"

# Quality measures compared against the published timetable for each option.
QUALITY_FIELDS = (
    "student_idle_hours",
    "faculty_idle_hours",
    "wasted_seats",
    "preference_hits",
    "last_slot_sessions",
)


@dataclass(frozen=True, slots=True)
class AffectedMove:
    """Where one directly affected session goes under an option."""

    session_id: str
    subject_code: str
    subject_name: str
    batch_id: str
    from_label: str
    from_room: str
    to_label: str
    to_room: str

    @property
    def moved_time(self) -> bool:
        return self.from_label != self.to_label


@dataclass(slots=True)
class RepairOption:
    label: str
    result: RepairResult
    affected_moves: list[AffectedMove]
    # Sessions that move although the disruption did not touch them.
    knock_on: int
    # Recounted by the independent validator, not taken from the solver.
    hard_violations: int
    # Weighted soft objective, from the solver's own term values.
    soft_cost: int
    quality: QualityMetrics
    quality_delta: dict[str, float]
    rank: int = 0
    recommended: bool = False

    @property
    def sort_key(self) -> tuple[int, int, int]:
        d = self.result.diff
        assert d is not None
        return (d.time_moves, d.room_only_moves, self.soft_cost)


@dataclass(slots=True)
class OptionSet:
    affected: list[str]
    options: list[RepairOption] = field(default_factory=list)
    # The first repair attempt when it failed. Carries the honest status,
    # reason and diagnosis; None whenever at least one option exists.
    failure: RepairResult | None = None

    @property
    def best(self) -> RepairOption | None:
        return self.options[0] if self.options else None


def soft_cost(result: RepairResult, weights: ObjectiveWeights | None) -> int:
    """The weighted quality cost of a repair, from the solver's term values."""
    w = weights or ObjectiveWeights()
    return int(
        sum(getattr(w, name, 0) * value for name, value in result.soft_breakdown.items())
    )


def _exclude(previous: list[dict[str, int]]):
    """Model hook: every earlier alternative must differ in at least one
    affected session's start time."""

    def apply(tm: TimetableModel) -> None:
        for starts in previous:
            if not starts:
                continue
            same_time = [
                tm.x[(sid, t, r)]
                for sid, start in starts.items()
                for t, r in tm.candidates.get(sid, [])
                if t == start
            ]
            tm.model.Add(sum(same_time) <= len(starts) - 1)

    return apply


def repair_options(
    disrupted: Instance,
    baseline: Timetable,
    disruptions: list[Disruption] | None,
    *,
    affected: list[str] | None = None,
    count: int = 3,
    weights: ObjectiveWeights | None = None,
    phase1_limit: float = 6.0,
    phase2_limit: float = 2.0,
    workers: int = 8,
    progress: ProgressLog | None = None,
) -> OptionSet:
    """Up to `count` distinct repairs, ranked by (time moves, room moves, cost)."""
    track = progress if progress is not None else ProgressLog()
    hit = (
        list(affected)
        if affected is not None
        else directly_affected(disrupted, baseline, disruptions or [])
    )
    found = OptionSet(affected=hit)
    cal = disrupted.calendar
    before = schedule_metrics(disrupted, baseline)
    seen: list[dict[str, int]] = []

    # With nothing directly affected there is one sensible answer -- change
    # nothing that is not forced -- so no alternatives are invented.
    wanted = count if hit else 1

    for i in range(wanted):
        child = track.child(f"alt{i + 1}", f"Alternative {i + 1} · ")
        result = repair(
            disrupted,
            baseline,
            disruptions,
            weights=weights,
            phase1_limit=phase1_limit,
            phase2_limit=phase2_limit,
            workers=workers,
            progress=child,
            extra_constraints=_exclude(seen) if seen else None,
        )
        if not result.solved or result.diff is None or result.timetable is None:
            if i == 0:
                found.failure = result
            # No further distinct repair exists, or none was found in time.
            break

        tt = result.timetable
        seen.append(
            {sid: tt.placements[sid].timeslot_id for sid in hit if sid in tt.placements}
        )

        moves = []
        for sid in hit:
            s = disrupted.session_by_id[sid]
            old = baseline.placements[sid]
            new = tt.placements[sid]
            moves.append(
                AffectedMove(
                    session_id=sid,
                    subject_code=s.subject_code,
                    subject_name=s.subject_name,
                    batch_id=s.batch_id,
                    from_label=cal.by_id[old.timeslot_id].label,
                    from_room=old.room_id,
                    to_label=cal.by_id[new.timeslot_id].label,
                    to_room=new.room_id,
                )
            )

        changed = {c.session_id for c in result.diff.changes}
        quality = result.quality or schedule_metrics(disrupted, tt)
        found.options.append(
            RepairOption(
                label="",
                result=result,
                affected_moves=moves,
                knock_on=len(changed - set(hit)),
                hard_violations=validate(disrupted, tt).total,
                soft_cost=soft_cost(result, weights),
                quality=quality,
                quality_delta={
                    f: round(getattr(quality, f) - getattr(before, f), 1)
                    for f in QUALITY_FIELDS
                },
            )
        )

    # The same lexicographic order the solver optimises: time moves, then
    # room-only moves, then weighted quality cost.
    found.options.sort(key=lambda o: o.sort_key)
    for rank, option in enumerate(found.options, start=1):
        option.rank = rank
        option.label = LETTERS[rank - 1]
        option.recommended = rank == 1
    return found
