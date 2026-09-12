"""Natural-language constraint entry.

A deterministic, dependency-free parser that turns coordinator sentences into
*candidate* structured rules:

    "Prof. Mehta is unavailable after 2 PM on Friday"
        -> {"kind": "FACULTY_UNAVAILABLE", "target": "F01",
            "day": 4, "start_time": "14:00", "end_time": "23:59"}

It reads three kinds of sentence:

  availability  someone or something cannot be scheduled in a window
  rule          a workload limit, e.g. "at most 3 lectures back to back"
  lock          pin one session to a chosen day and start time

and, when a sentence names a date rather than a weekday, it proposes a dated
override instead of a permanent rule.

Three things this layer deliberately is not:

* It does not generate or modify the timetable. CP-SAT does that. This only
  proposes a constraint.
* It never applies anything on its own. The parse is returned for a human to
  confirm, and every assumption it made is reported alongside it.
* It is not a language model. It is regex and a lexicon, so it runs offline,
  costs nothing, and fails predictably -- when it cannot parse a sentence it
  says so instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .domain.models import DAYS, PERIOD_START, Instance

FACULTY_UNAVAILABLE = "FACULTY_UNAVAILABLE"
ROOM_UNAVAILABLE = "ROOM_UNAVAILABLE"
BATCH_UNAVAILABLE = "BATCH_UNAVAILABLE"

# What kind of constraint a sentence proposes, and whether it is permanent.
AVAILABILITY = "availability"
RULE = "rule"
LOCK = "lock"
BASE = "BASE"
TEMPORARY = "TEMPORARY"

WEEKDAYS = (0, 1, 2, 3, 4)

DAY_WORDS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "weds": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
}

MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Phrases that mark the subject as unavailable rather than merely mentioned.
UNAVAILABLE_WORDS = (
    "unavailable",
    "at a seminar",
    "seminar",
    "assembly",
    "exam",
    "field visit",
    "industrial visit",
    "not available",
    "on leave",
    "away",
    "absent",
    "off",
    "cannot teach",
    "can not teach",
    "can't teach",
    "cant teach",
    "busy",
    "closed",
    "shut",
    "maintenance",
    "under repair",
    "out of service",
    "blocked",
    "booked",
    "reserved",
    "occupied",
)

# Wordings that state a standing policy rather than one absence. They state
# unavailability too, and they imply every weekday when no day is named.
POLICY_WORDS = (
    "should not have",
    "should not be",
    "shouldn't have",
    "must not have",
    "must not be",
    "should have no",
    "not be scheduled",
    "no lectures",
    "no classes",
    "no sessions",
)

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

LIMIT_WORDS = ("maximum", "max", "no more than", "not more than", "at most", "limit", "cap")

# Which workload rule a sentence is about. Order matters: "3 hours a day" is a
# daily cap even though the sentence also mentions hours.
RULE_FIELDS = (
    ("max_consecutive", ("consecutive", "back to back", "back-to-back", "in a row")),
    ("max_daily_load", ("per day", "a day", "each day", "in a day", "daily")),
    ("max_weekly_load", ("per week", "a week", "each week", "in a week", "weekly")),
)

READABLE_FIELDS = {
    "max_consecutive": "maximum consecutive teaching hours",
    "max_daily_load": "maximum teaching hours per day",
    "max_weekly_load": "maximum teaching hours per week",
}

DAY_START = "09:00"
DAY_END = "23:59"

NAMED_WINDOWS = {
    "morning": (DAY_START, "13:00"),
    "afternoon": ("13:00", DAY_END),
    "evening": ("16:00", DAY_END),
    "all day": (DAY_START, DAY_END),
    "whole day": (DAY_START, DAY_END),
    "entire day": (DAY_START, DAY_END),
    "full day": (DAY_START, DAY_END),
}

TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"

NO_TIME = "no time given, so the whole day is blocked"


@dataclass(slots=True)
class ParsedRule:
    """One candidate constraint, awaiting human confirmation."""

    text: str
    kind: str | None = None
    target_id: str | None = None
    target_label: str | None = None
    days: list[int] = field(default_factory=list)
    start_time: str = DAY_START
    end_time: str = DAY_END
    issues: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    # availability | rule | lock, and whether it is permanent or dated.
    category: str = AVAILABILITY
    scope: str = BASE
    start_date: date | None = None
    end_date: date | None = None
    rule_changes: list[dict] = field(default_factory=list)
    locks: list[dict] = field(default_factory=list)

    @property
    def understood(self) -> bool:
        if self.issues:
            return False
        if self.category == RULE:
            return bool(self.rule_changes)
        if self.category == LOCK:
            return bool(self.locks)
        return self.kind is not None and bool(self.days)

    @property
    def when(self) -> str:
        """The days this applies to -- as dates when it is a dated override."""
        if self.scope == TEMPORARY and self.start_date and self.end_date:
            if self.start_date == self.end_date:
                return self.start_date.strftime("%a %d %b %Y")
            return (
                f"{self.start_date.strftime('%d %b')} - "
                f"{self.end_date.strftime('%d %b %Y')}"
            )
        return ", ".join(DAYS[d] for d in self.days)

    @property
    def summary(self) -> str:
        if not self.understood:
            return "Could not build a rule from this sentence."
        if self.category == RULE:
            return "; ".join(
                f"{c['faculty_label'] or 'Every teacher'}: "
                f"{READABLE_FIELDS[c['rule']]} set to {c['value']}"
                for c in self.rule_changes
            )
        if self.category == LOCK:
            lock = self.locks[0]
            where = lock["label"] + (f" in {lock['room_id']}" if lock["room_id"] else "")
            return f"{lock['subject_code']} ({lock['batch_id']}) pinned to {where}"
        window = (
            "all day"
            if (self.start_time, self.end_time) == (DAY_START, DAY_END)
            else f"{self.start_time}-{self.end_time}"
        )
        return f"{self.target_label} unavailable on {self.when}, {window}"

    def to_disruptions(self) -> list[dict]:
        """The structured rules, in the shape POST /api/what-if accepts."""
        if not self.understood or self.category != AVAILABILITY:
            return []
        return [
            {
                "kind": self.kind,
                "target": self.target_id,
                "day": day,
                "start_time": self.start_time,
                "end_time": self.end_time,
            }
            for day in self.days
        ]


def _to_24h(hour: int, minute: int, meridiem: str | None) -> tuple[str, str | None]:
    """Return "HH:MM" plus a note when the meridiem had to be inferred."""
    note = None
    m = (meridiem or "").replace(".", "").lower()
    if m == "pm" and hour != 12:
        hour += 12
    elif m == "am" and hour == 12:
        hour = 0
    elif not m and hour < 9:
        # The teaching day is 09:00-17:00, so a bare "2" means the afternoon.
        hour += 12
        note = f"read '{hour - 12}' as {hour}:00 (the teaching day ends at 17:00)"
    return f"{hour:02d}:{minute:02d}", note


def _parse_window(text: str, rule: ParsedRule) -> None:
    """Fill start_time/end_time from whichever time phrase is present."""
    for phrase, (start, end) in NAMED_WINDOWS.items():
        if phrase in text:
            rule.start_time, rule.end_time = start, end
            return

    ranged = re.search(
        rf"(?:from\s+)?{TIME}\s*(?:-|–|to|till|until|through)\s*{TIME}", text
    )
    if ranged:
        start, note_a = _to_24h(
            int(ranged.group(1)), int(ranged.group(2) or 0), ranged.group(3)
        )
        end, note_b = _to_24h(
            int(ranged.group(4)), int(ranged.group(5) or 0), ranged.group(6)
        )
        rule.start_time, rule.end_time = start, end
        for note in (note_a, note_b):
            if note:
                rule.assumptions.append(note)
        return

    after = re.search(rf"(?:after|from|past)\s+{TIME}", text)
    if after:
        start, note = _to_24h(
            int(after.group(1)), int(after.group(2) or 0), after.group(3)
        )
        rule.start_time, rule.end_time = start, DAY_END
        if note:
            rule.assumptions.append(note)
        return

    before = re.search(rf"(?:before|until|till|up to)\s+{TIME}", text)
    if before:
        end, note = _to_24h(
            int(before.group(1)), int(before.group(2) or 0), before.group(3)
        )
        rule.start_time, rule.end_time = DAY_START, end
        if note:
            rule.assumptions.append(note)
        return

    at_time = re.search(rf"\bat\s+{TIME}", text)
    if at_time:
        start, note = _to_24h(
            int(at_time.group(1)), int(at_time.group(2) or 0), at_time.group(3)
        )
        rule.start_time = start
        rule.end_time = f"{int(start[:2]) + 1:02d}:{start[3:]}"
        if note:
            rule.assumptions.append(note)
        return

    rule.assumptions.append(NO_TIME)


def _room_aliases(instance: Instance) -> dict[str, str]:
    """Every spelling a coordinator might reasonably use for each room."""
    aliases: dict[str, str] = {}
    for r in instance.rooms:
        keys = {r.id.lower(), r.name.lower()}
        digits = re.sub(r"\D", "", r.id)
        prefix = re.sub(r"\d", "", r.id).lower()
        keys.add(f"{prefix} {digits}")
        if r.room_type.value == "LAB":
            keys |= {f"lab {digits}", f"lab{digits}", f"computer lab {digits}"}
        else:
            keys |= {
                f"hall {digits}",
                f"room {digits}",
                f"lecture hall {digits}",
                f"classroom {digits}",
            }
        for key in keys:
            aliases[key] = r.id
    return aliases


def _match_faculty(text: str, instance: Instance) -> tuple[str, str, int] | None:
    """Best faculty match as (id, display name, matched length)."""
    best = None
    for f in instance.faculty:
        full = f.name.lower()
        surname = full.replace("prof.", "").replace("dr.", "").strip()
        for candidate in (full, surname, f.id.lower()):
            if candidate and candidate in text:
                score = len(candidate)
                if best is None or score > best[2]:
                    best = (f.id, f.name, score)
    return best


def _match_batch(text: str, instance: Instance) -> tuple[str, str, int] | None:
    """Divisions are usually written exactly as their id (SE-A, TE-B)."""
    best = None
    for b in instance.batches:
        for candidate in (b.id.lower(), b.name.lower()):
            if candidate and candidate in text:
                score = len(candidate)
                if best is None or score > best[2]:
                    best = (b.id, b.id, score)
    return best


def _match_room(text: str, instance: Instance) -> tuple[str, str, int] | None:
    best = None
    for alias, room_id in _room_aliases(instance).items():
        if alias and alias in text:
            score = len(alias)
            if best is None or score > best[2]:
                room = instance.room_by_id[room_id]
                best = (room_id, f"{room.id} ({room.name})", score)
    return best


def _subject_aliases(name: str, code: str) -> set[str]:
    """Spellings of a subject: its code, its name, and the usual short forms."""
    lowered = name.lower()
    aliases = {code.lower(), lowered}
    trimmed = re.sub(r"\s+lab$", "", lowered).strip()
    aliases.add(trimmed)
    words = [w for w in re.findall(r"[a-z]+", trimmed) if w not in ("and", "of", "the")]
    if len(words) > 1:
        aliases.add("".join(w[0] for w in words))
    return {a for a in aliases if len(a) > 1}


def _match_subject(text: str, instance: Instance, batch_id: str | None) -> str | None:
    """The subject code a sentence names, among one division's subjects."""
    best: tuple[int, str] | None = None
    for s in instance.sessions:
        if batch_id and s.batch_id != batch_id:
            continue
        for alias in _subject_aliases(s.subject_name, s.subject_code):
            if re.search(rf"\b{re.escape(alias)}\b", text) and (
                best is None or len(alias) > best[0]
            ):
                best = (len(alias), s.subject_code)
    return best[1] if best else None


def _named_days(text: str) -> list[int]:
    found: set[int] = set()
    for word, index in DAY_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            found.add(index)
    return sorted(found)


def _number(text: str) -> int | None:
    digits = re.search(r"\b(\d{1,2})\b", text)
    if digits:
        return int(digits.group(1))
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return None


def _year_for(month: int, day: int, today: date) -> date:
    """A bare "15 September" means the next one, not one long past."""
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        return date(today.year, month, 1)
    if (today - candidate).days > 180:
        candidate = date(today.year + 1, month, day)
    return candidate


def _parse_dates(text: str, today: date) -> tuple[date, date] | None:
    """The dates a sentence names, if it names dates rather than weekdays."""
    if "day after tomorrow" in text:
        d = today + timedelta(days=2)
        return d, d
    if "tomorrow" in text:
        d = today + timedelta(days=1)
        return d, d
    if re.search(r"\btoday\b", text):
        return today, today
    if "next week" in text:
        monday = today + timedelta(days=7 - today.weekday())
        return monday, monday + timedelta(days=4)
    if "this week" in text:
        monday = today - timedelta(days=today.weekday())
        return monday, monday + timedelta(days=4)

    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        try:
            d = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
        return d, d

    day_month = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})\b", text)
    if day_month and day_month.group(2) in MONTHS:
        d = _year_for(MONTHS[day_month.group(2)], int(day_month.group(1)), today)
        return d, d

    month_day = re.search(r"\b([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b", text)
    if month_day and month_day.group(1) in MONTHS:
        d = _year_for(MONTHS[month_day.group(1)], int(month_day.group(2)), today)
        return d, d
    return None


def _parse_rule(text: str, instance: Instance) -> list[dict]:
    """A workload limit, e.g. "at most 3 lectures back to back"."""
    field_name = next(
        (name for name, words in RULE_FIELDS if any(w in text for w in words)), None
    )
    if field_name is None or not any(w in text for w in LIMIT_WORDS):
        return []
    value = _number(text)
    if value is None:
        return []
    match = _match_faculty(text, instance)
    return [
        {
            "rule": field_name,
            "value": value,
            "faculty": match[0] if match else None,
            "faculty_label": match[1] if match else None,
        }
    ]


def _slot_at(instance: Instance, day: int, start_time: str):
    """The teaching slot that starts at this time, or None."""
    for slot in instance.calendar.slots:
        if slot.day == day and PERIOD_START[slot.period] == start_time:
            return None if slot.is_lunch else slot
    return None


def _parse_lock(rule: ParsedRule, text: str, instance: Instance) -> ParsedRule:
    """Pin one session to a named day and start time."""
    rule.category = LOCK
    batch = _match_batch(text, instance)
    if batch is None:
        rule.issues.append("Name the division whose class to lock, e.g. 'TE-A'.")
    code = _match_subject(text, instance, batch[0] if batch else None)
    if code is None:
        rule.issues.append(
            "No subject recognised. Name it as it appears in the timetable, "
            "e.g. 'DBMS Lab' or 'CS352'."
        )
    days = _named_days(text)
    if not days:
        rule.issues.append("No weekday found. Name the day to lock it on, e.g. 'on Monday'.")

    _parse_window(text, rule)
    if rule.assumptions[-1:] == [NO_TIME]:
        rule.assumptions.pop()
        rule.issues.append("Name the start time to lock it at, e.g. 'at 10 AM'.")

    if rule.issues or batch is None or code is None:
        return rule

    rule.days = [days[0]]
    slot = _slot_at(instance, days[0], rule.start_time)
    if slot is None:
        rule.issues.append(f"{rule.start_time} is not the start of a teaching period.")
        return rule

    mine = sorted(
        (s for s in instance.sessions if s.batch_id == batch[0] and s.subject_code == code),
        key=lambda s: s.id,
    )
    if len(mine) > 1:
        rule.assumptions.append(
            f"{batch[0]} has {len(mine)} weekly {code} sessions; the first is proposed"
        )
    session = mine[0]
    room = _match_room(text, instance)
    rule.kind = LOCK
    rule.target_id = session.id
    rule.target_label = f"{session.subject_code} {session.subject_name}"
    rule.locks = [
        {
            "session_id": session.id,
            "subject_code": session.subject_code,
            "batch_id": session.batch_id,
            "timeslot_id": slot.id,
            "label": slot.label,
            "room_id": room[0] if room else None,
        }
    ]
    return rule


def parse(text: str, instance: Instance, today: date | None = None) -> ParsedRule:
    """Turn one sentence into a candidate rule. Never applies anything."""
    rule = ParsedRule(text=text.strip())
    lowered = " " + re.sub(r"\s+", " ", text.lower().strip()) + " "
    today = today or date.today()

    if not lowered.strip():
        rule.issues.append(
            "Enter a sentence such as "
            "'Prof. Mehta is unavailable after 2 PM on Friday'."
        )
        return rule

    # --- a date makes it a dated override, not a permanent rule --------
    window = _parse_dates(lowered, today)
    if window is not None:
        rule.scope = TEMPORARY
        rule.start_date, rule.end_date = window
        span = (window[1] - window[0]).days
        rule.days = sorted(
            {(window[0] + timedelta(days=i)).weekday() for i in range(span + 1)}
            & set(WEEKDAYS)
        )
        if not rule.days:
            rule.issues.append(
                f"{rule.when} is a weekend, and there are no classes then."
            )

    # --- pinning one session where a person chose ----------------------
    if re.search(r"\b(lock|locked|pin|pinned)\b", lowered):
        return _parse_lock(rule, lowered, instance)

    # --- a workload rule -----------------------------------------------
    changes = _parse_rule(lowered, instance)
    if changes:
        rule.category = RULE
        rule.kind = "RULE"
        rule.rule_changes = changes
        rule.target_id = changes[0]["faculty"]
        rule.target_label = changes[0]["faculty_label"]
        unusable = [c for c in changes if not 1 <= c["value"] <= 40]
        if unusable:
            rule.issues.append(
                f"A limit of {unusable[0]['value']} is outside the usable range "
                f"of 1 to 40 hours."
            )
        return rule

    # --- what is being blocked ---------------------------------------
    # The longest, most specific mention wins across all three entity kinds.
    matches = [
        (FACULTY_UNAVAILABLE, _match_faculty(lowered, instance)),
        (ROOM_UNAVAILABLE, _match_room(lowered, instance)),
        (BATCH_UNAVAILABLE, _match_batch(lowered, instance)),
    ]
    found = [(kind, m) for kind, m in matches if m is not None]

    if found:
        kind, best = max(found, key=lambda item: item[1][2])
        rule.kind = kind
        rule.target_id, rule.target_label = best[0], best[1]
    else:
        rule.issues.append(
            "No known faculty member, room or division recognised in that "
            "sentence. Use a name as it appears in the department data, e.g. "
            "'Prof. Mehta', 'CL3' or 'SE-A'."
        )

    # --- is it actually an unavailability? ----------------------------
    policy = any(w in lowered for w in POLICY_WORDS)
    if not policy and not any(w in lowered for w in UNAVAILABLE_WORDS):
        rule.issues.append(
            "No unavailability was stated. Try wording such as 'is unavailable', "
            "'is on leave', or 'is closed'."
        )

    # --- when ---------------------------------------------------------
    # A named day narrows a dated window; a standing policy with no day at
    # all means every weekday, and says so.
    named = _named_days(lowered)
    if named and rule.days:
        rule.days = sorted(set(named) & set(rule.days)) or named
    elif named:
        rule.days = named
    elif window is None:
        if policy or "every day" in lowered or "all week" in lowered:
            rule.days = list(WEEKDAYS)
            rule.assumptions.append("no day named, so the rule applies to every weekday")
        else:
            rule.issues.append(
                "No weekday found. Name a day, e.g. 'on Friday' or "
                "'on Monday and Tuesday'."
            )

    _parse_window(lowered, rule)

    if rule.days and rule.kind and rule.start_time >= rule.end_time:
        rule.issues.append(
            f"The time window {rule.start_time}-{rule.end_time} ends before it "
            f"starts."
        )

    return rule
