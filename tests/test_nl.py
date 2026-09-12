"""What a sentence is allowed to mean.

The parser proposes constraints for a person to confirm. These tests pin down
the three kinds of sentence it reads, the dates that make a rule temporary,
and -- just as important -- the sentences it refuses to guess at.
"""

from datetime import date, timedelta

import pytest

from backend.app.nl import parse

# The rehearsed demo week. 10 Sep 2026 is a Thursday, so tomorrow is a
# teaching day and the day after is not.
TODAY = date(2026, 9, 10)
TOMORROW = date(2026, 9, 11)
SATURDAY = date(2026, 9, 12)


def mehta(instance) -> str:
    return next(f.id for f in instance.faculty if "Mehta" in f.name)


# ----------------------------------------------------------------------
# dates: a temporary override rather than a permanent rule
# ----------------------------------------------------------------------


def test_tomorrow_becomes_a_dated_override(full_instance):
    rule = parse("Computer Lab 2 is unavailable tomorrow", full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert (rule.category, rule.scope) == ("availability", "TEMPORARY")
    assert rule.kind == "ROOM_UNAVAILABLE"
    assert rule.target_id == "CL2"
    assert rule.start_date == rule.end_date == TOMORROW
    assert rule.days == [TOMORROW.weekday()]
    assert rule.to_disruptions() == [
        {
            "kind": "ROOM_UNAVAILABLE",
            "target": "CL2",
            "day": TOMORROW.weekday(),
            "start_time": "09:00",
            "end_time": "23:59",
        }
    ]


def test_a_weekday_sentence_stays_a_permanent_rule(full_instance):
    """Naming a weekday means every week; only a date makes it temporary."""
    rule = parse("Prof. Mehta is unavailable after 2 PM on Friday", full_instance, today=TODAY)

    assert rule.scope == "BASE"
    assert rule.start_date is None
    assert rule.summary == "Prof. Mehta unavailable on Fri, 14:00-23:59"


def test_a_whole_week_override_covers_every_teaching_day(full_instance):
    rule = parse("Prof. Mehta is on leave all next week", full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert rule.scope == "TEMPORARY"
    assert rule.start_date == TODAY + timedelta(days=7 - TODAY.weekday())
    assert rule.end_date == rule.start_date + timedelta(days=4)
    assert rule.days == [0, 1, 2, 3, 4]


def test_a_weekend_date_is_refused_rather_than_scheduled(full_instance):
    rule = parse(
        f"CL2 is closed on {SATURDAY.strftime('%d %B')}", full_instance, today=TODAY
    )

    assert not rule.understood
    assert any("weekend" in issue for issue in rule.issues), rule.issues
    assert rule.to_disruptions() == []


# ----------------------------------------------------------------------
# standing policies
# ----------------------------------------------------------------------


def test_a_division_policy_applies_to_every_weekday(full_instance):
    rule = parse("TE-A should not have lectures after 4 PM", full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert rule.kind == "BATCH_UNAVAILABLE"
    assert rule.target_id == "TE-A"
    assert rule.scope == "BASE"
    assert rule.days == [0, 1, 2, 3, 4]
    assert (rule.start_time, rule.end_time) == ("16:00", "23:59")
    # Assuming every weekday is a real assumption, so it is disclosed.
    assert any("every weekday" in note for note in rule.assumptions), rule.assumptions


def test_an_absence_with_no_day_is_still_refused(full_instance):
    """A one-off absence with no day is missing information, not a policy."""
    rule = parse("Prof. Mehta is unavailable", full_instance, today=TODAY)

    assert not rule.understood
    assert any("weekday" in issue for issue in rule.issues)


# ----------------------------------------------------------------------
# workload rules
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence,field,value,named",
    [
        ("Maximum three consecutive lectures for faculty", "max_consecutive", 3, False),
        ("No more than 4 hours per day for Prof. Mehta", "max_daily_load", 4, True),
        ("Cap teaching at 16 hours per week", "max_weekly_load", 16, False),
    ],
)
def test_a_workload_sentence_becomes_a_rule(full_instance, sentence, field, value, named):
    rule = parse(sentence, full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert rule.category == "rule"
    change = rule.rule_changes[0]
    assert (change["rule"], change["value"]) == (field, value)
    assert change["faculty"] == (mehta(full_instance) if named else None)
    # A workload rule blocks nobody, so it proposes no disruptions.
    assert rule.to_disruptions() == []


def test_a_rule_reads_back_in_the_coordinators_own_terms(full_instance):
    rule = parse("Maximum three consecutive lectures for faculty", full_instance, today=TODAY)
    assert rule.summary == "Every teacher: maximum consecutive teaching hours set to 3"


def test_an_unusable_limit_is_refused(full_instance):
    rule = parse("Maximum 99 hours per week", full_instance, today=TODAY)

    assert not rule.understood
    assert any("1 to 40" in issue for issue in rule.issues), rule.issues


# ----------------------------------------------------------------------
# locks
# ----------------------------------------------------------------------


def test_a_lock_sentence_pins_one_session(full_instance):
    rule = parse("Lock DBMS for TE-B on Monday at 10 AM", full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert rule.category == "lock"
    lock = rule.locks[0]
    assert lock["subject_code"] == "CS352"  # "DBMS" is read as DBMS Lab
    assert lock["batch_id"] == "TE-B"
    assert (lock["label"], lock["timeslot_id"]) == ("Mon 10:00", 1)

    session = full_instance.session_by_id[lock["session_id"]]
    assert (session.batch_id, session.subject_code) == ("TE-B", "CS352")
    # A lock is not a disruption; it is a hard constraint for the next solve.
    assert rule.to_disruptions() == []


def test_a_lock_can_name_the_room_too(full_instance):
    rule = parse("Lock DBMS for TE-B in CL3 on Monday at 10 AM", full_instance, today=TODAY)

    assert rule.understood, rule.issues
    assert rule.locks[0]["room_id"] == "CL3"


def test_a_lock_without_a_start_time_says_so(full_instance):
    rule = parse("Lock DBMS for TE-B on Monday", full_instance, today=TODAY)

    assert not rule.understood
    assert any("start time" in issue for issue in rule.issues), rule.issues


def test_a_lock_for_an_unknown_subject_says_so(full_instance):
    rule = parse("Lock Astrophysics for TE-B on Monday at 10 AM", full_instance, today=TODAY)

    assert not rule.understood
    assert any("subject" in issue for issue in rule.issues), rule.issues


def test_a_lock_at_lunch_is_refused(full_instance):
    rule = parse("Lock DBMS for TE-B on Monday at 1 PM", full_instance, today=TODAY)

    assert not rule.understood
    assert any("teaching period" in issue for issue in rule.issues), rule.issues


# ----------------------------------------------------------------------
# through the API
# ----------------------------------------------------------------------


def test_the_parse_route_returns_a_structured_rule(client):
    body = client.post(
        "/api/parse", json={"text": "Maximum three consecutive lectures for faculty"}
    ).json()

    assert body["understood"] is True
    assert body["category"] == "rule"
    assert body["rule_changes"] == [
        {"rule": "max_consecutive", "value": 3, "faculty": None, "faculty_label": None}
    ]
    assert body["disruptions"] == []


def test_the_parse_route_dates_a_temporary_override(client):
    """The route reads "tomorrow" against the institution's today."""
    body = client.post(
        "/api/parse", json={"text": "Computer Lab 2 is unavailable tomorrow"}
    ).json()

    assert body["scope"] == "TEMPORARY"
    assert body["start_date"] == body["end_date"] == TOMORROW.isoformat()


def test_a_parsed_override_can_be_recorded_as_written(client):
    parsed = client.post(
        "/api/parse", json={"text": "Computer Lab 2 is unavailable tomorrow"}
    ).json()

    created = client.post(
        "/api/constraints",
        json={
            "category": parsed["scope"],
            "kind": parsed["kind"],
            "target_id": parsed["target_id"],
            "days": parsed["days"],
            "start_time": parsed["start_time"],
            "end_time": parsed["end_time"],
            "start_date": parsed["start_date"],
            "end_date": parsed["end_date"],
            "reason": parsed["text"],
        },
    )

    assert created.status_code == 200, created.text
    record = created.json()
    assert record["category"] == "TEMPORARY"
    assert record["lifecycle"] == "UPCOMING"
    assert record["start_date"] == TOMORROW.isoformat()


def test_the_parse_route_still_changes_nothing(client):
    before = client.get("/api/state").json()
    client.post("/api/parse", json={"text": "Lock DBMS for TE-B on Monday at 10 AM"})
    assert client.get("/api/state").json() == before
