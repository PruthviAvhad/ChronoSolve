"""The operational workflow over HTTP, end to end.

A teacher reports an absence and sees ranked options computed by the solver; a
coordinator reviews and approves; a new version is published and every role
sees it. Plus the coordinator's own controls: locks, moves, rules, restoring
after an override ends, versions and analytics.

"Today" is pinned to Thursday 10 September 2026 by conftest.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.app.domain.models import PERIOD_END, PERIOD_START

MONDAY = date(2026, 9, 14)


def _theory(inst, faculty_id: str | None = None):
    return [
        s
        for s in inst.sessions
        if not s.elective_group
        and s.duration == 1
        and (faculty_id is None or s.faculty_id == faculty_id)
    ]


def _absence_for(inst, tt, session) -> dict:
    """Next week's occurrence of exactly the period `session` is taught."""
    slot = inst.calendar.by_id[tt.placements[session.id].timeslot_id]
    day = MONDAY + timedelta(days=slot.day)
    return {
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "start_time": PERIOD_START[slot.period],
        "end_time": PERIOD_END[slot.period],
        "reason": "medical appointment",
    }


@pytest.fixture
def filed(sign_in, small_instance, published):
    """Prof. Mehta reports being away for one of their classes next week."""
    session = _theory(small_instance, "F01")[0]
    teacher = sign_in("mehta", "faculty123")
    r = teacher.post("/api/requests", json=_absence_for(small_instance, published, session))
    assert r.status_code == 200, r.text
    return teacher, r.json(), session


# --------------------------------------------------------------------------
# The teacher's side
# --------------------------------------------------------------------------


def test_reporting_an_absence_finds_the_affected_class(filed):
    _, req, session = filed
    assert req["status"] == "DRAFT"
    assert [a["session_id"] for a in req["affected"]] == [session.id]
    assert req["faculty_name"] == "Prof. Mehta"


def test_the_options_are_ranked_valid_and_measured(filed):
    _, req, session = filed
    options = req["options"]
    assert len(options) >= 2
    assert options[0]["recommended"] and options[0]["rank"] == 1
    assert [o["recommended"] for o in options].count(True) == 1
    keys = [(o["time_moves"], o["room_only_moves"], o["soft_cost"]) for o in options]
    assert keys == sorted(keys)
    for o in options:
        assert o["hard_violations"] == 0
        assert o["unchanged"] + o["changed"] == o["total"]
        assert o["retention_pct"] == round(o["unchanged"] / o["total"] * 100, 1)
        assert o["moves"][0]["session_id"] == session.id
        assert all(c["why"] for c in o["changes"])
    targets = {o["moves"][0]["to_label"] for o in options}
    assert len(targets) == len(options), "each option moves the class somewhere new"


def test_a_request_publishes_nothing(filed, client):
    before = client.get("/api/state").json()
    assert before["version"]["number"] == 1
    assert before["has_pending"] is False


def test_auto_select_best_submits_the_top_option(filed):
    teacher, req, _ = filed
    body = teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True}).json()
    assert (body["status"], body["selected_rank"], body["auto_selected"]) == ("SUBMITTED", 1, True)


def test_a_teacher_may_prefer_a_lower_ranked_option(filed):
    teacher, req, _ = filed
    body = teacher.post(f"/api/requests/{req['id']}/submit", json={"rank": 2}).json()
    assert (body["selected_rank"], body["auto_selected"]) == (2, False)


def test_a_teacher_can_withdraw_before_a_decision(filed):
    teacher, req, _ = filed
    assert teacher.post(f"/api/requests/{req['id']}/withdraw").json()["status"] == "WITHDRAWN"
    assert teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True}).status_code == 409


def test_an_absence_during_a_free_period_changes_nothing(sign_in, small_instance, published):
    taught = {
        published.placements[s.id].timeslot_id + k
        for s in small_instance.sessions
        if s.faculty_id == "F01"
        for k in range(s.duration)
    }
    free = next(
        sl
        for sl in small_instance.calendar.teaching_slots
        if sl.id not in taught and sl.id not in small_instance.faculty_by_id["F01"].unavailable
    )
    day = MONDAY + timedelta(days=free.day)
    teacher = sign_in("mehta", "faculty123")
    req = teacher.post(
        "/api/requests",
        json={
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "start_time": PERIOD_START[free.period],
            "end_time": PERIOD_END[free.period],
        },
    ).json()
    assert req["affected"] == []
    assert len(req["options"]) == 1 and req["options"][0]["changed"] == 0


def test_an_absence_on_a_weekend_is_refused(sign_in):
    teacher = sign_in("mehta", "faculty123")
    r = teacher.post(
        "/api/requests",
        json={"start_date": "2026-09-12", "end_date": "2026-09-13"},
    )
    assert r.status_code == 400
    assert "no teaching period" in r.json()["detail"]


# --------------------------------------------------------------------------
# The coordinator's side
# --------------------------------------------------------------------------


def test_the_coordinator_sees_and_reviews_the_submitted_request(filed, client):
    teacher, req, session = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})

    pending = client.get("/api/requests?status=SUBMITTED").json()
    assert [r["id"] for r in pending] == [req["id"]]
    assert client.get("/api/admin/dashboard").json()["pending_count"] == 1

    review = client.get(f"/api/requests/{req['id']}/review").json()
    assert review["feasible"] is True
    assert review["validation"]["total_violations"] == 0
    assert review["unchanged"] + review["changed"] == review["total"]
    moved = next(c for c in review["changes"] if c["session_id"] == session.id)
    assert moved["why"] and moved["why"][0]["rule"] == "faculty_unavailable"


def test_approval_publishes_a_new_version_everyone_sees(filed, client, sign_in, small_instance):
    teacher, req, session = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    chosen = req["options"][0]["moves"][0]

    approved = client.post(f"/api/requests/{req['id']}/approve", json={"note": "fine"})
    assert approved.status_code == 200, approved.text
    version = approved.json()["version"]
    assert version["number"] == 2
    assert version["label"] == f"published-request-{req['id']}"
    assert (version["request_id"], version["effective_until"]) == (req["id"], req["end_date"])
    assert approved.json()["request"]["status"] == "APPROVED"

    state = client.get("/api/state").json()
    assert state["version"]["id"] == version["id"]
    assert state["validation"]["clean"] is True

    # The teacher's absence is now a temporary override in force.
    rule = next(r for r in client.get("/api/constraints").json() if r["request_id"] == req["id"])
    assert (rule["category"], rule["status"]) == ("TEMPORARY", "ACTIVE")

    # Batch, teacher and student views all show the class in its new place.
    def where(c, view, entity):
        cells = c.get(f"/api/grid?view={view}&id={entity}").json()["cells"]
        cell = next(x for x in cells if x["session_id"] == session.id)
        return (cell["day"], cell["period"], cell["room_id"])

    new_place = where(client, "batch", session.batch_id)
    assert where(teacher, "faculty", "F01") == new_place
    assert where(sign_in("student", "student123"), "batch", session.batch_id) == new_place
    assert chosen["to_room"] == new_place[2]


def test_approval_is_refused_when_the_timetable_moved_on(filed, client):
    teacher, req, _ = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    client.post("/api/generate", json={"time_limit": 5, "publish": True})

    stale = client.post(f"/api/requests/{req['id']}/approve", json={})
    assert stale.status_code == 409
    assert "changed" in stale.json()["detail"]
    assert client.get(f"/api/requests/{req['id']}").json()["status"] == "STALE"

    fresh = teacher.post(f"/api/requests/{req['id']}/recompute").json()
    assert fresh["status"] == "DRAFT" and not fresh["stale"]
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    assert client.post(f"/api/requests/{req['id']}/approve", json={}).status_code == 200


def test_rejection_leaves_the_published_timetable_alone(filed, client):
    teacher, req, _ = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    before = client.get("/api/state").json()["version"]
    body = client.post(f"/api/requests/{req['id']}/reject", json={"note": "cover arranged"}).json()
    assert body["status"] == "REJECTED"
    assert client.get("/api/state").json()["version"] == before
    rule = next(r for r in client.get("/api/constraints").json() if r["request_id"] == req["id"])
    assert rule["status"] == "REJECTED"


def test_an_ended_override_can_be_previewed_for_restoration(filed, client, monkeypatch):
    teacher, req, _ = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    client.post(f"/api/requests/{req['id']}/approve", json={})
    rule = next(r for r in client.get("/api/constraints").json() if r["request_id"] == req["id"])

    early = client.post(f"/api/constraints/{rule['id']}/restore-preview")
    assert early.status_code == 409  # still in force

    monkeypatch.setenv("CHRONOSOLVE_TODAY", (MONDAY + timedelta(days=10)).isoformat())
    rule_now = next(r for r in client.get("/api/constraints").json() if r["id"] == rule["id"])
    assert rule_now["lifecycle"] == "EXPIRED"
    published = client.get("/api/state").json()["version"]

    preview = client.post(f"/api/constraints/{rule['id']}/restore-preview").json()
    assert preview["feasible"] is True
    assert preview["proposal"]["label"] == "proposed-restoration"
    assert preview["validation"]["total_violations"] == 0
    # Nothing was reverted on its own.
    assert client.get("/api/state").json()["version"] == published


# --------------------------------------------------------------------------
# Coordinator controls
# --------------------------------------------------------------------------


def test_a_locked_session_survives_regeneration(client, small_instance, published):
    s = _theory(small_instance)[0]
    before = published.placements[s.id]
    assert client.post("/api/locks", json={"session_id": s.id, "reason": "exam"}).status_code == 200
    assert [lk["session_id"] for lk in client.get("/api/locks").json()] == [s.id]

    client.post("/api/generate", json={"time_limit": 5, "publish": True})
    cells = client.get(f"/api/grid?view=batch&id={s.batch_id}").json()["cells"]
    cell = next(c for c in cells if c["session_id"] == s.id)
    slot = small_instance.calendar.by_id[before.timeslot_id]
    assert (cell["day"], cell["period"], cell["room_id"], cell["locked"]) == (
        slot.day,
        slot.period,
        before.room_id,
        True,
    )

    assert client.delete(f"/api/locks/{s.id}").status_code == 200
    assert client.get("/api/locks").json() == []


def test_a_coordinator_move_is_checked_then_repaired_around(client, small_instance):
    s = _theory(small_instance)[0]
    target = None
    for slot in small_instance.calendar.teaching_slots:
        check = client.post("/api/moves/check", json={"session_id": s.id, "timeslot_id": slot.id}).json()
        if check["allowed"] and any(b["rule"] == "batch_busy" for b in check["resolvable"]):
            target = slot
            break
    assert target is not None

    preview = client.post("/api/moves/preview", json={"session_id": s.id, "timeslot_id": target.id}).json()
    assert preview["feasible"] is True
    assert preview["changed"] >= 2  # the class that held the slot had to move too
    assert preview["validation"]["total_violations"] == 0

    state = client.post("/api/apply").json()
    assert state["version"]["number"] == 2
    assert state["locked_sessions"] == 1
    cells = client.get(f"/api/grid?view=batch&id={s.batch_id}").json()["cells"]
    cell = next(c for c in cells if c["session_id"] == s.id)
    assert (cell["day"], cell["period"], cell["locked"]) == (target.day, target.period, True)


def test_a_move_the_rules_forbid_is_refused_with_the_rule(client, small_instance):
    s = _theory(small_instance)[0]
    away = sorted(small_instance.faculty_by_id[s.faculty_id].unavailable)[0]
    check = client.post("/api/moves/check", json={"session_id": s.id, "timeslot_id": away}).json()
    assert check["allowed"] is False
    assert any(b["rule"] == "faculty_unavailable" for b in check["hard"])
    preview = client.post("/api/moves/preview", json={"session_id": s.id, "timeslot_id": away}).json()
    assert (preview["feasible"], preview["status"]) == (False, "REFUSED")
    assert client.get("/api/state").json()["has_pending"] is False


def test_a_new_permanent_rule_shows_up_and_is_repaired(client, small_instance, published):
    """An institutional policy added after publishing: the division must keep
    one period free. The published timetable breaks it until it is repaired."""
    s = _theory(small_instance)[0]
    slot = small_instance.calendar.by_id[published.placements[s.id].timeslot_id]
    r = client.post(
        "/api/constraints",
        json={
            "category": "BASE",
            "kind": "BATCH_UNAVAILABLE",
            "target_id": s.batch_id,
            "days": [slot.day],
            "start_time": PERIOD_START[slot.period],
            "end_time": PERIOD_END[slot.period],
            "reason": "weekly department meeting",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "PERMANENT"
    # The published timetable now breaks the rule, and the state says so.
    state = client.get("/api/state").json()
    assert state["validation"]["counts"]["batch_availability"] > 0

    repair = client.post("/api/reoptimize").json()
    assert repair["feasible"] is True
    assert repair["validation"]["total_violations"] == 0
    assert s.id in {c["session_id"] for c in repair["changes"]}
    after = client.post("/api/apply").json()
    assert after["validation"]["clean"] is True


def test_an_impossible_rule_is_reported_not_repaired(client):
    """Every lab is a two-hour block, so no teacher can be held to one period
    at a time: the repair must say so rather than invent a timetable."""
    client.post(
        "/api/constraints",
        json={"category": "BASE", "kind": "RULE", "rule_field": "max_consecutive", "rule_value": 1},
    )
    before = client.get("/api/state").json()["version"]
    repair = client.post("/api/reoptimize").json()
    assert (repair["feasible"], repair["status"]) == (False, "INFEASIBLE")
    assert repair["changes"] == []
    after = client.get("/api/state").json()
    assert after["version"] == before and after["has_pending"] is False


def test_an_override_that_blocks_nothing_is_refused(client):
    r = client.post(
        "/api/constraints",
        json={
            "category": "TEMPORARY",
            "kind": "ROOM_UNAVAILABLE",
            "target_id": "CL3",
            "start_date": "2026-09-12",
            "end_date": "2026-09-13",
        },
    )
    assert r.status_code == 400


def test_versions_record_what_changed(filed, client):
    teacher, req, _ = filed
    teacher.post(f"/api/requests/{req['id']}/submit", json={"auto": True})
    client.post(f"/api/requests/{req['id']}/approve", json={})
    versions = client.get("/api/versions").json()
    latest, first = versions[0], versions[-1]
    assert (latest["status"], latest["is_current"], first["status"]) == ("PUBLISHED", True, "SUPERSEDED")
    assert latest["changed_count"] == req["options"][0]["changed"]
    assert latest["retention_pct"] == pytest.approx(req["options"][0]["retention_pct"], abs=0.05)
    grid = client.get(f"/api/grid?view=batch&id=SE-A&version_id={first['id']}").json()
    assert grid["version_number"] == 1


def test_analytics_are_recomputed_from_the_timetable(client):
    analytics = client.get("/api/analytics").json()
    state = client.get("/api/state").json()
    assert analytics["validation"]["total"] == state["validation"]["total_violations"]
    assert analytics["totals"]["sessions"] == state["summary"]["sessions"]
    assert analytics["totals"]["scheduled"] == state["summary"]["sessions"]
    assert analytics["quality"]["student_idle_hours"] == state["quality"]["student_idle_hours"]
    assert sum(f["weekly_hours"] for f in analytics["faculty"]) == state["summary"]["contact_hours"]


def test_configuration_edits_persist(client):
    room = client.patch("/api/rooms/LH101", json={"capacity": 120, "capabilities": ["projector", "smart-board"]}).json()
    assert (room["capacity"], room["capabilities"]) == (120, ["projector", "smart-board"])
    assert client.get("/api/rooms").json()[0]["capacity"] == 120
    fac = client.put("/api/faculty/F01/availability", json={"unavailable": [0, 1], "preferred_off": [7]}).json()
    assert (fac["unavailable"], fac["preferred_off"]) == ([0, 1], [7])
    structure = client.get("/api/academic/structure").json()
    assert structure["departments"][0]["name"] == "Computer Engineering"
