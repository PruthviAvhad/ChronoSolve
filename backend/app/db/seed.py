"""First-run seeding, demo accounts, and restoring the rehearsed baseline.

The database is seeded from `data/fixtures/rehearsed_v1.json`, an immutable
copy of the timetable the demo was rehearsed on. The application never writes
that file, so no amount of clicking in the running app can destroy the
reference it recovers from.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session as DB

from ..data.store import load_snapshot
from ..data.synthetic import enrich_metadata
from ..domain.models import Instance, Timetable
from ..services.auth import ADMIN, FACULTY, STUDENT
from ..services.constraints import ACTIVE, INACTIVE, PENDING
from ..solver.metrics import diff_schedules
from ..solver.model import ObjectiveWeights
from . import models as m
from . import repository as repo
from .session import ROOT

FIXTURE = ROOT / "data" / "fixtures" / "rehearsed_v1.json"

# Demo credentials, documented in the README. Real deployments create their
# own accounts; these exist so a judge can try every role in seconds.
DEMO_PASSWORDS = {ADMIN: "admin123", FACULTY: "faculty123", STUDENT: "student123"}


def demo_accounts(instance: Instance) -> list[dict]:
    """One coordinator, one account per teacher, one student viewer."""
    accounts = [
        {"username": "admin", "display_name": "Timetable Coordinator", "role": ADMIN}
    ]
    used = {"admin", "student"}
    for f in instance.faculty:
        surname = f.name.replace("Prof.", "").replace("Dr.", "").strip().split()[-1].lower()
        username = surname if surname not in used else f"{surname}{f.id.lower()}"
        used.add(username)
        accounts.append(
            {
                "username": username,
                "display_name": f.name,
                "role": FACULTY,
                "faculty_id": f.id,
            }
        )
    accounts.append(
        {
            "username": "student",
            "display_name": "Student viewer",
            "role": STUDENT,
            "batch_id": instance.batches[0].id if instance.batches else None,
        }
    )
    return accounts


def ensure_demo_users(db: DB, instance: Instance) -> None:
    for acct in demo_accounts(instance):
        if repo.user_by_username(db, acct["username"]) is None:
            repo.create_user(db, password=DEMO_PASSWORDS[acct["role"]], **acct)
    db.flush()


def seed(
    db: DB,
    instance: Instance,
    timetable: Timetable,
    label: str,
    *,
    reason: str = "Rehearsed baseline",
    with_users: bool = True,
) -> m.VersionRow:
    """Store a department and publish `timetable` as its first version."""
    repo.replace_config(db, instance)
    repo.set_weights(db, ObjectiveWeights())
    version = repo.add_version(
        db,
        instance=instance,
        timetable=timetable,
        status=repo.PROPOSED,
        label=label,
        created_by="system",
        reason=reason,
    )
    repo.publish(db, version)
    if with_users:
        ensure_demo_users(db, instance)
    db.commit()
    return version


def seed_if_empty(db: DB, fixture=FIXTURE) -> bool:
    """Seed from the rehearsed fixture when nothing has been published yet."""
    if repo.current_version(db) is not None:
        return False
    instance, timetable, meta = load_snapshot(fixture)
    seed(db, enrich_metadata(instance), timetable, meta.get("label") or "published-v1")
    return True


def restore_rehearsed(db: DB, by: str, fixture=FIXTURE) -> m.VersionRow:
    """Republish the rehearsed baseline as a new version.

    History is kept: earlier versions remain in the list, superseded. Rules
    and overrides added since are switched off, since they may refer to the
    configuration this restores over.
    """
    instance, timetable, meta = load_snapshot(fixture)
    instance = enrich_metadata(instance)
    current = repo.current_version(db)
    repo.discard_open_proposals(db)
    repo.replace_config(db, instance)
    db.execute(
        update(m.ConstraintRow)
        .where(m.ConstraintRow.status.in_((ACTIVE, PENDING)))
        .values(status=INACTIVE)
    )
    diff = (
        diff_schedules(instance, repo.version_timetable(db, current), timetable)
        if current is not None
        else None
    )
    version = repo.add_version(
        db,
        instance=instance,
        timetable=timetable,
        status=repo.PROPOSED,
        label=f"{meta.get('label') or 'published-v1'} (restored)",
        created_by=by,
        reason="Restored the rehearsed demo baseline",
        source=current,
        diff=diff,
    )
    repo.publish(db, version)
    ensure_demo_users(db, instance)
    db.commit()
    return version


def user_count(db: DB) -> int:
    return len(list(db.scalars(select(m.UserRow.id))))
