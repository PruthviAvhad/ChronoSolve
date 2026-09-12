"""Minimum-disruption re-optimisation -- the core of ChronoSolve.

When a published timetable breaks, regenerating from scratch produces a valid
but socially useless answer: everyone's week moves. Repair instead makes the
published schedule part of the objective and searches for the smallest feasible
deviation from it.

The search is strictly lexicographic, run as two phases over one model:

  Phase 1  minimise disruption      (a) fewest sessions moved to a new time,
                                    (b) then fewest moved to a new room.
  Phase 2  minimise schedule cost   best student/faculty quality achievable
                                    *without* exceeding phase 1's disruption.

Phase 2 is what stops the repair from being technically minimal but practically
awful -- among all equally-minimal repairs it picks the most comfortable one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..domain.models import Instance, Timetable
from ..progress import ProgressLog
from .diagnose import DiagnosisReport, diagnose
from .disruption import Disruption, apply_disruptions, directly_affected
from .engine import solve
from .metrics import QualityMetrics, ScheduleDiff, diff_schedules, schedule_metrics
from .model import ObjectiveWeights, TimetableModel


@dataclass(slots=True)
class RepairResult:
    status: str
    baseline: Timetable
    timetable: Timetable | None = None
    diff: ScheduleDiff | None = None
    quality: QualityMetrics | None = None
    soft_breakdown: dict[str, int] = field(default_factory=dict)
    model_stats: dict[str, int] = field(default_factory=dict)
    directly_affected: list[str] = field(default_factory=list)
    phase1_seconds: float = 0.0
    phase2_seconds: float = 0.0
    reason: str | None = None
    diagnosis: DiagnosisReport | None = None

    # The two phases certify different things and must not be conflated.
    # Phase 1 proves the *number of moves* is minimal; phase 2 only improves
    # quality within that budget, and its status says nothing about minimality.
    phase1_status: str = "UNKNOWN"
    phase2_status: str = "SKIPPED"

    @property
    def solved(self) -> bool:
        return self.timetable is not None and self.timetable.is_solved

    @property
    def minimal_proven(self) -> bool:
        """True when the solver proved no smaller repair exists."""
        return self.phase1_status == "OPTIMAL"

    @property
    def total_seconds(self) -> float:
        return self.phase1_seconds + self.phase2_seconds


def _change_expressions(tm: TimetableModel, baseline: Timetable):
    """Build `time_moves` and `room_only_moves` against the published schedule.

    For each session, `kept_time` is 1 when it stays in its published timeslot
    (any room) and `kept_exact` is 1 when timeslot *and* room both survive. A
    room-only move is therefore `kept_time - kept_exact`, which is never
    negative because keeping the exact placement implies keeping the time.
    """
    time_moves = []
    room_only_moves = []

    for s in tm.instance.sessions:
        base = baseline.placements.get(s.id)
        if base is None:
            continue

        same_time = [
            tm.x[(s.id, t, r)] for t, r in tm.candidates[s.id] if t == base.timeslot_id
        ]
        kept_time = sum(same_time) if same_time else 0
        kept_exact = tm.x.get((s.id, base.timeslot_id, base.room_id), 0)

        time_moves.append(1 - kept_time)
        room_only_moves.append(kept_time - kept_exact)

    return sum(time_moves), sum(room_only_moves)


def _hint_from(tm: TimetableModel, timetable: Timetable) -> None:
    tm.model.ClearHints()
    for sid, p in timetable.placements.items():
        var = tm.x.get((sid, p.timeslot_id, p.room_id))
        if var is not None:
            tm.model.AddHint(var, 1)


def repair(
    disrupted: Instance,
    baseline: Timetable,
    disruptions: list[Disruption] | None = None,
    weights: ObjectiveWeights | None = None,
    phase1_limit: float = 10.0,
    phase2_limit: float = 5.0,
    workers: int = 16,
    progress: ProgressLog | None = None,
    extra_constraints: Callable[[TimetableModel], None] | None = None,
) -> RepairResult:
    """Re-optimise `disrupted` while staying as close as possible to `baseline`.

    `disrupted` must already have the disruption applied (see
    `disruption.apply_disruptions`).

    `progress` receives one event per real step; passing none changes nothing
    about the search.

    `extra_constraints` is applied to the built model before either phase
    runs. It is how ranked alternatives are produced -- "the best repair that
    differs from the ones already offered" -- without a second repair
    algorithm: the same two phases run, over a narrower model.
    """
    track = progress if progress is not None else ProgressLog()

    with track.stage("validating", "Checking what the disruption broke") as step:
        affected = (
            directly_affected(disrupted, baseline, disruptions) if disruptions else []
        )
        step.note(
            f"{len(affected)} of {len(disrupted.sessions)} published sessions sit "
            f"in a slot the disruption blocks"
        )

    tm = None
    with track.stage("building", "Building the CP-SAT model") as step:
        try:
            tm = TimetableModel(disrupted, weights)
        except ValueError as exc:
            # A session lost every legal placement -- no repair can exist.
            unbuildable = str(exc)
            step.note(f"model cannot be built: {unbuildable}")
        else:
            stats = tm.stats()
            step.note(
                f"{stats['boolean_variables']} boolean variables from "
                f"{stats['surviving_candidates']} candidate placements "
                f"(of {stats['raw_candidates']} raw)"
            )

    if tm is None:
        # Say which rules broke and what would unblock them.
        with track.stage("diagnosing", "Diagnosing why no repair exists") as step:
            report = diagnose(disrupted)
            step.note(report.headline)
        return RepairResult(
            status="INFEASIBLE",
            baseline=baseline,
            directly_affected=affected,
            reason=unbuildable,
            diagnosis=report,
        )

    if extra_constraints is not None:
        extra_constraints(tm)

    time_moves, room_only_moves = _change_expressions(tm, baseline)

    # One time move must outweigh every conceivable room move, so that reducing
    # time moves is always preferred -- lexicographic ordering in one objective.
    room_move_ceiling = len(disrupted.sessions) + 1
    change_cost = room_move_ceiling * time_moves + room_only_moves

    # Phase 1 -- minimise disruption, warm-started from the published schedule.
    with track.stage(
        "minimising-disruption", "Phase 1: minimising moves from the published plan"
    ) as step:
        _hint_from(tm, baseline)
        phase1 = solve(
            tm, objective=change_cost, time_limit=phase1_limit, workers=workers
        )
        step.note(
            f"CP-SAT returned {phase1.status} after "
            f"{phase1.timetable.solve_seconds:.2f}s"
            + (
                " - fewest possible moves proven"
                if phase1.status == "OPTIMAL"
                else " - a repair was found but not proven smallest"
                if phase1.is_solved
                else " - no repair found"
            )
        )

    if not phase1.is_solved:
        with track.stage("diagnosing", "Diagnosing why no repair was found") as step:
            report = diagnose(disrupted)
            step.note(report.headline)
        # INFEASIBLE is a proof; UNKNOWN is only a timeout. Reporting the second
        # as the first would claim something CP-SAT never established.
        return RepairResult(
            status=phase1.status,
            baseline=baseline,
            directly_affected=affected,
            phase1_seconds=phase1.timetable.solve_seconds,
            reason=(
                "No feasible repair exists under the current constraints."
                if phase1.status == "INFEASIBLE"
                else (
                    f"No repair was found within the "
                    f"{phase1_limit:g}s search budget (solver returned "
                    f"{phase1.status}). This does not prove that none exists."
                )
            ),
            diagnosis=report,
            phase1_status=phase1.status,
        )

    # Recompute the achieved disruption from the placements themselves rather
    # than trusting the objective value.
    best = phase1
    diff = diff_schedules(disrupted, baseline, phase1.timetable)
    achieved = room_move_ceiling * diff.time_moves + diff.room_only_moves

    # Phase 2 -- best quality among repairs that are no more disruptive.
    phase2_seconds = 0.0
    phase2_status = "SKIPPED"
    if phase2_limit > 0:
        with track.stage(
            "improving-quality", "Phase 2: best schedule within that move budget"
        ) as step:
            tm.model.Add(change_cost <= achieved)
            _hint_from(tm, phase1.timetable)
            phase2 = solve(tm, time_limit=phase2_limit, workers=workers)
            phase2_seconds = phase2.timetable.solve_seconds
            phase2_status = phase2.status

            accepted = False
            if phase2.is_solved:
                candidate_diff = diff_schedules(disrupted, baseline, phase2.timetable)
                candidate_cost = (
                    room_move_ceiling * candidate_diff.time_moves
                    + candidate_diff.room_only_moves
                )
                # Guard: never accept a "better quality" repair that disrupts more.
                if candidate_cost <= achieved:
                    best = phase2
                    diff = candidate_diff
                    accepted = True
            step.note(
                f"CP-SAT returned {phase2.status} after {phase2_seconds:.2f}s"
                + (
                    " - quality improved without extra moves"
                    if accepted
                    else " - phase 1 result kept"
                )
            )

    with track.stage("measuring-retention", "Measuring schedule retention") as step:
        quality = schedule_metrics(disrupted, best.timetable)
        step.note(
            f"{diff.unchanged} of {diff.total} sessions unchanged = "
            f"{diff.retention_pct:.1f}% retention "
            f"({diff.time_moves} moved time, {diff.room_only_moves} moved room only)"
        )

    # The validator shares no code with the model, so this is a second,
    # independent opinion on the repair rather than the solver grading itself.
    with track.stage(
        "verifying", "Verifying the repaired timetable independently"
    ) as step:
        from .validate import validate

        check = validate(disrupted, best.timetable)
        step.note(
            f"{check.total} hard-rule violations across {len(check.counts)} "
            f"rule families"
        )

    return RepairResult(
        status=best.status,
        baseline=baseline,
        timetable=best.timetable,
        diff=diff,
        quality=quality,
        soft_breakdown=best.soft_breakdown,
        model_stats=best.model_stats,
        directly_affected=affected,
        phase1_seconds=phase1.timetable.solve_seconds,
        phase2_seconds=phase2_seconds,
        phase1_status=phase1.status,
        phase2_status=phase2_status,
    )


def what_if(
    instance: Instance,
    baseline: Timetable,
    disruptions: list[Disruption],
    weights: ObjectiveWeights | None = None,
    phase1_limit: float = 10.0,
    phase2_limit: float = 5.0,
    progress: ProgressLog | None = None,
) -> RepairResult:
    """Preview a disruption without committing to it.

    Identical computation to `repair`; the difference is intent -- the caller
    keeps the published timetable until a coordinator approves the result.
    """
    return repair(
        disrupted=apply_disruptions(instance, disruptions),
        baseline=baseline,
        disruptions=disruptions,
        weights=weights,
        phase1_limit=phase1_limit,
        phase2_limit=phase2_limit,
        progress=progress,
    )
