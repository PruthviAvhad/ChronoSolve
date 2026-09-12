"""Institution-level scheduling.

Several years share one pool of teachers and rooms, rooms differ in size and
equipment, and a coordinator can pin sessions in place. Each test here checks a
property of real solver output, not of the model's construction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

import pytest

from backend.app.data.store import (
    instance_from_dict,
    instance_to_dict,
    load_snapshot,
)
from backend.app.data.synthetic import DepartmentSpec, build_department
from backend.app.domain.models import (
    PERIOD_END,
    PERIOD_START,
    Batch,
    Calendar,
    Faculty,
    Instance,
    Lock,
    Placement,
    Room,
    RoomType,
    Session,
    Timetable,
)
from backend.app.solver.diagnose import diagnose
from backend.app.solver.disruption import apply_disruptions, faculty_unavailable
from backend.app.solver.engine import generate, solve
from backend.app.solver.explain import explain_session
from backend.app.solver.metrics import schedule_metrics
from backend.app.solver.model import ObjectiveWeights, TimetableModel
from backend.app.solver.repair import repair
from backend.app.solver.validate import validate

# Six divisions over three years, deliberately short of teachers so that
# nearly every teacher serves more than one year.
THREE_YEARS = DepartmentSpec(
    seed=5,
    n_batches=6,
    theory_per_batch=2,
    sessions_per_theory=2,
    labs_per_batch=1,
    lab_duration=2,
    elective_options=2,
    sessions_per_elective=1,
    n_faculty=6,
)


@pytest.fixture(scope="module")
def institution():
    inst = build_department(THREE_YEARS)
    out = generate(inst, time_limit=20.0, workers=8)
    assert out.is_solved, out.status
    return inst, out.timetable


def _spans(inst: Instance, tt: Timetable, key):
    """(entity, timeslot) -> sessions occupying it, for a chosen entity."""
    at = defaultdict(list)
    for s in inst.sessions:
        p = tt.placements[s.id]
        for k in range(s.duration):
            at[(key(s, p), p.timeslot_id + k)].append(s)
    return at


def _tiny(**overrides) -> Instance:
    """One 30-student division, three lecture rooms of very different sizes,
    and two labs of which only one carries networking kit."""
    rooms = [
        Room("R32", "Room 32", 32, RoomType.LECTURE),
        Room("R60", "Room 60", 60, RoomType.LECTURE),
        Room("R90", "Room 90", 90, RoomType.LECTURE),
        Room("LAB-A", "Lab A", 40, RoomType.LAB, capabilities=frozenset({"computer"})),
        Room(
            "LAB-N",
            "Networks Lab",
            40,
            RoomType.LAB,
            capabilities=frozenset({"computer", "networking"}),
        ),
    ]
    faculty = [Faculty("F1", "Prof. One"), Faculty("F2", "Prof. Two")]
    batches = [Batch("X-A", "X Div A", 30, year=2, year_label="Second Year", semester=3)]
    sessions = [
        Session("X-A-T1-1", "T1", "Theory One", "X-A", "F1"),
        Session("X-A-T1-2", "T1", "Theory One", "X-A", "F1"),
        Session(
            "X-A-L1-1",
            "L1",
            "Networks Lab",
            "X-A",
            "F2",
            duration=2,
            room_type=RoomType.LAB,
            required_capability="networking",
        ),
    ]
    data = dict(
        name="tiny",
        calendar=Calendar(),
        rooms=rooms,
        faculty=faculty,
        batches=batches,
        sessions=sessions,
    )
    data.update(overrides)
    return Instance(**data)


# --------------------------------------------------------------------------
# Academic structure and shared resources
# --------------------------------------------------------------------------


def test_every_division_knows_its_place_in_the_institution(institution):
    inst, _ = institution
    years = {(b.year, b.year_label, b.semester) for b in inst.batches}
    assert years == {(2, "Second Year", 3), (3, "Third Year", 5), (4, "Final Year", 7)}
    assert {b.department for b in inst.batches} == {"Computer Engineering"}


def test_teachers_really_are_shared_across_years(institution):
    """Precondition for the clash tests: otherwise they would prove nothing."""
    inst, _ = institution
    years_of = defaultdict(set)
    for s in inst.sessions:
        years_of[s.faculty_id].add(inst.batch_by_id[s.batch_id].year)
    assert sum(1 for ys in years_of.values() if len(ys) > 1) >= 3


def test_a_teacher_shared_across_years_is_never_double_booked(institution):
    inst, tt = institution
    at = _spans(inst, tt, lambda s, p: s.faculty_id)
    clashes = [
        (fid, slot)
        for (fid, slot), sessions in at.items()
        if len(sessions) > 1
    ]
    assert clashes == []
    assert validate(inst, tt).counts["faculty_conflicts"] == 0


def test_a_room_shared_across_years_is_never_double_booked(institution):
    inst, tt = institution
    at = _spans(inst, tt, lambda s, p: p.room_id)
    shared = {
        room
        for (room, _), sessions in at.items()
        for s in sessions
    }
    years_in_room = defaultdict(set)
    for (room, _), sessions in at.items():
        for s in sessions:
            years_in_room[room].add(inst.batch_by_id[s.batch_id].year)
    assert any(len(ys) > 1 for ys in years_in_room.values()), "no room is shared"
    assert shared
    assert [k for k, v in at.items() if len(v) > 1] == []


def test_all_years_are_solved_as_one_timetable(institution):
    inst, tt = institution
    assert len(tt.placements) == len(inst.sessions)
    assert validate(inst, tt).is_clean


# --------------------------------------------------------------------------
# Rooms: size, type, equipment, service
# --------------------------------------------------------------------------


def test_every_session_sits_in_a_room_that_fits_it(institution):
    inst, tt = institution
    for s in inst.sessions:
        room = inst.room_by_id[tt.placements[s.id].room_id]
        assert room.room_type is s.room_type
        assert room.capacity >= inst.seats_needed(s)
        if s.required_capability:
            assert s.required_capability in room.capabilities


def test_room_wastage_steers_classes_to_the_best_fitting_room():
    inst = _tiny()
    tm = TimetableModel(
        inst, ObjectiveWeights(room_wastage=10), max_rooms_per_session=None
    )
    out = solve(tm, time_limit=10.0, workers=4)
    assert out.is_solved
    # 30 students: the 32-seat room wastes 2 seats, the others 30 and 60.
    for sid in ("X-A-T1-1", "X-A-T1-2"):
        assert out.timetable.placements[sid].room_id == "R32"
    # What remains is unavoidable: the lab's only legal room is the 40-seat
    # networking lab, 10 empty seats = 1 unit. The theory classes add nothing.
    assert out.soft_breakdown["room_wastage"] == 1


def test_seat_efficiency_is_measured_from_the_assigned_rooms():
    inst = _tiny()
    tt = Timetable(
        placements={
            "X-A-T1-1": Placement("X-A-T1-1", 0, "R90"),
            "X-A-T1-2": Placement("X-A-T1-2", 9, "R32"),
            "X-A-L1-1": Placement("X-A-L1-1", 1, "LAB-N"),
        },
        status="FEASIBLE",
    )
    q = schedule_metrics(inst, tt)
    assert q.wasted_seats == (90 - 30) + (32 - 30) + (40 - 30)
    assert q.oversized_sessions == 1  # only the 90-seat hall
    # (30 + 30 + 30*2) seat-hours used of (90 + 32 + 40*2) offered.
    assert q.seat_efficiency_pct == pytest.approx(120 / 202 * 100)


def test_a_lab_needing_equipment_only_goes_to_an_equipped_lab():
    inst = _tiny()
    out = generate(inst, time_limit=10.0, workers=4)
    assert out.is_solved
    assert out.timetable.placements["X-A-L1-1"].room_id == "LAB-N"


def test_missing_equipment_is_diagnosed_not_ignored():
    rooms = [r for r in _tiny().rooms if r.id != "LAB-N"]
    inst = _tiny(rooms=rooms)
    with pytest.raises(ValueError, match="no legal placement"):
        TimetableModel(inst)
    report = diagnose(inst)
    assert report.proven_infeasible
    assert any("equipped for 'networking'" in f.message for f in report.findings)


def test_a_room_out_of_service_is_never_offered():
    inst = _tiny()
    first = generate(inst, time_limit=10.0, workers=4).timetable
    closed = inst.derive(
        rooms=[replace(r, active=False) if r.id == "R32" else r for r in inst.rooms]
    )
    # The old timetable is now illegal, and the validator says so ...
    assert validate(closed, first).counts["inactive_room_violations"] == 2
    # ... and a repair moves exactly those classes out of the closed room.
    fixed = repair(closed, first, None, phase1_limit=10, phase2_limit=2, workers=4)
    assert fixed.solved
    assert validate(closed, fixed.timetable).is_clean
    assert all(p.room_id != "R32" for p in fixed.timetable.placements.values())
    assert fixed.diff.room_only_moves == 2 and fixed.diff.time_moves == 0


# --------------------------------------------------------------------------
# Locks
# --------------------------------------------------------------------------


def _theory_session(inst):
    return next(s for s in inst.sessions if not s.elective_group and s.duration == 1)


def test_a_locked_session_is_placed_exactly_where_it_was_locked(
    small_instance, published
):
    s = _theory_session(small_instance)
    ex = explain_session(small_instance, published, s.id)
    target = next(
        o
        for o in ex.options
        if o.feasible and o.timeslot_id != published.placements[s.id].timeslot_id
    )
    locked = small_instance.derive(
        locks={s.id: Lock(s.id, target.timeslot_id, target.room_id)}
    )
    out = generate(locked, time_limit=20.0, workers=4)
    assert out.is_solved
    p = out.timetable.placements[s.id]
    assert (p.timeslot_id, p.room_id) == (target.timeslot_id, target.room_id)
    assert validate(locked, out.timetable).counts["lock_violations"] == 0


def test_a_time_only_lock_leaves_the_room_to_the_solver(small_instance, published):
    s = _theory_session(small_instance)
    start = published.placements[s.id].timeslot_id
    locked = small_instance.derive(locks={s.id: Lock(s.id, start, None)})
    out = generate(locked, time_limit=20.0, workers=4)
    assert out.is_solved
    assert out.timetable.placements[s.id].timeslot_id == start


def _block_session_slot(inst, tt, session):
    p = tt.placements[session.id]
    slot = inst.calendar.by_id[p.timeslot_id]
    return [
        faculty_unavailable(
            inst,
            session.faculty_id,
            slot.day,
            PERIOD_START[slot.period],
            PERIOD_END[slot.period],
        )
    ]


def test_repair_keeps_a_locked_session_where_it_is(small_instance, published):
    victim, keeper = [
        s for s in small_instance.sessions if not s.elective_group and s.duration == 1
    ][:2]
    p = published.placements[keeper.id]
    locked = small_instance.derive(
        locks={keeper.id: Lock(keeper.id, p.timeslot_id, p.room_id)}
    )
    blocks = _block_session_slot(locked, published, victim)
    result = repair(
        apply_disruptions(locked, blocks), published, blocks, phase1_limit=15,
        phase2_limit=3, workers=4,
    )
    assert result.solved
    assert result.timetable.placements[keeper.id] == p
    assert validate(apply_disruptions(locked, blocks), result.timetable).is_clean


def test_a_lock_the_rules_cannot_honour_is_named_as_the_cause(
    small_instance, published
):
    s = _theory_session(small_instance)
    p = published.placements[s.id]
    locked = small_instance.derive(
        locks={s.id: Lock(s.id, p.timeslot_id, p.room_id)}
    )
    blocks = _block_session_slot(locked, published, s)
    disrupted = apply_disruptions(locked, blocks)

    result = repair(disrupted, published, blocks, phase1_limit=10, workers=4)
    assert result.status == "INFEASIBLE"
    assert result.timetable is None
    assert "locked" in (result.reason or "")
    first = result.diagnosis.findings[0]
    assert first.category == "Locked sessions" and first.severity == "blocking"


def test_unlocking_lets_the_solver_move_the_session_again(small_instance, published):
    s = _theory_session(small_instance)
    blocks = _block_session_slot(small_instance, published, s)
    result = repair(
        apply_disruptions(small_instance, blocks), published, blocks,
        phase1_limit=15, phase2_limit=3, workers=4,
    )
    assert result.solved
    assert result.timetable.placements[s.id] != published.placements[s.id]


# --------------------------------------------------------------------------
# The independent validator knows the new rules
# --------------------------------------------------------------------------


def test_validator_catches_a_broken_lock(small_instance, published):
    s = _theory_session(small_instance)
    p = published.placements[s.id]
    elsewhere = (p.timeslot_id + 1) % len(small_instance.calendar)
    locked = small_instance.derive(locks={s.id: Lock(s.id, elsewhere, None)})
    assert validate(locked, published).counts["lock_violations"] == 1


def test_validator_catches_a_room_lacking_equipment(small_instance, published):
    lab = next(s for s in small_instance.sessions if s.is_lab)
    needy = small_instance.derive(
        sessions=[
            replace(s, required_capability="quantum") if s.id == lab.id else s
            for s in small_instance.sessions
        ]
    )
    assert validate(needy, published).counts["capability_violations"] == 1


def test_validator_catches_a_class_in_a_closed_room(small_instance, published):
    s = _theory_session(small_instance)
    room = published.placements[s.id].room_id
    closed = small_instance.derive(
        rooms=[
            replace(r, active=False) if r.id == room else r
            for r in small_instance.rooms
        ]
    )
    assert validate(closed, published).counts["inactive_room_violations"] >= 1


# --------------------------------------------------------------------------
# Soft preferences
# --------------------------------------------------------------------------


def test_teacher_preferences_are_honoured_when_they_can_be():
    cal = Calendar()
    mondays = frozenset(sl.id for sl in cal.slots_on(0))
    inst = _tiny(
        faculty=[
            Faculty("F1", "Prof. One", preferred_off=mondays),
            Faculty("F2", "Prof. Two", preferred_off=mondays),
        ]
    )
    out = solve(
        TimetableModel(inst, ObjectiveWeights(faculty_preferences=20)),
        time_limit=10.0,
        workers=4,
    )
    assert out.is_solved
    assert schedule_metrics(inst, out.timetable).preference_hits == 0


# --------------------------------------------------------------------------
# Persistence of the new fields
# --------------------------------------------------------------------------


def test_new_fields_survive_a_snapshot_round_trip(institution):
    inst, tt = institution
    first = inst.sessions[0]
    locked = inst.derive(
        locks={first.id: Lock(first.id, tt.placements[first.id].timeslot_id, None, "exam")}
    )
    back = instance_from_dict(instance_to_dict(locked))
    assert back.locks == locked.locks
    assert [b.semester for b in back.batches] == [b.semester for b in inst.batches]
    assert back.room_by_id["CL5"].capabilities == inst.room_by_id["CL5"].capabilities
    assert [s.required_capability for s in back.sessions] == [
        s.required_capability for s in inst.sessions
    ]


def test_snapshots_written_before_these_fields_still_load():
    inst, tt, meta = load_snapshot("data/fixtures/rehearsed_v1.json")
    assert meta["label"] == "published-v1"
    assert all(r.active for r in inst.rooms)
    assert inst.locks == {}
    assert validate(inst, tt).is_clean
