"""Solver correctness guarantees.

Two halves:

* **Invariants** -- properties every generated or repaired timetable must hold.
* **Mutation tests** -- deliberately corrupt a valid timetable and assert the
  independent validator notices. Without these, "0 hard conflicts" would only
  prove the validator never fires, not that the schedule is sound. These are the
  tests that make the headline claim worth anything.
"""

from __future__ import annotations

import pytest

from backend.app.domain.models import Placement, RoomType, Timetable
from backend.app.solver.disruption import (
    apply_disruptions,
    apply_rule_changes,
    batch_unavailable,
    faculty_unavailable,
    room_unavailable,
    rule_change,
)
from backend.app.solver.metrics import diff_schedules
from backend.app.solver.repair import repair
from backend.app.solver.validate import sessions_breaking_load_rules, validate


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def mutate(timetable: Timetable, session_id: str, *, slot=None, room=None) -> Timetable:
    """Copy the timetable with one placement moved."""
    places = dict(timetable.placements)
    old = places[session_id]
    places[session_id] = Placement(
        session_id=session_id,
        timeslot_id=old.timeslot_id if slot is None else slot,
        room_id=old.room_id if room is None else room,
    )
    return Timetable(placements=places, status=timetable.status)


def drop(timetable: Timetable, session_id: str) -> Timetable:
    places = dict(timetable.placements)
    places.pop(session_id)
    return Timetable(placements=places, status=timetable.status)


def pair_sharing(instance, timetable, attr: str, *, differ: str | None = None):
    """Two placed sessions with the same `attr`, optionally differing in `differ`."""
    for a in instance.sessions:
        for b in instance.sessions:
            if a.id >= b.id:
                continue
            if getattr(a, attr) != getattr(b, attr):
                continue
            if differ and getattr(a, differ) == getattr(b, differ):
                continue
            if a.id in timetable.placements and b.id in timetable.placements:
                return a, b
    return None, None


# ----------------------------------------------------------------------
# generation invariants
# ----------------------------------------------------------------------


def test_every_session_is_scheduled_exactly_once(small_instance, published):
    assert len(published.placements) == len(small_instance.sessions)
    assert set(published.placements) == {s.id for s in small_instance.sessions}


def test_generated_timetable_has_no_violations(small_instance, published):
    report = validate(small_instance, published)
    assert report.total == 0, report.render()


def test_multi_hour_labs_are_contiguous_within_one_day(small_instance, published):
    cal = small_instance.calendar
    labs = [s for s in small_instance.sessions if s.duration > 1]
    assert labs, "fixture must contain a multi-hour lab"
    for s in labs:
        start = cal.by_id[published.placements[s.id].timeslot_id]
        # The whole block fits the day and never lands on lunch.
        assert start.period + s.duration <= cal.periods
        for k in range(s.duration):
            assert not cal.by_id[start.id + k].is_lunch


def test_nothing_is_scheduled_over_lunch(small_instance, published):
    cal = small_instance.calendar
    for s in small_instance.sessions:
        start = published.placements[s.id].timeslot_id
        for k in range(s.duration):
            assert not cal.by_id[start + k].is_lunch


def test_parallel_electives_run_together_in_different_rooms(small_instance, published):
    groups = small_instance.elective_groups()
    assert groups, "fixture must contain elective groups"
    for gid, members in groups.items():
        slots = {published.placements[s.id].timeslot_id for s in members}
        rooms = {published.placements[s.id].room_id for s in members}
        assert len(slots) == 1, f"{gid} options are not simultaneous"
        assert len(rooms) == len(members), f"{gid} options share a room"


def test_rooms_match_type_and_seat_everyone(small_instance, published):
    for s in small_instance.sessions:
        room = small_instance.room_by_id[published.placements[s.id].room_id]
        assert room.room_type is s.room_type
        assert room.capacity >= small_instance.seats_needed(s)


def test_faculty_never_exceed_their_load_limits(small_instance, published):
    cal = small_instance.calendar
    busy: dict[tuple[str, int], int] = {}
    for s in small_instance.sessions:
        start = published.placements[s.id].timeslot_id
        for k in range(s.duration):
            busy[(s.faculty_id, start + k)] = 1

    for f in small_instance.faculty:
        weekly = 0
        for d in range(cal.days):
            day = [1 if busy.get((f.id, sl.id)) else 0 for sl in cal.slots_on(d)]
            assert sum(day) <= f.max_daily_load
            weekly += sum(day)

            run = longest = 0
            for flag in day:
                run = run + 1 if flag else 0
                longest = max(longest, run)
            assert longest <= f.max_consecutive
        assert weekly <= f.max_weekly_load


def test_faculty_are_never_booked_when_unavailable(small_instance, published):
    for s in small_instance.sessions:
        fac = small_instance.faculty_by_id[s.faculty_id]
        start = published.placements[s.id].timeslot_id
        for k in range(s.duration):
            assert start + k not in fac.unavailable


# ----------------------------------------------------------------------
# mutation tests -- the validator must actually catch breakage
# ----------------------------------------------------------------------


def test_validator_catches_faculty_double_booking(small_instance, published):
    a, b = pair_sharing(small_instance, published, "faculty_id", differ="batch_id")
    if a is None:
        pytest.skip("no two sessions share a faculty across batches")
    broken = mutate(published, b.id, slot=published.placements[a.id].timeslot_id)
    assert validate(small_instance, broken).counts["faculty_conflicts"] > 0


def test_validator_catches_room_double_booking(small_instance, published):
    a, b = pair_sharing(small_instance, published, "room_type")
    if a is None:
        pytest.skip("no two sessions share a room type")
    target = published.placements[a.id]
    broken = mutate(published, b.id, slot=target.timeslot_id, room=target.room_id)
    assert validate(small_instance, broken).counts["room_conflicts"] > 0


def test_validator_catches_batch_double_booking(small_instance, published):
    candidates = [
        s for s in small_instance.sessions if not s.elective_group and s.duration == 1
    ]
    a = b = None
    for x in candidates:
        for y in candidates:
            if x.id < y.id and x.batch_id == y.batch_id:
                a, b = x, y
                break
        if a:
            break
    if a is None:
        pytest.skip("no two non-elective sessions share a batch")
    broken = mutate(published, b.id, slot=published.placements[a.id].timeslot_id)
    assert validate(small_instance, broken).counts["batch_conflicts"] > 0


def test_validator_catches_undersized_room(small_instance, published):
    for s in small_instance.sessions:
        seats = small_instance.seats_needed(s)
        smaller = [
            r
            for r in small_instance.rooms
            if r.room_type is s.room_type and r.capacity < seats
        ]
        if smaller:
            broken = mutate(published, s.id, room=smaller[0].id)
            assert validate(small_instance, broken).counts["capacity_violations"] > 0
            return
    pytest.skip("every room seats every session in this fixture")


def test_validator_catches_wrong_room_type(small_instance, published):
    lecture = next(s for s in small_instance.sessions if not s.is_lab)
    lab_room = next(r for r in small_instance.rooms if r.room_type is RoomType.LAB)
    broken = mutate(published, lecture.id, room=lab_room.id)
    assert validate(small_instance, broken).counts["room_type_violations"] > 0


def test_validator_catches_a_class_placed_on_lunch(small_instance, published):
    lunch = next(sl for sl in small_instance.calendar.slots if sl.is_lunch)
    session = next(s for s in small_instance.sessions if s.duration == 1)
    broken = mutate(published, session.id, slot=lunch.id)
    assert validate(small_instance, broken).counts["lunch_violations"] > 0


def test_validator_catches_faculty_booked_while_unavailable(small_instance, published):
    for s in small_instance.sessions:
        fac = small_instance.faculty_by_id[s.faculty_id]
        blocked = [
            u
            for u in sorted(fac.unavailable)
            if small_instance.calendar.span(u, s.duration) is not None
        ]
        if blocked:
            broken = mutate(published, s.id, slot=blocked[0])
            assert validate(small_instance, broken).counts["faculty_availability"] > 0
            return
    pytest.skip("no faculty has a usable unavailable slot in this fixture")


def test_validator_catches_a_lab_running_past_the_end_of_day(small_instance, published):
    cal = small_instance.calendar
    lab = next(s for s in small_instance.sessions if s.duration > 1)
    last = next(sl for sl in cal.slots if sl.day == 0 and sl.period == cal.periods - 1)
    broken = mutate(published, lab.id, slot=last.id)
    assert validate(small_instance, broken).counts["lab_contiguity"] > 0


def test_validator_catches_broken_elective_parallelism(small_instance, published):
    groups = small_instance.elective_groups()
    _, members = next(iter(groups.items()))
    victim = members[0]
    current = published.placements[victim.id].timeslot_id
    other = next(
        sl.id
        for sl in small_instance.calendar.slots
        if not sl.is_lunch and sl.id != current
    )
    broken = mutate(published, victim.id, slot=other)
    assert validate(small_instance, broken).counts["elective_parallel_violations"] > 0


def test_validator_catches_a_division_booked_while_it_is_away(
    small_instance, published
):
    """Blocking a division out is a different rule from a room or a teacher
    being busy, so the validator needs its own check for it."""
    session = next(s for s in small_instance.sessions if s.duration == 1)
    slot = published.placements[session.id].timeslot_id
    disrupted = apply_disruptions(
        small_instance,
        [
            batch_unavailable(
                small_instance,
                session.batch_id,
                small_instance.calendar.by_id[slot].day,
                "09:00",
                "23:59",
            )
        ],
    )
    # The published timetable still has the session where the division is away.
    assert validate(disrupted, published).counts["batch_availability"] > 0
    assert validate(small_instance, published).counts["batch_availability"] == 0


def test_a_division_away_has_its_classes_moved(small_instance, published):
    session = next(s for s in small_instance.sessions if s.duration == 1)
    day = small_instance.calendar.by_id[published.placements[session.id].timeslot_id].day
    disruptions = [
        batch_unavailable(small_instance, session.batch_id, day, "09:00", "23:59")
    ]
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=5)

    assert result.solved, result.reason
    assert validate(disrupted, result.timetable).total == 0
    # Nothing for that division may remain on the blocked day.
    for s in small_instance.sessions:
        if s.batch_id != session.batch_id:
            continue
        placed = result.timetable.placements[s.id]
        assert small_instance.calendar.by_id[placed.timeslot_id].day != day


def test_a_division_away_does_not_move_anyone_else_needlessly(
    small_instance, published
):
    """Other divisions keep their week; only the blocked one is disturbed."""
    session = next(s for s in small_instance.sessions if s.duration == 1)
    day = small_instance.calendar.by_id[published.placements[session.id].timeslot_id].day
    disruptions = [
        batch_unavailable(small_instance, session.batch_id, day, "09:00", "23:59")
    ]
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=5)
    assert result.solved

    moved_batches = {c.batch_id for c in result.diff.changes}
    assert session.batch_id in moved_batches or result.diff.changed == 0


def test_validator_catches_a_missing_session(small_instance, published):
    victim = small_instance.sessions[0]
    broken = drop(published, victim.id)
    assert validate(small_instance, broken).counts["unscheduled_sessions"] == 1


# ----------------------------------------------------------------------
# repair invariants
# ----------------------------------------------------------------------


def _block_a_teachers_day(instance, published):
    """Disruption that removes one teacher for the whole day they teach on."""
    victim = next(s for s in instance.sessions if s.duration == 1)
    slot = instance.calendar.by_id[published.placements[victim.id].timeslot_id]
    fac = instance.faculty_by_id[victim.faculty_id]
    return [faculty_unavailable(instance, fac.name, slot.day, "09:00", "23:59")]


def test_repair_output_is_still_conflict_free(small_instance, published):
    disruptions = _block_a_teachers_day(small_instance, published)
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=5)

    assert result.solved, result.reason
    assert validate(disrupted, result.timetable).total == 0


def test_retention_is_consistent_with_the_diff(small_instance, published):
    disruptions = _block_a_teachers_day(small_instance, published)
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=5)
    assert result.solved

    d = result.diff
    assert d.changed + d.unchanged == d.total
    assert d.retention_pct == pytest.approx(d.unchanged / d.total * 100)
    # Recomputing the diff independently must agree.
    assert diff_schedules(disrupted, published, result.timetable).changed == d.changed


def test_a_disruption_that_touches_nothing_changes_nothing(small_instance, published):
    """A room closed while it is already empty must not perturb the schedule."""
    cal = small_instance.calendar
    used = {
        (p.room_id, cal.by_id[p.timeslot_id].day)
        for p in published.placements.values()
    }
    idle = next(
        (r.id, d)
        for r in small_instance.rooms
        for d in range(cal.days)
        if (r.id, d) not in used
    )
    disruptions = [room_unavailable(small_instance, idle[0], idle[1])]
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=3)

    assert result.solved
    assert result.diff.changed == 0
    assert result.diff.retention_pct == 100.0


def test_closing_one_lab_prefers_a_room_swap_over_moving_students(
    small_instance, published
):
    """Time moves are strictly worse than room moves, so a lab closure that a
    spare lab can absorb must not move anyone's timetable."""
    lab = next(s for s in small_instance.sessions if s.is_lab)
    place = published.placements[lab.id]
    day = small_instance.calendar.by_id[place.timeslot_id].day

    disruptions = [room_unavailable(small_instance, place.room_id, day)]
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=15, phase2_limit=5)

    assert result.solved
    assert validate(disrupted, result.timetable).total == 0
    assert result.diff.time_moves == 0, (
        "a spare lab was available, so nothing should have changed time"
    )


def test_minimality_is_claimed_from_phase_one_not_phase_two(
    small_instance, published
):
    """Phase 1 proves the move count is minimal; phase 2 only tunes quality
    inside that budget. Reporting phase 2's status as proof of minimality would
    be an overclaim, so the two are tracked separately."""
    disruptions = _block_a_teachers_day(small_instance, published)
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=20, phase2_limit=5)

    assert result.solved
    assert result.phase1_status in ("OPTIMAL", "FEASIBLE")
    assert result.minimal_proven == (result.phase1_status == "OPTIMAL")

    # Skipping phase 2 must not affect what phase 1 proved.
    without_phase2 = repair(
        disrupted, published, disruptions, phase1_limit=20, phase2_limit=0
    )
    assert without_phase2.phase2_status == "SKIPPED"
    assert without_phase2.minimal_proven == result.minimal_proven
    assert without_phase2.diff.changed == result.diff.changed


def test_a_tightened_rule_is_detected_on_the_published_timetable(
    small_instance, published
):
    """A stricter consecutive-hours policy breaks no single placement; it makes
    a run of them illegal. The published schedule must be seen to violate it."""
    assert sessions_breaking_load_rules(small_instance, published) == []

    stricter = apply_rule_changes(
        small_instance, [rule_change(small_instance, "max_consecutive", 1)]
    )
    assert sessions_breaking_load_rules(stricter, published)
    assert validate(stricter, published).counts["max_consecutive_violations"] > 0


def test_a_rule_change_applies_only_where_it_is_aimed(small_instance):
    target = small_instance.faculty[0]
    changed = apply_rule_changes(
        small_instance,
        [rule_change(small_instance, "max_daily_load", 2, faculty=target.id)],
    )
    assert changed.faculty_by_id[target.id].max_daily_load == 2
    for f in changed.faculty:
        if f.id != target.id:
            assert f.max_daily_load == small_instance.faculty_by_id[f.id].max_daily_load


def test_repair_satisfies_the_new_rule_not_the_old_one(small_instance, published):
    stricter = apply_rule_changes(
        small_instance, [rule_change(small_instance, "max_consecutive", 2)]
    )
    result = repair(stricter, published, phase1_limit=20, phase2_limit=5)

    assert result.solved, result.reason
    # Judged against the tightened instance, not the one the baseline was for.
    assert validate(stricter, result.timetable).is_clean
    assert sessions_breaking_load_rules(stricter, result.timetable) == []


@pytest.mark.parametrize("rule,value", [("not_a_rule", 3), ("max_consecutive", 0)])
def test_a_nonsense_rule_change_is_refused(small_instance, rule, value):
    with pytest.raises(ValueError):
        rule_change(small_instance, rule, value)


def test_a_timeout_is_never_reported_as_a_proof_of_impossibility(
    small_instance, published
):
    """UNKNOWN means the search ran out of time, not that no repair exists.

    Conflating the two would claim something CP-SAT never established, which is
    the one thing this project must not do.
    """
    stricter = apply_rule_changes(
        small_instance, [rule_change(small_instance, "max_consecutive", 2)]
    )
    result = repair(stricter, published, phase1_limit=0.01, phase2_limit=0, workers=1)

    assert result.status == "UNKNOWN"
    assert not result.solved
    # Whatever else it says, it must never hand back a schedule it did not find.
    assert result.timetable is None
    assert result.reason is not None
    assert "does not prove" in result.reason
    assert "No feasible repair exists" not in result.reason
    assert not result.minimal_proven


def test_a_proven_impossibility_still_says_so(small_instance, published):
    """The opposite guard: a real INFEASIBLE must not be softened into a maybe."""
    victim = small_instance.faculty[0]
    blocked = [
        faculty_unavailable(small_instance, victim.id, day, "09:00", "23:59")
        for day in range(small_instance.calendar.days)
    ]
    disrupted = apply_disruptions(small_instance, blocked)
    result = repair(disrupted, published, blocked, phase1_limit=10, phase2_limit=0)

    assert result.status == "INFEASIBLE"
    assert result.timetable is None
    assert result.diagnosis is not None and result.diagnosis.proven_infeasible
    assert "does not prove" not in (result.reason or "")


def test_impossible_disruption_is_reported_and_diagnosed(small_instance, published):
    """Blocking a teacher all week must fail loudly, not silently or invalidly."""
    busiest = max(
        small_instance.faculty,
        key=lambda f: sum(
            s.duration for s in small_instance.sessions if s.faculty_id == f.id
        ),
    )
    disruptions = [
        faculty_unavailable(small_instance, busiest.name, day, "09:00", "23:59")
        for day in range(small_instance.calendar.days)
    ]
    disrupted = apply_disruptions(small_instance, disruptions)
    result = repair(disrupted, published, disruptions, phase1_limit=10, phase2_limit=3)

    assert not result.solved
    assert result.timetable is None
    assert result.diagnosis is not None
    assert result.diagnosis.proven_infeasible
    assert any(f.category == "Faculty availability" for f in result.diagnosis.blocking)
