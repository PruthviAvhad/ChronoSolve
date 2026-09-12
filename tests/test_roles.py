"""Role-based access: one application, three roles, enforced by the API.

The UI hides what a role cannot do, but hiding is not security -- every rule
here is checked against the HTTP layer directly.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

REQUEST = {
    "faculty_id": "F02",
    "start_date": "2026-09-14",
    "end_date": "2026-09-14",
    "start_time": "09:00",
    "end_time": "17:00",
}

# (method, path, json) -- actions only a coordinator may take.
COORDINATOR_ONLY = [
    ("post", "/api/what-if", {"scenario": "faculty"}),
    ("post", "/api/apply", None),
    ("post", "/api/discard", None),
    ("post", "/api/generate", {"time_limit": 3}),
    ("post", "/api/reoptimize", None),
    ("get", "/api/scenarios", None),
    ("post", "/api/locks", {"session_id": "SE-A-CS301-1"}),
    ("get", "/api/constraints", None),
    ("post", "/api/constraints", {"category": "BASE", "kind": "RULE", "rule_field": "max_consecutive", "rule_value": 2}),
    ("post", "/api/moves/check", {"session_id": "SE-A-CS301-1", "timeslot_id": 0}),
    ("get", "/api/analytics", None),
    ("get", "/api/admin/dashboard", None),
    ("get", "/api/versions", None),
    ("get", "/api/import/template", None),
    ("post", "/api/admin/restore-rehearsed", None),
    ("patch", "/api/rooms/LH101", {"capacity": 90}),
    ("put", "/api/faculty/F01/availability", {"unavailable": []}),
]


def call(c: TestClient, method: str, path: str, body):
    return getattr(c, method)(path, json=body) if body is not None else getattr(c, method)(path)


def test_the_api_requires_signing_in(anonymous):
    assert anonymous.get("/api/health").status_code == 200
    assert anonymous.get("/api/state").status_code == 401
    assert anonymous.get("/api/grid?view=batch&id=SE-A").status_code == 401


def test_a_wrong_password_is_refused(anonymous):
    r = anonymous.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401


def test_the_signed_in_user_knows_their_role(sign_in):
    assert sign_in("admin", "admin123").get("/api/auth/me").json()["role"] == "ADMIN"
    me = sign_in("mehta", "faculty123").get("/api/auth/me").json()
    assert (me["role"], me["faculty_id"]) == ("FACULTY", "F01")
    assert sign_in("student", "student123").get("/api/auth/me").json()["role"] == "STUDENT"


def test_signing_out_ends_the_session(sign_in):
    c = sign_in("admin", "admin123")
    c.post("/api/auth/logout")
    assert c.get("/api/state").status_code == 401


@pytest.mark.parametrize("method,path,body", COORDINATOR_ONLY)
def test_a_student_cannot_change_or_inspect_scheduling(sign_in, method, path, body):
    assert call(sign_in("student", "student123"), method, path, body).status_code == 403


@pytest.mark.parametrize("method,path,body", COORDINATOR_ONLY)
def test_a_teacher_cannot_publish_or_administer(sign_in, method, path, body):
    assert call(sign_in("mehta", "faculty123"), method, path, body).status_code == 403


def test_a_student_reads_the_published_timetable_only(sign_in):
    student = sign_in("student", "student123")
    assert student.get("/api/grid?view=batch&id=SE-A").status_code == 200
    assert student.get("/api/grid?view=batch&id=SE-A&source=pending").status_code == 403
    assert student.get("/api/export/ics?view=batch&id=SE-A").status_code == 200
    assert student.get("/api/academic/structure").status_code == 200
    assert student.post("/api/requests", json=REQUEST).status_code == 403


def test_a_teacher_reports_only_their_own_unavailability(sign_in):
    teacher = sign_in("mehta", "faculty123")
    r = teacher.post("/api/requests", json=REQUEST)  # F02 is Prof. Iyer
    assert r.status_code == 403


def test_a_teacher_cannot_approve_a_request(sign_in):
    teacher = sign_in("mehta", "faculty123")
    body = {**REQUEST, "faculty_id": None}
    created = teacher.post("/api/requests", json=body)
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    assert teacher.post(f"/api/requests/{rid}/approve", json={}).status_code == 403
    assert teacher.post(f"/api/requests/{rid}/reject", json={}).status_code == 403


def test_teachers_see_only_their_own_requests(sign_in):
    mehta = sign_in("mehta", "faculty123")
    rid = mehta.post("/api/requests", json={**REQUEST, "faculty_id": None}).json()["id"]
    iyer = sign_in("iyer", "faculty123")
    assert iyer.get("/api/requests").json() == []
    assert iyer.get(f"/api/requests/{rid}").status_code == 403
    admin = sign_in("admin", "admin123")
    assert [r["id"] for r in admin.get("/api/requests").json()] == [rid]


def test_demo_sign_in_follows_its_setting(anonymous, monkeypatch):
    r = anonymous.post("/api/auth/demo", json={"role": "FACULTY"})
    assert r.status_code == 200
    assert r.json()["username"] == "mehta"
    monkeypatch.setenv("CHRONOSOLVE_DEMO_LOGIN", "0")
    assert anonymous.post("/api/auth/demo", json={"role": "ADMIN"}).status_code == 404
    assert anonymous.get("/api/auth/demo-accounts").json() == []


def test_demo_sign_in_cannot_borrow_another_role(anonymous):
    r = anonymous.post("/api/auth/demo", json={"role": "ADMIN", "username": "mehta"})
    assert r.status_code == 404


def test_the_request_date_is_checked_against_today(sign_in):
    teacher = sign_in("mehta", "faculty123")
    past = {**REQUEST, "faculty_id": None, "start_date": "2026-09-01", "end_date": "2026-09-01"}
    r = teacher.post("/api/requests", json=past)
    assert r.status_code == 400
    assert date.fromisoformat("2026-09-01") < date(2026, 9, 10)
