"""Shared CP-SAT execution: run a built model, extract placements, report status.

Both initial generation and minimum-disruption repair funnel through here, so
solver status and metric extraction are reported identically in both flows.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..domain.models import Instance, Placement, Timetable
from ..progress import ProgressLog
from .diagnose import diagnose
from .model import ObjectiveWeights, TimetableModel
from .validate import validate

STATUS_NAMES = {
    cp_model.OPTIMAL: "OPTIMAL",
    cp_model.FEASIBLE: "FEASIBLE",
    cp_model.INFEASIBLE: "INFEASIBLE",
    cp_model.MODEL_INVALID: "MODEL_INVALID",
    cp_model.UNKNOWN: "UNKNOWN",
}


@dataclass(slots=True)
class SolveOutcome:
    timetable: Timetable
    soft_breakdown: dict[str, int] = field(default_factory=dict)
    model_stats: dict[str, int] = field(default_factory=dict)
    # Only tracked when a caller asked to be told about the first solution:
    # when CP-SAT first held a conflict-free timetable, and how many strictly
    # better ones it found in total. None means "not watched", not "none found".
    first_solution_seconds: float | None = None
    solutions_found: int | None = None

    @property
    def status(self) -> str:
        return self.timetable.status

    @property
    def is_solved(self) -> bool:
        return self.timetable.is_solved


class _SolutionWatch(cp_model.CpSolverSolutionCallback):
    """Counts improving solutions and reports the first one as it happens."""

    def __init__(self, started: float, on_first: Callable[[float, float], None]):
        super().__init__()
        self._started = started
        self._on_first = on_first
        self.count = 0
        self.first_at: float | None = None

    def on_solution_callback(self) -> None:
        self.count += 1
        if self.count == 1:
            self.first_at = time.perf_counter() - self._started
            self._on_first(self.first_at, self.ObjectiveValue())


def _value(solver: cp_model.CpSolver, expr) -> int:
    """Evaluate a term that may have collapsed to a plain int."""
    if isinstance(expr, int):
        return expr
    return int(solver.Value(expr))


def solve(
    tm: TimetableModel,
    objective=None,
    time_limit: float = 30.0,
    workers: int = 8,
    log: bool = False,
    on_first_solution: Callable[[float, float], None] | None = None,
) -> SolveOutcome:
    """Solve `tm`, minimising `objective` (defaults to the soft objective).

    `on_first_solution(seconds, objective)` is called the moment CP-SAT finds
    its first feasible timetable. Without it the solve runs exactly as it
    always has, with no callback attached.
    """
    if objective is None:
        objective = tm.soft_objective
    tm.model.Minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.log_search_progress = log

    started = time.perf_counter()
    watch = (
        _SolutionWatch(started, on_first_solution)
        if on_first_solution is not None
        else None
    )
    status = solver.Solve(tm.model, watch) if watch else solver.Solve(tm.model)
    elapsed = time.perf_counter() - started

    status_name = STATUS_NAMES.get(status, "UNKNOWN")
    watched = {
        "first_solution_seconds": watch.first_at if watch else None,
        "solutions_found": watch.count if watch else None,
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return SolveOutcome(
            timetable=Timetable(
                placements={}, status=status_name, solve_seconds=elapsed
            ),
            model_stats=tm.stats(),
            **watched,
        )

    placements: dict[str, Placement] = {}
    for (sid, t, r), var in tm.x.items():
        if solver.Value(var):
            placements[sid] = Placement(session_id=sid, timeslot_id=t, room_id=r)

    breakdown = {term.name: _value(solver, term.expr) for term in tm.soft_terms}

    return SolveOutcome(
        timetable=Timetable(
            placements=placements,
            status=status_name,
            objective=solver.ObjectiveValue(),
            best_bound=solver.BestObjectiveBound(),
            solve_seconds=elapsed,
        ),
        soft_breakdown=breakdown,
        model_stats=tm.stats(),
        **watched,
    )


def generate(
    instance: Instance,
    weights: ObjectiveWeights | None = None,
    time_limit: float = 30.0,
    workers: int = 8,
    log: bool = False,
    progress: ProgressLog | None = None,
) -> SolveOutcome:
    """Step 2 of the coordinator flow: build a conflict-free timetable from
    scratch, optimising only for schedule quality.

    `progress` receives one event per real step. Passing none still runs the
    same steps; nothing is reported.
    """
    track = progress if progress is not None else ProgressLog()

    with track.stage("validating", "Validating the department data") as step:
        # A necessary-condition scan: it cannot prove a timetable exists, but it
        # names any rule category that makes one impossible before the model is
        # built. Costs ~35ms against a ~20s solve.
        report = diagnose(instance)
        blocking = report.blocking
        step.note(
            f"{len(instance.sessions)} sessions, {len(instance.faculty)} faculty, "
            f"{len(instance.rooms)} rooms, {instance.total_contact_hours} contact "
            f"hours over {len(instance.calendar.teaching_slots)} teaching periods"
            + (
                f" - {len(blocking)} blocking rule category("
                f"{', '.join(sorted({f.category for f in blocking}))})"
                if blocking
                else " - no blocking rule category found"
            )
        )

    with track.stage("building", "Building the CP-SAT model") as step:
        tm = TimetableModel(instance, weights)
        stats = tm.stats()
        step.note(
            f"{stats['boolean_variables']} boolean variables from "
            f"{stats['surviving_candidates']} candidate placements "
            f"(of {stats['raw_candidates']} raw)"
            + (
                f", {stats['locked_sessions']} locked session(s)"
                if stats["locked_sessions"]
                else ""
            )
        )

    # Every hard rule holds from CP-SAT's first solution on; after that it only
    # improves quality. The callback marks that moment as it happens, so the
    # two stages are the solver's own boundary rather than a guess.
    track.begin("searching", "Finding a conflict-free timetable")

    def first_found(elapsed: float, objective: float) -> None:
        track.end(
            "searching",
            f"first conflict-free timetable after {elapsed:.2f}s "
            f"(objective {objective:.0f})",
        )
        track.begin("optimising", "Optimising schedule quality")

    out = solve(
        tm,
        time_limit=time_limit,
        workers=workers,
        log=log,
        on_first_solution=first_found,
    )

    tt = out.timetable
    detail = f"CP-SAT returned {out.status} after {tt.solve_seconds:.2f}s"
    if tt.objective is not None:
        detail += f", objective {tt.objective:.0f}"
        if tt.best_bound is not None:
            detail += f" (best bound {tt.best_bound:.0f})"
    if out.solutions_found:
        detail += f", {out.solutions_found} improving solution(s) found"
    if track.is_open("searching"):
        track.end("searching", f"{detail} - no timetable found")
    else:
        track.end("optimising", detail)

    with track.stage("verifying", "Verifying the timetable independently") as step:
        if out.is_solved:
            check = validate(instance, tt)
            step.note(
                f"{len(tt.placements)}/{len(instance.sessions)} sessions placed, "
                f"{check.total} hard-rule violations across {len(check.counts)} "
                f"rule families"
            )
        else:
            step.note(f"nothing to verify: CP-SAT returned {out.status}")

    return out
