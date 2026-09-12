"""JSON persistence for problem instances and published timetables.

A published timetable is stored data, not something regenerated on demand --
that is both how a real coordinator works and what makes a live demo
reproducible. Repair always runs against the *stored* baseline.

The database is the primary store; JSON remains the fixture format the
database is seeded from, and the export/recovery format a snapshot can always
be written back to. Newer fields are optional on read, so snapshots written
before they existed still load unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ..domain.models import (
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

SCHEMA_VERSION = 1


def instance_to_dict(inst: Instance) -> dict:
    return {
        "name": inst.name,
        "calendar": {"days": inst.calendar.days, "periods": inst.calendar.periods},
        "rooms": [
            {
                "id": r.id,
                "name": r.name,
                "capacity": r.capacity,
                "room_type": r.room_type.value,
                "unavailable": sorted(r.unavailable),
                "building": r.building,
                "capabilities": sorted(r.capabilities),
                "active": r.active,
            }
            for r in inst.rooms
        ],
        "faculty": [
            {
                "id": f.id,
                "name": f.name,
                "unavailable": sorted(f.unavailable),
                "max_daily_load": f.max_daily_load,
                "max_weekly_load": f.max_weekly_load,
                "max_consecutive": f.max_consecutive,
                "department": f.department,
                "preferred_off": sorted(f.preferred_off),
                "subjects": list(f.subjects),
            }
            for f in inst.faculty
        ],
        "batches": [
            {
                "id": b.id,
                "name": b.name,
                "strength": b.strength,
                "unavailable": sorted(b.unavailable),
                "department": b.department,
                "program": b.program,
                "year": b.year,
                "year_label": b.year_label,
                "semester": b.semester,
            }
            for b in inst.batches
        ],
        "sessions": [
            {
                "id": s.id,
                "subject_code": s.subject_code,
                "subject_name": s.subject_name,
                "batch_id": s.batch_id,
                "faculty_id": s.faculty_id,
                "duration": s.duration,
                "room_type": s.room_type.value,
                "elective_group": s.elective_group,
                "headcount": s.headcount,
                "required_capability": s.required_capability,
                "category": s.category,
            }
            for s in inst.sessions
        ],
        "locks": [
            {
                "session_id": lk.session_id,
                "timeslot_id": lk.timeslot_id,
                "room_id": lk.room_id,
                "reason": lk.reason,
            }
            for lk in sorted(inst.locks.values(), key=lambda lk: lk.session_id)
        ],
    }


def instance_from_dict(d: dict) -> Instance:
    cal = Calendar(days=d["calendar"]["days"], periods=d["calendar"]["periods"])
    return Instance(
        name=d["name"],
        calendar=cal,
        rooms=[
            Room(
                id=r["id"],
                name=r["name"],
                capacity=r["capacity"],
                room_type=RoomType(r["room_type"]),
                unavailable=frozenset(r["unavailable"]),
                building=r.get("building", ""),
                capabilities=frozenset(r.get("capabilities", [])),
                active=r.get("active", True),
            )
            for r in d["rooms"]
        ],
        faculty=[
            Faculty(
                id=f["id"],
                name=f["name"],
                unavailable=frozenset(f["unavailable"]),
                max_daily_load=f["max_daily_load"],
                max_weekly_load=f["max_weekly_load"],
                max_consecutive=f["max_consecutive"],
                department=f.get("department", ""),
                preferred_off=frozenset(f.get("preferred_off", [])),
                subjects=tuple(f.get("subjects", [])),
            )
            for f in d["faculty"]
        ],
        batches=[
            # Snapshots written before divisions could be blocked omit the key.
            Batch(
                id=b["id"],
                name=b["name"],
                strength=b["strength"],
                unavailable=frozenset(b.get("unavailable", [])),
                department=b.get("department", ""),
                program=b.get("program", ""),
                year=b.get("year", 0),
                year_label=b.get("year_label", ""),
                semester=b.get("semester", 0),
            )
            for b in d["batches"]
        ],
        sessions=[
            Session(
                id=s["id"],
                subject_code=s["subject_code"],
                subject_name=s["subject_name"],
                batch_id=s["batch_id"],
                faculty_id=s["faculty_id"],
                duration=s["duration"],
                room_type=RoomType(s["room_type"]),
                elective_group=s["elective_group"],
                headcount=s["headcount"],
                required_capability=s.get("required_capability"),
                category=s.get("category", ""),
            )
            for s in d["sessions"]
        ],
        locks={
            lk["session_id"]: Lock(
                session_id=lk["session_id"],
                timeslot_id=lk["timeslot_id"],
                room_id=lk.get("room_id"),
                reason=lk.get("reason", ""),
            )
            for lk in d.get("locks", [])
        },
    )


def timetable_to_dict(tt: Timetable) -> dict:
    return {
        "status": tt.status,
        "objective": tt.objective,
        "best_bound": tt.best_bound,
        "solve_seconds": tt.solve_seconds,
        "placements": [
            {
                "session_id": p.session_id,
                "timeslot_id": p.timeslot_id,
                "room_id": p.room_id,
            }
            for p in sorted(tt.placements.values(), key=lambda p: p.session_id)
        ],
    }


def timetable_from_dict(d: dict) -> Timetable:
    return Timetable(
        placements={
            p["session_id"]: Placement(
                session_id=p["session_id"],
                timeslot_id=p["timeslot_id"],
                room_id=p["room_id"],
            )
            for p in d["placements"]
        },
        status=d["status"],
        objective=d.get("objective"),
        best_bound=d.get("best_bound"),
        solve_seconds=d.get("solve_seconds", 0.0),
    )


def save_snapshot(
    path: str | Path,
    instance: Instance,
    timetable: Timetable,
    label: str = "published",
) -> Path:
    """Persist an instance together with the timetable published for it."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        # ISO-8601 UTC, e.g. "2026-09-08T17:04:22Z"
        "saved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "instance": instance_to_dict(instance),
        "timetable": timetable_to_dict(timetable),
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def load_snapshot(path: str | Path) -> tuple[Instance, Timetable, dict]:
    """Load a published snapshot. Returns (instance, timetable, metadata)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"Snapshot schema {version} is not supported "
            f"(this build reads version {SCHEMA_VERSION})"
        )
    meta = {"label": payload.get("label"), "saved_at": payload.get("saved_at")}
    return (
        instance_from_dict(payload["instance"]),
        timetable_from_dict(payload["timetable"]),
        meta,
    )
