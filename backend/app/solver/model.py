"""The CP-SAT constraint model.

Design notes that matter when explaining this to a judge:

* One Boolean `x[session, start_timeslot, room]` per *surviving* candidate. Rules
  that depend only on a single placement -- room type, capacity, equipment,
  rooms out of service, faculty and room unavailability, lunch protection, lab
  contiguity, day boundaries, coordinator locks -- are applied as candidate
  filters, so forbidden placements never become variables. That is what keeps
  the model far smaller than the 156 x 40 x 20 upper bound.

* Rules that couple placements -- clashes, consecutive hours, daily/weekly load,
  elective parallelism -- become real constraints.

* Occupancy Booleans do double duty: `Add(sum(covering placements) == occ)` with
  `occ` Boolean simultaneously *enforces* the no-double-booking rule and gives the
  objective a clean handle on "is this entity busy at this slot".

* Every division of every year shares these occupancy variables, so a teacher
  or lab shared across years can never be double-booked between them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from ..domain.models import Instance, Room, Session
from .locks import lock_blockers, lock_placements


@dataclass(frozen=True, slots=True)
class ObjectiveWeights:
    """Soft-objective weights. Coordinators tune these and re-solve."""

    student_gaps: int = 6
    faculty_gaps: int = 2
    faculty_load_balance: int = 2
    subject_spread: int = 3
    last_slot: int = 1
    # Empty seats, counted in tens: a 32-student cohort in a 120-seat hall
    # costs 8, in a 40-seat room it costs 0.
    room_wastage: int = 1
    # Periods a teacher asked to keep free (availability itself is hard).
    faculty_preferences: int = 2

    # Target daily teaching hours per faculty; load above this is penalised.
    faculty_daily_target: int = 3


@dataclass(slots=True)
class SoftTerm:
    name: str
    expr: object  # cp_model linear expression
    weight: int
    upper_bound: int


def room_fits(instance: Instance, session: Session, room: Room, seats: int) -> bool:
    """Whether `room` can ever host `session`: in service, right type, big
    enough, and equipped for what the session needs."""
    return (
        room.active
        and room.room_type is session.room_type
        and room.capacity >= seats
        and (
            not session.required_capability
            or session.required_capability in room.capabilities
        )
    )


class TimetableModel:
    """Builds the shared constraint model used by both generation and repair."""

    def __init__(
        self,
        instance: Instance,
        weights: ObjectiveWeights | None = None,
        max_rooms_per_session: int | None = 3,
    ):
        self.instance = instance
        self.weights = weights or ObjectiveWeights()
        # Rooms of the same type that all fit a session are interchangeable, and
        # that symmetry dominates search cost. Offering each session only its
        # tightest-fitting rooms collapses the symmetry and, as a side effect,
        # stops a 31-student elective from booking an 80-seat hall. Set to None
        # to consider every suitable room.
        self.max_rooms_per_session = max_rooms_per_session
        self.model = cp_model.CpModel()

        self.x: dict[tuple[str, int, str], cp_model.IntVar] = {}
        self.candidates: dict[str, list[tuple[int, str]]] = {}
        self.group_start: dict[tuple[str, int], cp_model.IntVar] = {}
        self.batch_occ: dict[tuple[str, int], cp_model.IntVar] = {}
        self.faculty_occ: dict[tuple[str, int], cp_model.IntVar] = {}
        self.room_occ: dict[tuple[str, int], cp_model.IntVar] = {}
        self.soft_terms: list[SoftTerm] = []
        self.raw_candidate_count = 0
        self.locked: set[str] = set()

        self._build_candidates()
        self._add_placement_constraints()
        self._add_elective_parallelism()
        self._add_occupancy_and_clashes()
        self._add_load_constraints()
        self._build_soft_objective()

    # ------------------------------------------------------------------
    # Candidate generation -- this is where single-placement rules are applied
    # ------------------------------------------------------------------

    def _build_candidates(self) -> None:
        inst = self.instance
        cal = inst.calendar

        for s in inst.sessions:
            fac = inst.faculty_by_id[s.faculty_id]
            batch = inst.batch_by_id[s.batch_id]
            seats = inst.seats_needed(s)
            options: list[tuple[int, str]] = []

            # A locked session is offered its locked placement and nothing else,
            # which is what makes a lock a hard constraint rather than a hint.
            lock = inst.locks.get(s.id)
            if lock is not None:
                self.raw_candidate_count += len(cal.slots) * len(inst.rooms)
                problems = lock_blockers(inst, lock)
                if problems:
                    where = (
                        cal.by_id[lock.timeslot_id].label
                        if lock.timeslot_id in cal.by_id
                        else f"slot {lock.timeslot_id}"
                    )
                    room = f" in {lock.room_id}" if lock.room_id else ""
                    raise ValueError(
                        f"Session {s.id} ({s.subject_name}, batch {s.batch_id}) is "
                        f"locked at {where}{room}, but {'; '.join(problems)}. "
                        f"Unlock it or relax the conflicting rule."
                    )
                options = lock_placements(inst, lock)
                self.locked.add(s.id)
                self.candidates[s.id] = options
                for t, r in options:
                    self.x[(s.id, t, r)] = self.model.NewBoolVar(f"x[{s.id},{t},{r}]")
                continue

            # Type, capacity, equipment and service status do not vary by
            # timeslot, so resolve the eligible room shortlist once, smallest
            # adequate room first.
            eligible = sorted(
                (r for r in inst.rooms if room_fits(inst, s, r, seats)),
                key=lambda r: (r.capacity, r.id),
            )
            if self.max_rooms_per_session is not None:
                eligible = eligible[: self.max_rooms_per_session]

            for slot in cal.slots:
                self.raw_candidate_count += len(inst.rooms)

                # Lab contiguity, lunch protection and day boundaries: span()
                # returns None when the block is not a legal contiguous run.
                span = cal.span(slot.id, s.duration)
                if span is None:
                    continue
                # Faculty and division must both be free for the whole block.
                if any(u in fac.unavailable for u in span):
                    continue
                if any(u in batch.unavailable for u in span):
                    continue

                for room in eligible:
                    if any(u in room.unavailable for u in span):
                        continue
                    options.append((slot.id, room.id))

            if not options:
                raise ValueError(
                    f"Session {s.id} ({s.subject_name}, batch {s.batch_id}) has no "
                    f"legal placement before solving. Check room type/capacity, "
                    f"equipment and faculty availability."
                )

            self.candidates[s.id] = options
            for t, r in options:
                self.x[(s.id, t, r)] = self.model.NewBoolVar(f"x[{s.id},{t},{r}]")

    def _span(self, session: Session, start: int) -> tuple[int, ...]:
        span = self.instance.calendar.span(start, session.duration)
        assert span is not None  # candidates were filtered on exactly this
        return span

    # ------------------------------------------------------------------
    # Hard constraints
    # ------------------------------------------------------------------

    def _add_placement_constraints(self) -> None:
        """Every session is scheduled exactly once."""
        for s in self.instance.sessions:
            self.model.AddExactlyOne(
                self.x[(s.id, t, r)] for t, r in self.candidates[s.id]
            )

    def _add_elective_parallelism(self) -> None:
        """Options within an elective group must run at the same time, so the
        division can split between them. Distinct rooms follow from room clash."""
        for gid, members in self.instance.elective_groups().items():
            durations = {s.duration for s in members}
            if len(durations) != 1:
                raise ValueError(f"Elective group {gid} mixes session durations")

            per_member_starts = [
                {t for t, _ in self.candidates[s.id]} for s in members
            ]
            starts = sorted(set.intersection(*per_member_starts))
            if not starts:
                raise ValueError(
                    f"Elective group {gid} has no timeslot where every option "
                    f"can run in parallel."
                )

            for t in starts:
                self.group_start[(gid, t)] = self.model.NewBoolVar(f"g[{gid},{t}]")

            # Each option's start distribution is exactly the group's start.
            for s in members:
                for t in {t for t, _ in self.candidates[s.id]}:
                    covering = [
                        self.x[(s.id, tt, r)]
                        for tt, r in self.candidates[s.id]
                        if tt == t
                    ]
                    gvar = self.group_start.get((gid, t))
                    if gvar is None:
                        # No parallel start possible here -- forbid it outright.
                        for v in covering:
                            self.model.Add(v == 0)
                    else:
                        self.model.Add(sum(covering) == gvar)

            self.model.AddExactlyOne(self.group_start[(gid, t)] for t in starts)

    def _add_occupancy_and_clashes(self) -> None:
        """Faculty / room / batch may hold at most one thing per timeslot.

        Declaring occupancy as a Boolean and equating it to the sum of covering
        placements enforces `sum <= 1` and exposes the occupancy for objectives.
        """
        inst = self.instance
        cal = inst.calendar

        fac_cover: dict[tuple[str, int], list] = {}
        room_cover: dict[tuple[str, int], list] = {}
        batch_cover: dict[tuple[str, int], list] = {}

        for s in inst.sessions:
            for t, r in self.candidates[s.id]:
                var = self.x[(s.id, t, r)]
                for u in self._span(s, t):
                    fac_cover.setdefault((s.faculty_id, u), []).append(var)
                    room_cover.setdefault((r, u), []).append(var)
                    if not s.elective_group:
                        batch_cover.setdefault((s.batch_id, u), []).append(var)

        # Elective groups consume exactly one batch slot no matter how many
        # options run inside them.
        groups = inst.elective_groups()
        for (gid, t), gvar in self.group_start.items():
            members = groups[gid]
            span = cal.span(t, members[0].duration)
            assert span is not None
            for u in span:
                batch_cover.setdefault((members[0].batch_id, u), []).append(gvar)

        for f in inst.faculty:
            for slot in cal.slots:
                occ = self.model.NewBoolVar(f"focc[{f.id},{slot.id}]")
                self.faculty_occ[(f.id, slot.id)] = occ
                self.model.Add(sum(fac_cover.get((f.id, slot.id), [])) == occ)

        for room in inst.rooms:
            for slot in cal.slots:
                occ = self.model.NewBoolVar(f"rocc[{room.id},{slot.id}]")
                self.room_occ[(room.id, slot.id)] = occ
                self.model.Add(sum(room_cover.get((room.id, slot.id), [])) == occ)

        for b in inst.batches:
            for slot in cal.slots:
                occ = self.model.NewBoolVar(f"bocc[{b.id},{slot.id}]")
                self.batch_occ[(b.id, slot.id)] = occ
                self.model.Add(sum(batch_cover.get((b.id, slot.id), [])) == occ)

    def _add_load_constraints(self) -> None:
        """Maximum consecutive teaching hours, plus daily and weekly load caps."""
        inst = self.instance
        cal = inst.calendar

        for f in inst.faculty:
            week = []
            for d in range(cal.days):
                day_occ = [self.faculty_occ[(f.id, s.id)] for s in cal.slots_on(d)]
                week.extend(day_occ)

                self.model.Add(sum(day_occ) <= f.max_daily_load)

                # No run longer than max_consecutive: every window of
                # (max_consecutive + 1) periods must contain at least one gap.
                window = f.max_consecutive + 1
                if window <= len(day_occ):
                    for start in range(len(day_occ) - window + 1):
                        self.model.Add(
                            sum(day_occ[start : start + window]) <= f.max_consecutive
                        )

            self.model.Add(sum(week) <= f.max_weekly_load)

    # ------------------------------------------------------------------
    # Soft objectives
    # ------------------------------------------------------------------

    def _gap_vars(self, occ_lookup, entity_id: str, prefix: str) -> list:
        """Idle periods that sit *between* two busy periods on the same day.

        gap[p] == 1 iff (busy before p) and (busy after p) and (free at p).
        Only the lower bound is asserted; minimisation drives it to exact.
        """
        cal = self.instance.calendar
        gaps = []

        for d in range(cal.days):
            occ = [occ_lookup[(entity_id, s.id)] for s in cal.slots_on(d)]
            n = len(occ)

            for p in range(1, n - 1):
                before = self.model.NewBoolVar(f"{prefix}bef[{entity_id},{d},{p}]")
                after = self.model.NewBoolVar(f"{prefix}aft[{entity_id},{d},{p}]")
                self.model.AddMaxEquality(before, occ[:p])
                self.model.AddMaxEquality(after, occ[p + 1 :])

                gap = self.model.NewBoolVar(f"{prefix}gap[{entity_id},{d},{p}]")
                self.model.Add(gap >= before + after + (1 - occ[p]) - 2)
                gaps.append(gap)

        return gaps

    def _build_soft_objective(self) -> None:
        inst = self.instance
        cal = inst.calendar
        w = self.weights

        # 1. Student idle periods.
        student_gaps = []
        for b in inst.batches:
            student_gaps.extend(self._gap_vars(self.batch_occ, b.id, "s"))
        self._add_soft(
            "student_gaps", sum(student_gaps), w.student_gaps, len(student_gaps)
        )

        # 2. Faculty idle windows.
        faculty_gaps = []
        for f in inst.faculty:
            faculty_gaps.extend(self._gap_vars(self.faculty_occ, f.id, "f"))
        self._add_soft(
            "faculty_gaps", sum(faculty_gaps), w.faculty_gaps, len(faculty_gaps)
        )

        # 3. Daily load balance: hours above the comfortable daily target.
        excesses = []
        bound = 0
        for f in inst.faculty:
            for d in range(cal.days):
                day_occ = [self.faculty_occ[(f.id, s.id)] for s in cal.slots_on(d)]
                excess = self.model.NewIntVar(0, f.max_daily_load, f"exc[{f.id},{d}]")
                self.model.Add(excess >= sum(day_occ) - w.faculty_daily_target)
                excesses.append(excess)
                bound += f.max_daily_load
        self._add_soft(
            "faculty_load_balance", sum(excesses), w.faculty_load_balance, bound
        )

        # 4. Subject spread: discourage repeating a subject for a batch same day.
        dups = []
        bound = 0
        by_key: dict[tuple[str, str], list[Session]] = {}
        for s in inst.sessions:
            by_key.setdefault((s.batch_id, s.subject_code), []).append(s)

        for (batch_id, code), group in by_key.items():
            if len(group) < 2:
                continue
            for d in range(cal.days):
                day_ids = {sl.id for sl in cal.slots_on(d)}
                same_day = [
                    self.x[(s.id, t, r)]
                    for s in group
                    for t, r in self.candidates[s.id]
                    if t in day_ids
                ]
                if len(same_day) < 2:
                    continue
                dup = self.model.NewIntVar(
                    0, len(group), f"dup[{batch_id},{code},{d}]"
                )
                self.model.Add(dup >= sum(same_day) - 1)
                dups.append(dup)
                bound += len(group)
        self._add_soft("subject_spread", sum(dups), w.subject_spread, bound)

        # 5. Last period of the day is unpopular.
        last_ids = {sl.id for sl in cal.slots if sl.period == cal.periods - 1}
        last_vars = [
            self.x[(s.id, t, r)]
            for s in inst.sessions
            for t, r in self.candidates[s.id]
            if t in last_ids
        ]
        self._add_soft("last_slot", sum(last_vars), w.last_slot, len(inst.sessions))

        # 6. Room suitability. Each placement is charged for its empty seats in
        # tens, so among rooms that all fit, the tightest one is preferred --
        # the solver weighs this against everything else rather than following
        # a fixed rule. The shortlist above bounds how bad a choice can be; this
        # term decides among what remains.
        waste_terms = []
        bound = 0
        for s in inst.sessions:
            seats = inst.seats_needed(s)
            worst = 0
            for t, r in self.candidates[s.id]:
                cost = max(0, inst.room_by_id[r].capacity - seats) // 10
                if cost:
                    waste_terms.append(cost * self.x[(s.id, t, r)])
                worst = max(worst, cost)
            bound += worst
        self._add_soft("room_wastage", sum(waste_terms), w.room_wastage, bound)

        # 7. Faculty preferences: teaching in a period the teacher asked to keep
        # free is allowed, but costs.
        preferred = [
            self.faculty_occ[(f.id, u)]
            for f in inst.faculty
            for u in sorted(f.preferred_off)
            if (f.id, u) in self.faculty_occ
        ]
        self._add_soft(
            "faculty_preferences", sum(preferred), w.faculty_preferences, len(preferred)
        )

    def _add_soft(self, name: str, expr, weight: int, upper_bound: int) -> None:
        self.soft_terms.append(
            SoftTerm(name=name, expr=expr, weight=weight, upper_bound=upper_bound)
        )

    # ------------------------------------------------------------------

    @property
    def soft_objective(self):
        return sum(term.weight * term.expr for term in self.soft_terms)

    @property
    def soft_upper_bound(self) -> int:
        """Worst possible weighted soft cost. Used to pick a disruption penalty
        large enough that minimising change strictly dominates comfort."""
        return sum(term.weight * term.upper_bound for term in self.soft_terms) + 1

    def stats(self) -> dict[str, int]:
        return {
            "raw_candidates": self.raw_candidate_count,
            "surviving_candidates": len(self.x),
            "sessions": len(self.instance.sessions),
            "locked_sessions": len(self.locked),
            "boolean_variables": len(self.x)
            + len(self.batch_occ)
            + len(self.faculty_occ)
            + len(self.room_occ)
            + len(self.group_start),
        }
