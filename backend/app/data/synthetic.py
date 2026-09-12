"""Seeded synthetic department data.

Deterministic by design: a live demo must produce the same instance every run,
so the numbers on screen can be trusted and rehearsed. Nothing here is real
institutional data.

Default shape targets the scale quoted in the abstract -- ~150 weekly sessions,
40 timeslots, 20 rooms -- for a Computer Engineering department running three
years at once (Second, Third and Final Year, two divisions each). All six
divisions draw on one shared pool of faculty, lecture halls and laboratories,
which is what makes cross-year clashes a real constraint rather than a
theoretical one.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from ..domain.models import (
    Batch,
    Calendar,
    Faculty,
    Instance,
    Room,
    RoomType,
    Session,
)

THEORY_SUBJECTS = [
    ("CS301", "Data Structures & Algorithms"),
    ("CS302", "Database Management Systems"),
    ("CS303", "Operating Systems"),
    ("CS304", "Computer Networks"),
    ("CS305", "Software Engineering"),
    ("CS306", "Theory of Computation"),
    ("CS307", "Machine Learning"),
    ("CS308", "Compiler Design"),
    ("CS309", "Computer Architecture"),
    ("CS310", "Discrete Mathematics"),
    ("CS311", "Web Technologies"),
    ("CS312", "Artificial Intelligence"),
]

LAB_SUBJECTS = [
    ("CS351", "Data Structures Lab"),
    ("CS352", "DBMS Lab"),
    ("CS353", "Operating Systems Lab"),
    ("CS354", "Networks Lab"),
    ("CS355", "Machine Learning Lab"),
    ("CS356", "Web Technologies Lab"),
]

ELECTIVE_SUBJECTS = [
    ("CS401", "Cloud Computing"),
    ("CS402", "Cyber Security"),
    ("CS403", "Computer Vision"),
    ("CS404", "Natural Language Processing"),
    ("CS405", "Blockchain Systems"),
    ("CS406", "Internet of Things"),
]

# "Prof. Mehta" is deliberately present: the rehearsed disruption scenario in the
# project brief names this faculty member.
FACULTY_NAMES = [
    "Prof. Mehta",
    "Prof. Iyer",
    "Prof. Deshpande",
    "Prof. Nair",
    "Prof. Kulkarni",
    "Prof. Banerjee",
    "Prof. Rao",
    "Prof. Chatterjee",
    "Prof. Joshi",
    "Prof. Pillai",
    "Prof. Sharma",
    "Prof. Gokhale",
    "Prof. Fernandes",
    "Prof. Bhatt",
    "Prof. Reddy",
    "Prof. Sengupta",
]

DEPARTMENT = "Computer Engineering"
PROGRAM = "B.E. Computer Engineering"

# Year number, year label and the semester running in the odd term, keyed by
# the division prefix. All three years share one faculty and room pool.
YEAR_OF = {
    "SE": (2, "Second Year", 3),
    "TE": (3, "Third Year", 5),
    "BE": (4, "Final Year", 7),
}

BATCH_SPECS = [
    ("SE-A", "Second Year - Div A", 62),
    ("SE-B", "Second Year - Div B", 68),
    ("TE-A", "Third Year - Div A", 58),
    ("TE-B", "Third Year - Div B", 70),
    ("BE-A", "Final Year - Div A", 65),
    ("BE-B", "Final Year - Div B", 60),
]

# 14 lecture rooms + 6 labs = 20. The small lecture rooms cannot seat a full
# division, which is what makes the capacity constraint bind rather than be
# decorative -- they are only usable by split elective cohorts.
LECTURE_CAPACITIES = [80, 78, 75, 75, 72, 70, 70, 68, 66, 64, 48, 45, 42, 40]
LAB_CAPACITIES = [75, 75, 72, 72, 70, 70]

# Every lab has workstations; two also carry the switching and routing kit the
# Networks Lab needs, so that subject may only run in one of them.
LAB_EQUIPMENT = "computer"
NETWORKING_LABS = {"CL5", "CL6"}
SUBJECT_EQUIPMENT = {"CS354": "networking"}


@dataclass(frozen=True, slots=True)
class DepartmentSpec:
    seed: int = 7
    n_batches: int = 6
    theory_per_batch: int = 6
    sessions_per_theory: int = 3
    labs_per_batch: int = 2
    lab_duration: int = 2
    elective_options: int = 2
    sessions_per_elective: int = 3
    n_faculty: int = 16
    faculty_unavailable_slots: tuple[int, int] = (2, 5)
    room_unavailable_slots: tuple[int, int] = (0, 3)


def enrich_metadata(instance: Instance) -> Instance:
    """Fill in the institutional structure the scheduling data implies.

    Adds department, programme, year and semester to divisions; buildings and
    equipment to rooms; and equipment requirements to lab sessions. Only blank
    fields are filled, so anything set explicitly -- by an import or by an
    administrator -- is never overwritten. The same rules apply to the pinned
    demo baseline and to freshly generated departments, so the two agree.
    """
    batches = []
    for b in instance.batches:
        year = YEAR_OF.get(b.id.split("-")[0])
        if year and not b.year:
            number, label, semester = year
            b = replace(
                b,
                department=b.department or DEPARTMENT,
                program=b.program or PROGRAM,
                year=number,
                year_label=label,
                semester=semester,
            )
        batches.append(b)

    faculty = [
        f if f.department else replace(f, department=DEPARTMENT)
        for f in instance.faculty
    ]

    rooms = []
    for r in instance.rooms:
        if r.room_type is RoomType.LAB:
            building = r.building or "Lab Complex"
            caps = r.capabilities or frozenset(
                {LAB_EQUIPMENT, "networking"} if r.id in NETWORKING_LABS else {LAB_EQUIPMENT}
            )
        else:
            number = int("".join(c for c in r.id if c.isdigit()) or 0)
            building = r.building or ("A Block" if number < 108 else "B Block")
            caps = r.capabilities or frozenset({"projector"})
        rooms.append(replace(r, building=building, capabilities=caps))

    sessions = []
    for s in instance.sessions:
        if s.is_lab and s.required_capability is None:
            s = replace(
                s,
                required_capability=SUBJECT_EQUIPMENT.get(s.subject_code, LAB_EQUIPMENT),
            )
        if not s.category:
            s = replace(s, category="LAB" if s.is_lab else "THEORY")
        sessions.append(s)

    return instance.derive(
        batches=batches, faculty=faculty, rooms=rooms, sessions=sessions
    )


def build_department(spec: DepartmentSpec | None = None) -> Instance:
    """Build a realistic, reproducible department scheduling instance."""
    spec = spec or DepartmentSpec()
    rng = random.Random(spec.seed)
    calendar = Calendar()
    teaching_ids = [s.id for s in calendar.teaching_slots]

    batches = [
        Batch(id=bid, name=name, strength=strength)
        for bid, name, strength in BATCH_SPECS[: spec.n_batches]
    ]

    faculty = [
        Faculty(
            id=f"F{i + 1:02d}",
            name=FACULTY_NAMES[i % len(FACULTY_NAMES)],
            unavailable=frozenset(
                rng.sample(teaching_ids, rng.randint(*spec.faculty_unavailable_slots))
            ),
            max_daily_load=5,
            max_weekly_load=18,
            max_consecutive=3,
        )
        for i in range(spec.n_faculty)
    ]

    rooms: list[Room] = []
    for i, cap in enumerate(LECTURE_CAPACITIES):
        rooms.append(
            Room(
                id=f"LH{i + 101}",
                name=f"Lecture Hall {i + 101}",
                capacity=cap,
                room_type=RoomType.LECTURE,
                unavailable=frozenset(
                    rng.sample(teaching_ids, rng.randint(*spec.room_unavailable_slots))
                ),
            )
        )
    for i, cap in enumerate(LAB_CAPACITIES):
        rooms.append(
            Room(
                id=f"CL{i + 1}",
                name=f"Computer Lab {i + 1}",
                capacity=cap,
                room_type=RoomType.LAB,
                unavailable=frozenset(
                    rng.sample(teaching_ids, rng.randint(*spec.room_unavailable_slots))
                ),
            )
        )

    # Assign teaching duties to the least-loaded eligible faculty so no one is
    # pushed past their weekly cap before the solver even starts.
    load: dict[str, int] = {f.id: 0 for f in faculty}

    def assign(hours: int, batch_id: str, busy: dict[str, set[str]]) -> str:
        candidates = sorted(
            (f for f in faculty if batch_id not in busy.get(f.id, set())),
            key=lambda f: (load[f.id], f.id),
        )
        chosen = candidates[0] if candidates else min(faculty, key=lambda f: load[f.id])
        load[chosen.id] += hours
        busy.setdefault(chosen.id, set()).add(batch_id)
        return chosen.id

    sessions: list[Session] = []
    for b_index, batch in enumerate(batches):
        busy: dict[str, set[str]] = {}

        # Theory: rotate the subject catalogue so divisions of different years
        # study different subjects.
        offset = b_index * spec.theory_per_batch
        for t in range(spec.theory_per_batch):
            code, name = THEORY_SUBJECTS[(offset + t) % len(THEORY_SUBJECTS)]
            fid = assign(spec.sessions_per_theory, batch.id, busy)
            for k in range(spec.sessions_per_theory):
                sessions.append(
                    Session(
                        id=f"{batch.id}-{code}-{k + 1}",
                        subject_code=code,
                        subject_name=name,
                        batch_id=batch.id,
                        faculty_id=fid,
                        duration=1,
                        room_type=RoomType.LECTURE,
                    )
                )

        # Labs: one contiguous multi-hour block per lab subject per week.
        lab_offset = b_index * spec.labs_per_batch
        for t in range(spec.labs_per_batch):
            code, name = LAB_SUBJECTS[(lab_offset + t) % len(LAB_SUBJECTS)]
            fid = assign(spec.lab_duration, batch.id, busy)
            sessions.append(
                Session(
                    id=f"{batch.id}-{code}-1",
                    subject_code=code,
                    subject_name=name,
                    batch_id=batch.id,
                    faculty_id=fid,
                    duration=spec.lab_duration,
                    room_type=RoomType.LAB,
                )
            )

        # Electives: options run in parallel, so the division splits between them.
        cohort = math.ceil(batch.strength / spec.elective_options)
        elective_offset = b_index * spec.elective_options
        for t in range(spec.elective_options):
            code, name = ELECTIVE_SUBJECTS[
                (elective_offset + t) % len(ELECTIVE_SUBJECTS)
            ]
            fid = assign(spec.sessions_per_elective, batch.id, busy)
            for k in range(spec.sessions_per_elective):
                sessions.append(
                    Session(
                        id=f"{batch.id}-{code}-{k + 1}",
                        subject_code=code,
                        subject_name=name,
                        batch_id=batch.id,
                        faculty_id=fid,
                        duration=1,
                        room_type=RoomType.LECTURE,
                        elective_group=f"{batch.id}-ELECTIVE-1-{k + 1}",
                        headcount=cohort,
                    )
                )

    return enrich_metadata(
        Instance(
            name="Computer Engineering Department (synthetic)",
            calendar=calendar,
            rooms=rooms,
            faculty=faculty,
            batches=batches,
            sessions=sessions,
        )
    )
