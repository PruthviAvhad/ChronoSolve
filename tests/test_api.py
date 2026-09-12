"""HTTP surface of the scheduling routes.

Every test runs against a fresh database in pytest's tmp_path, seeded with the
small department and signed in as the coordinator (see conftest). `/api/apply`
and `/api/generate` publish new versions; neither the rehearsed fixture nor any
real database is within reach.
"""

from __future__ import annotations

import json

import pytest

from backend.app.services import scheduling as scheduling_module


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "CP-SAT" in body["solver"]


def test_state_reports_a_clean_published_timetable(client, small_instance):
    body = client.get("/api/state").json()
    assert body["summary"]["sessions"] == len(small_instance.sessions)
    assert body["validation"]["clean"] is True
    assert body["validation"]["total_violations"] == 0
    assert body["published_label"] == "test-baseline"
    assert body["has_pending"] is False
    assert body["published"]["status"] in ("OPTIMAL", "FEASIBLE")


def test_entities_cover_every_selector(client, small_instance):
    body = client.get("/api/entities").json()
    assert len(body["batches"]) == len(small_instance.batches)
    assert len(body["faculty"]) == len(small_instance.faculty)
    assert len(body["rooms"]) == len(small_instance.rooms)


def test_grid_returns_one_cell_per_session_of_a_batch(client, small_instance):
    batch = small_instance.batches[0]
    body = client.get(f"/api/grid?view=batch&id={batch.id}").json()
    expected = sum(1 for s in small_instance.sessions if s.batch_id == batch.id)
    assert len(body["cells"]) == expected
    assert body["calendar"]["lunch_period"] == 4
    assert all(c["changed"] is False for c in body["cells"])


def test_grid_rejects_an_unknown_entity(client):
    assert client.get("/api/grid?view=batch&id=NOPE").status_code == 404


def test_pending_grid_is_absent_until_a_what_if_runs(client):
    batch = client.get("/api/entities").json()["batches"][0]["id"]
    assert (
        client.get(f"/api/grid?view=batch&id={batch}&source=pending").status_code == 404
    )


def test_what_if_proposes_without_publishing(client):
    before = client.get("/api/state").json()

    body = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    assert body["feasible"] is True
    assert body["total"] == before["summary"]["sessions"]
    assert body["unchanged"] + body["changed"] == body["total"]
    assert body["validation"]["total_violations"] == 0

    after = client.get("/api/state").json()
    assert after["published"] == before["published"], "what-if must not publish"
    assert after["has_pending"] is True


def test_changes_carry_the_rule_that_forced_them(client, small_instance, published):
    """Build the disruption from a session that is actually placed, so this
    always forces a change -- a canned scenario can legitimately need none, and
    the test would then skip and cover nothing."""
    victim = next(s for s in small_instance.sessions if s.duration == 1)
    slot = small_instance.calendar.by_id[published.placements[victim.id].timeslot_id]
    faculty = small_instance.faculty_by_id[victim.faculty_id]

    body = client.post(
        "/api/what-if",
        json={
            "disruptions": [
                {
                    "kind": "FACULTY_UNAVAILABLE",
                    "target": faculty.id,
                    "day": slot.day,
                    "start_time": "09:00",
                    "end_time": "23:59",
                }
            ]
        },
    ).json()

    assert body["feasible"] is True
    assert body["changed"] > 0, "blocking a teaching day must move something"
    for change in body["changes"]:
        assert change["why"], f"{change['session_id']} moved with no reason"
        assert all(w["rule"] and w["message"] for w in change["why"])


def test_discard_drops_the_proposal(client):
    client.post("/api/what-if", json={"scenario": "faculty"})
    assert client.get("/api/state").json()["has_pending"] is True

    body = client.post("/api/discard").json()
    assert body["has_pending"] is False
    batch = client.get("/api/entities").json()["batches"][0]["id"]
    assert (
        client.get(f"/api/grid?view=batch&id={batch}&source=pending").status_code == 404
    )


def test_apply_publishes_the_proposal(client):
    proposal = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    assert proposal["feasible"]

    body = client.post("/api/apply").json()
    assert body["has_pending"] is False
    assert body["published_label"] == "published-repaired"
    assert body["validation"]["total_violations"] == 0


def test_apply_without_a_proposal_is_rejected(client):
    assert client.post("/api/apply").status_code == 400


def test_an_impossible_scenario_returns_a_diagnosis(client):
    body = client.post("/api/what-if", json={"scenario": "leave"}).json()
    assert body["feasible"] is False
    assert body["status"] == "INFEASIBLE"
    assert body["diagnosis"]["proven_infeasible"] is True
    assert body["diagnosis"]["findings"]
    assert all(f["message"] for f in body["diagnosis"]["findings"])
    # A failed what-if must leave nothing pending.
    assert client.get("/api/state").json()["has_pending"] is False


def test_explain_covers_every_timeslot(client, small_instance):
    session = small_instance.sessions[0]
    body = client.get(f"/api/explain?session_id={session.id}").json()
    assert len(body["options"]) == len(small_instance.calendar)
    assert body["feasible_count"] >= 1  # its own slot at minimum
    assert body["current_label"]


def test_explain_rejects_an_unknown_session(client):
    assert client.get("/api/explain?session_id=nope").status_code == 404


def test_parse_proposes_a_rule_without_solving(client):
    before = client.get("/api/state").json()
    body = client.post(
        "/api/parse", json={"text": "Prof. Mehta is unavailable after 2 PM on Friday"}
    ).json()

    assert body["understood"] is True
    assert body["kind"] == "FACULTY_UNAVAILABLE"
    assert body["days"] == [4]
    assert body["start_time"] == "14:00"
    assert len(body["disruptions"]) == 1
    # Parsing alone changes nothing.
    assert client.get("/api/state").json() == before


def test_parse_reports_what_it_could_not_read(client):
    body = client.post("/api/parse", json={"text": "something vague"}).json()
    assert body["understood"] is False
    assert body["issues"]
    assert body["disruptions"] == []


def test_a_parsed_rule_can_be_solved(client):
    parsed = client.post(
        "/api/parse", json={"text": "Prof. Mehta is unavailable after 2 PM on Friday"}
    ).json()
    body = client.post(
        "/api/what-if", json={"disruptions": parsed["disruptions"]}
    ).json()
    assert body["feasible"] is True
    assert body["validation"]["total_violations"] == 0


@pytest.mark.parametrize(
    "path,media,magic",
    [
        ("/api/export/xlsx", "spreadsheetml", b"PK"),
        ("/api/export/pdf", "application/pdf", b"%PDF"),
        ("/api/export/ics", "text/calendar", b"BEGIN:VCALENDAR"),
    ],
)
def test_exports_download_with_the_right_type(client, path, media, magic):
    r = client.get(path)
    assert r.status_code == 200
    assert media in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(magic)


def test_exporting_a_proposal_requires_one(client):
    assert client.get("/api/export/pdf?source=pending").status_code == 404


def test_a_rule_change_can_be_simulated(client):
    """A policy change is a what-if too: nobody becomes unavailable, but the
    published schedule stops being legal and has to be repaired."""
    before = client.get("/api/state").json()

    body = client.post(
        "/api/what-if",
        json={"rule_changes": [{"rule": "max_consecutive", "value": 1}]},
    ).json()

    assert body["directly_affected"], "back-to-back teaching should now be illegal"
    assert any("maximum consecutive" in d for d in body["disruptions"])
    # Simulating a rule change must not publish anything.
    assert client.get("/api/state").json()["published"] == before["published"]


def test_a_nonsense_rule_change_is_refused(client):
    r = client.post(
        "/api/what-if",
        json={"rule_changes": [{"rule": "max_consecutive", "value": 0}]},
    )
    assert r.status_code == 422  # pydantic bounds reject it before the solver


def test_a_window_that_blocks_nothing_is_refused(client):
    """A typo like 20:00 for 2 PM must not become a rule that silently no-ops."""
    r = client.post(
        "/api/what-if",
        json={
            "disruptions": [
                {
                    "kind": "FACULTY_UNAVAILABLE",
                    "target": "F01",
                    "day": 2,
                    "start_time": "20:00",
                    "end_time": "22:00",
                }
            ]
        },
    )
    assert r.status_code == 400
    assert "no teaching period" in r.json()["detail"]


def test_scenarios_say_which_kind_they_are(client):
    kinds = {s["key"]: s["kind"] for s in client.get("/api/scenarios").json()}
    assert kinds["faculty"] == "availability"
    assert kinds["tighter-hours"] == "rule"


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_the_template_downloads_as_a_workbook(client):
    r = client.get("/api/import/template")
    assert r.status_code == 200
    assert XLSX in r.headers["content-type"]
    assert "chronosolve-template.xlsx" in r.headers["content-disposition"]
    assert r.content.startswith(b"PK")


def test_the_template_can_be_imported_straight_back(client, small_instance):
    book = client.get("/api/import/template").content

    r = client.post(
        "/api/import?time_limit=20",
        files={"file": ("department.xlsx", book, XLSX)},
    )
    body = r.json()

    assert r.status_code == 200
    assert body["accepted"] is True
    assert body["counts"]["sessions"] == len(small_instance.sessions)
    assert body["state"]["validation"]["total_violations"] == 0
    assert body["state"]["published_label"] == "published-imported"


def test_a_file_that_is_not_a_workbook_is_refused(client):
    before = client.get("/api/state").json()

    body = client.post(
        "/api/import", files={"file": ("junk.xlsx", b"not a workbook", XLSX)}
    ).json()

    assert body["accepted"] is False
    assert body["errors"]
    assert body["state"] is None
    # A rejected import must leave the published timetable exactly as it was.
    assert client.get("/api/state").json() == before


def test_a_workbook_with_bad_references_is_refused_with_row_numbers(client):
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for sheet, header, rows in (
        ("Rooms", ["id", "name", "capacity", "type"], [["LH1", "Hall", 80, "LECTURE"]]),
        ("Faculty", ["id", "name"], [["F1", "Prof. A"]]),
        ("Batches", ["id", "name", "strength"], [["B1", "Div", 60]]),
        (
            "Subjects",
            ["code", "name", "batch", "faculty", "sessions_per_week"],
            [["CS1", "Maths", "GHOST", "F1", 2]],
        ),
    ):
        ws = wb.create_sheet(sheet)
        ws.append(header)
        for row in rows:
            ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)

    body = client.post(
        "/api/import", files={"file": ("bad.xlsx", buffer.getvalue(), XLSX)}
    ).json()

    assert body["accepted"] is False
    assert body["errors"][0]["sheet"] == "Subjects"
    assert body["errors"][0]["row"] == 2
    assert "unknown batch" in body["errors"][0]["message"]


def test_reset_restores_the_snapshot_on_disk(client):
    client.post("/api/what-if", json={"scenario": "faculty"})
    client.post("/api/apply")
    body = client.post("/api/reset").json()
    assert body["has_pending"] is False
    assert body["validation"]["total_violations"] == 0


# ----------------------------------------------------------------------
# Solve progress
# ----------------------------------------------------------------------


def read_sse(response) -> list[dict]:
    """Collect the JSON frames of an SSE response, ignoring keep-alives.

    A streaming response has no `.text` until it is consumed, so this reads it
    line by line the way a browser does.
    """
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.iter_lines()
        if line.startswith("data: ")
    ]


def test_a_solve_reports_the_stages_it_actually_ran(client):
    body = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    keys = [s["key"] for s in body["stages"]]
    assert keys == [
        "validating",
        "building",
        "minimising-disruption",
        "improving-quality",
        "measuring-retention",
        "verifying",
    ]


def test_every_stage_carries_a_measured_duration_and_a_fact(client):
    body = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    for stage in body["stages"]:
        assert stage["seconds"] >= 0
        assert stage["label"], f"{stage['key']} has no label"
        assert stage["detail"], f"{stage['key']} reports no finding"

    # The measured stage durations must account for the reported solve time
    # rather than being independent decoration.
    solving = sum(
        s["seconds"]
        for s in body["stages"]
        if s["key"] in ("minimising-disruption", "improving-quality")
    )
    assert solving >= body["total_seconds"] - 0.5


def test_stage_details_quote_the_numbers_they_measured(client):
    body = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    stages = {s["key"]: s["detail"] for s in body["stages"]}

    # Retention in the stage note must be the same figure the panel shows.
    assert f"{body['unchanged']} of {body['total']} sessions unchanged" in (
        stages["measuring-retention"]
    )
    assert f"{body['retention_pct']:.1f}% retention" in stages["measuring-retention"]
    # And the phase-1 note must not claim minimality the solver did not prove.
    proven = "fewest possible moves proven" in stages["minimising-disruption"]
    assert proven == body["minimal_proven"]


def test_no_stage_invents_a_completion_percentage(client):
    """CP-SAT cannot say how much search remains, so nothing may claim to.

    Retention is a measured property of the schedule and is allowed; a
    percentage of the *solve* would be fabricated.
    """
    body = client.post("/api/what-if", json={"scenario": "faculty"}).json()
    for stage in body["stages"]:
        if "%" in stage["detail"]:
            assert "retention" in stage["detail"], (
                f"stage {stage['key']} reports a percentage that is not a "
                f"measured schedule property: {stage['detail']}"
            )


def test_the_stream_reports_each_stage_as_it_starts_and_finishes(client):
    with client.stream(
        "POST", "/api/what-if/stream", json={"scenario": "faculty"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        frames = read_sse(response)

    started = [f["key"] for f in frames if f["event"] == "stage_started"]
    finished = [f["key"] for f in frames if f["event"] == "stage_finished"]
    assert started == finished, "every stage that starts must also report finishing"
    assert frames[-1]["event"] == "result"


def test_the_stream_ends_with_the_payload_the_plain_post_returns(client):
    with client.stream(
        "POST", "/api/what-if/stream", json={"scenario": "faculty"}
    ) as response:
        streamed = read_sse(response)[-1]["data"]

    plain = client.post("/api/what-if", json={"scenario": "faculty"}).json()

    assert set(streamed) == set(plain)
    # Solve times differ run to run; the schedule facts must not.
    for field in ("feasible", "retention_pct", "unchanged", "total", "changed"):
        assert streamed[field] == plain[field], field


def test_a_streamed_what_if_still_does_not_publish(client):
    before = client.get("/api/state").json()
    with client.stream(
        "POST", "/api/what-if/stream", json={"scenario": "faculty"}
    ) as response:
        read_sse(response)
    after = client.get("/api/state").json()

    assert after["published"] == before["published"]
    assert after["has_pending"] is True


def test_a_streamed_generation_reports_its_own_stages(client):
    with client.stream(
        "POST", "/api/generate/stream", json={"time_limit": 5, "publish": False}
    ) as response:
        frames = read_sse(response)

    keys = [f["key"] for f in frames if f["event"] == "stage_finished"]
    # "searching" ends at CP-SAT's first solution, reported from its callback;
    # "optimising" is everything after that.
    assert keys == ["validating", "building", "searching", "optimising", "verifying"]
    assert frames[-1]["event"] == "result"
    assert frames[-1]["data"]["validation"]["clean"] is True


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"scenario": "no-such-scenario"}, 404),
        ({}, 400),
        (
            {
                "disruptions": [
                    {"kind": "FACULTY_UNAVAILABLE", "target": "GHOST", "day": 1}
                ]
            },
            400,
        ),
    ],
)
def test_a_rejected_stream_request_keeps_its_real_status(client, body, expected):
    """Once SSE headers are sent the status is fixed, so validation runs first.

    Otherwise a 404 would reach the client as a 200 carrying an error frame.
    """
    with client.stream("POST", "/api/what-if/stream", json=body) as response:
        assert response.status_code == expected


def test_a_streamed_failure_arrives_as_an_error_frame(client, monkeypatch):
    """A solve that fails must say so on the stream, not hang or truncate."""

    def explode(*args, **kwargs):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(scheduling_module, "generate", explode)
    with client.stream(
        "POST", "/api/generate/stream", json={"time_limit": 5, "publish": False}
    ) as response:
        frames = read_sse(response)

    assert frames[-1]["event"] == "error"
    assert "solver exploded" in frames[-1]["detail"]


def test_an_unpublished_generation_waits_as_a_proposal(client):
    """Generating for review must not change what students see."""
    before = client.get("/api/state").json()
    response = client.post("/api/generate", json={"time_limit": 5, "publish": False})
    assert response.status_code == 200
    state = response.json()

    assert state["published"] == before["published"]
    assert state["version"]["number"] == before["version"]["number"]
    assert state["has_pending"] is True
    assert state["proposal"]["label"] == "proposed-generated"

    published = client.post("/api/apply").json()
    assert published["version"]["number"] > before["version"]["number"]
    assert published["version"]["label"] == "published-generated"
    assert published["has_pending"] is False
