"""The operational workflow: ranked repairs, coordinator moves, and the rules
that decide which constraints are in force on a given day."""

from __future__ import annotations

from datetime import date

import pytest

from backend.app.domain.models import PERIOD_END, PERIOD_START, RoomType
from backend.app.services.constraints import (
    ACTIVE,
    BASE,
    EXPIRED,
    IN_EFFECT,
    INACTIVE,
    LOCK,
    PENDING,
    REJECTED,
    RULE,
    TEMPORARY,
    UPCOMING,
    ConstraintRecord,
    compile_instance,
    weekdays_in,
)
from backend.app.solver.disruption import (
    BATCH_UNAVAILABLE,
    FACULTY_UNAVAILABLE,
    apply_disruptions,
    faculty_unavailable,
)
from backend.app.solver.moves import check_move, preview_move
from backend.app.solver.options import repair_options
from backend.app.solver.repair import repair
from backend.app.solver.validate import validate


def _theory(inst):
    return [s for s in inst.sessions if not s.elective_group and s.duration == 1]


def _absence_during(inst, tt, session):
    """Block the session's teacher for exactly the period it is taught."""
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


@pytest.fixture(scope="module")
def ranked(small_instance, published):
    victim = _theory(small_instance)[0]
    blocks = _absence_during(small_instance, published, victim)
    disrupted = apply_disruptions(small_instance, blocks)
    found = repair_options(
        disrupted, published, blocks, count=3, phase1_limit=10, phase2_limit=2,
        workers=4,
    )
    return victim, blocks, disrupted, found


# --------------------------------------------------------------------------
# Ranked repair options and Auto-select Best
# --------------------------------------------------------------------------


def test_the_affected_session_is_found(ranked):
    victim, _, _, found = ranked
    assert found.affected == [victim.id]


def test_several_distinct_repairs_are_offered(ranked):
    _, _, _, found = ranked
    assert len(found.options) >= 2


def test_options_are_ranked_by_disruption_then_quality(ranked):
    _, _, _, found = ranked
    keys = [o.sort_key for o in found.options]
    assert keys == sorted(keys)
    assert [o.rank for o in found.options] == list(range(1, len(found.options) + 1))
    assert [o.label for o in found.options][:2] == ["A", "B"]
    assert [o.recommended for o in found.options].count(True) == 1
    assert found.best is found.options[0] and found.best.recommended


def test_auto_select_best_is_the_solvers_own_optimum(ranked, published):
    """Option A is not a heuristic pick: it matches a plain two-phase repair."""
    _, blocks, disrupted, found = ranked
    plain = repair(disrupted, published, blocks, phase1_limit=10, phase2_limit=2, workers=4)
    assert plain.minimal_proven
    assert found.best.result.diff.time_moves == plain.diff.time_moves
    assert found.best.result.diff.room_only_moves == plain.diff.room_only_moves


def test_every_option_is_conflict_free(ranked):
    _, _, disrupted, found = ranked
    for option in found.options:
        assert option.hard_violations == 0
        assert validate(disrupted, option.result.timetable).is_clean


def test_options_really_differ_for_the_affected_session(ranked):
    victim, _, _, found = ranked
    starts = [
        o.result.timetable.placements[victim.id].timeslot_id for o in found.options
    ]
    assert len(set(starts)) == len(starts)


def test_each_option_accounts_for_its_knock_on_moves(ranked):
    victim, _, _, found = ranked
    for o in found.options:
        moved_affected = sum(1 for m in o.affected_moves if m.moved_time or m.from_room != m.to_room)
        assert o.knock_on == o.result.diff.changed - moved_affected
        assert o.affected_moves[0].session_id == victim.id


def test_an_impossible_absence_offers_no_options(small_instance, published):
    teacher = _theory(small_instance)[0].faculty_id
    blocks = [
        faculty_unavailable(small_instance, teacher, day, "09:00", "23:59")
        for day in range(small_instance.calendar.days)
    ]
    found = repair_options(
        apply_disruptions(small_instance, blocks), published, blocks,
        phase1_limit=5, workers=4,
    )
    assert found.options == []
    assert found.failure is not None
    assert found.failure.status == "INFEASIBLE"
    assert found.failure.timetable is None


def test_an_absence_that_touches_no_class_changes_nothing(small_instance, published):
    teacher = _theory(small_instance)[0].faculty_id
    taught = {
        published.placements[s.id].timeslot_id + k
        for s in small_instance.sessions
        if s.faculty_id == teacher
        for k in range(s.duration)
    }
    free = next(
        sl
        for sl in small_instance.calendar.teaching_slots
        if sl.id not in taught
    )
    blocks = [
        faculty_unavailable(
            small_instance, teacher, free.day,
            PERIOD_START[free.period], PERIOD_END[free.period],
        )
    ]
    found = repair_options(
        apply_disruptions(small_instance, blocks), published, blocks, workers=4,
    )
    assert found.affected == []
    assert len(found.options) == 1
    assert found.best.result.diff.changed == 0


# --------------------------------------------------------------------------
# Coordinator moves
# --------------------------------------------------------------------------


def test_a_move_into_the_teachers_unavailable_period_is_refused(
    small_instance, published
):
    s = _theory(small_instance)[0]
    away = sorted(small_instance.faculty_by_id[s.faculty_id].unavailable)[0]
    check, result, _ = preview_move(small_instance, published, s.id, away, workers=4)
    assert not check.allowed
    assert any(b.rule == "faculty_unavailable" for b in check.hard)
    assert result is None


def test_a_move_into_the_wrong_kind_of_room_is_refused(small_instance, published):
    s = _theory(small_instance)[0]
    lab = next(r for r in small_instance.rooms if r.room_type is RoomType.LAB)
    check = check_move(
        small_instance, published, s.id, published.placements[s.id].timeslot_id, lab.id
    )
    assert any(b.rule == "room_type" for b in check.hard)


def test_a_move_onto_an_occupied_slot_is_repaired_around(small_instance, published):
    s = _theory(small_instance)[0]
    target = None
    for slot in small_instance.calendar.teaching_slots:
        check = check_move(small_instance, published, s.id, slot.id)
        if check.allowed and any(b.rule == "batch_busy" for b in check.resolvable):
            target = slot.id
            break
    assert target is not None, "no occupied-but-legal slot to test against"

    check, result, pinned = preview_move(
        small_instance, published, s.id, target, phase1_limit=15, workers=4
    )
    assert result is not None and result.solved
    assert result.timetable.placements[s.id].timeslot_id == target
    # The class that held the slot had to make room.
    assert result.diff.changed >= 2
    assert validate(pinned, result.timetable).is_clean
    assert pinned.locks[s.id].timeslot_id == target


# --------------------------------------------------------------------------
# Base rules and temporary overrides
# --------------------------------------------------------------------------

FRIDAY = date(2026, 9, 11)


def _leave(**over) -> ConstraintRecord:
    data = dict(
        category=TEMPORARY,
        kind=FACULTY_UNAVAILABLE,
        target_id="F01",
        start_time="14:00",
        end_time="17:00",
        start_date=FRIDAY,
        end_date=FRIDAY,
        reason="medical appointment",
    )
    data.update(over)
    return ConstraintRecord(**data)


def test_dates_map_onto_teaching_weekdays():
    assert weekdays_in(FRIDAY, FRIDAY) == [4]
    assert weekdays_in(date(2026, 9, 12), date(2026, 9, 13)) == []  # a weekend
    assert weekdays_in(date(2026, 9, 7), date(2026, 9, 30)) == [0, 1, 2, 3, 4]
    assert weekdays_in(FRIDAY, date(2026, 9, 10)) == []


def test_a_temporary_override_constrains_while_it_is_in_effect(small_instance):
    rec = _leave()
    assert rec.lifecycle(FRIDAY) == IN_EFFECT
    compiled = compile_instance(small_instance, [rec], FRIDAY)
    friday = {
        sl.id
        for sl in small_instance.calendar.slots_on(4)
        if PERIOD_START[sl.period] in ("14:00", "15:00", "16:00")
    }
    assert friday <= compiled.instance.faculty_by_id["F01"].unavailable
    assert compiled.applied == [rec]


def test_tomorrows_leave_already_shapes_todays_repair(small_instance):
    rec = _leave()
    assert rec.lifecycle(date(2026, 9, 10)) == UPCOMING
    assert compile_instance(small_instance, [rec], date(2026, 9, 10)).applied == [rec]


def test_an_expired_override_stops_constraining(small_instance):
    rec = _leave()
    compiled = compile_instance(small_instance, [rec], date(2026, 9, 14))
    assert compiled.applied == []
    assert compiled.skipped == [(rec, EXPIRED)]
    assert (
        compiled.instance.faculty_by_id["F01"].unavailable
        == small_instance.faculty_by_id["F01"].unavailable
    )


def test_a_base_rule_never_expires(small_instance):
    rule = ConstraintRecord(
        category=BASE,
        kind=BATCH_UNAVAILABLE,
        target_id="SE-A",
        start_time="16:00",
        end_time="23:59",
        reason="no lectures after 4 PM",
    )
    for today in (date(2026, 1, 1), date(2030, 12, 31)):
        compiled = compile_instance(small_instance, [rule], today)
        late = {sl.id for sl in small_instance.calendar.slots if sl.period == 7}
        assert late <= compiled.instance.batch_by_id["SE-A"].unavailable


@pytest.mark.parametrize("status", [PENDING, REJECTED, INACTIVE])
def test_records_not_active_do_not_constrain(small_instance, status):
    compiled = compile_instance(small_instance, [_leave(status=status)], FRIDAY)
    assert compiled.applied == []
    assert compiled.skipped[0][1] == status


def test_a_rule_record_tightens_the_workload_rule(small_instance):
    rec = ConstraintRecord(category=BASE, kind=RULE, rule_field="max_consecutive", rule_value=2)
    compiled = compile_instance(small_instance, [rec], FRIDAY)
    assert {f.max_consecutive for f in compiled.instance.faculty} == {2}


def test_a_lock_record_becomes_a_hard_lock(small_instance, published):
    s = _theory(small_instance)[0]
    p = published.placements[s.id]
    rec = ConstraintRecord(
        category=BASE, kind=LOCK, session_id=s.id, lock_timeslot=p.timeslot_id,
        lock_room=p.room_id, reason="external examiner",
    )
    compiled = compile_instance(small_instance, [rec], FRIDAY)
    assert compiled.instance.locks[s.id].room_id == p.room_id


def test_an_invalid_record_is_reported_not_silently_dropped(small_instance):
    ghost = _leave(target_id="GHOST")
    compiled = compile_instance(small_instance, [ghost], FRIDAY)
    assert compiled.applied == []
    assert "GHOST" in compiled.skipped[0][1]


def test_an_override_on_a_weekend_blocks_nothing_and_says_so(small_instance):
    saturday = _leave(start_date=date(2026, 9, 12), end_date=date(2026, 9, 12))
    compiled = compile_instance(small_instance, [saturday], date(2026, 9, 12))
    assert compiled.applied == []
    assert "covers no teaching period" in compiled.skipped[0][1]
