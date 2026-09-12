"""ChronoSolve end-to-end console demonstration.

Runs the finale workflow from the project brief:

    Import & Validate -> Generate -> Inspect Views -> Simulate Disruption
    -> What-If -> Re-Optimize -> Explain & Measure -> Approve & Export

Usage:
    python -m backend.demo                 # use the published baseline on disk
    python -m backend.demo --regenerate    # solve a fresh baseline and publish it
    python -m backend.demo --scenario lab  # pick a disruption scenario
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .app.data.excel import read_workbook, to_workbook
from .app.data.store import load_snapshot, save_snapshot
from .app.data.synthetic import build_department
from .app.export import to_ics, to_pdf, to_xlsx
from .app.domain.models import Instance, Timetable
from .app.solver.disruption import (
    apply_disruptions,
    apply_rule_changes,
    builtin_scenarios,
    directly_affected,
)
from .app.solver.engine import generate
from .app.solver.explain import explain_move, explain_session
from .app.solver.metrics import schedule_metrics
from .app.solver.repair import repair
from .app.solver.validate import sessions_breaking_load_rules, validate
from .app.views import batch_view, faculty_view

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "data" / "published_baseline.json"
REPAIRED = ROOT / "data" / "repaired_timetable.json"
EXPORTS = ROOT / "data" / "exports"

RULE = "=" * 78
THIN = "-" * 78


def banner(step: str, title: str) -> None:
    print(f"\n{RULE}\n{step}  {title}\n{RULE}")


def technical_proof(instance: Instance, timetable: Timetable, label: str) -> None:
    report = validate(instance, timetable)
    print(f"\n  {label}")
    print("  Solver                      Google OR-Tools CP-SAT")
    print(f"  Status                      {timetable.status}")
    if timetable.objective is not None:
        print(f"  Objective                   {timetable.objective:.0f}")
        print(f"  Best bound                  {timetable.best_bound:.0f}")
    print(f"  Solve time                  {timetable.solve_seconds:.2f}s")
    print("\n  Hard constraints (verified independently of the solver model)")
    print(report.render())
    print("\n  Schedule quality")
    print(schedule_metrics(instance, timetable).render())


def main() -> None:
    ap = argparse.ArgumentParser(description="ChronoSolve console demo")
    ap.add_argument("--regenerate", action="store_true", help="solve a fresh baseline")
    ap.add_argument("--scenario", default="faculty", help="faculty | wednesday | lab")
    ap.add_argument("--time-limit", type=float, default=20.0)
    ap.add_argument(
        "--import", dest="import_path", help="load the department from an .xlsx workbook"
    )
    ap.add_argument(
        "--template", help="write the current department out as an .xlsx template"
    )
    args = ap.parse_args()

    # ---- 01 IMPORT & VALIDATE ---------------------------------------
    banner("01", "IMPORT & VALIDATE")
    published: Timetable | None

    if args.template:
        source, _, _ = (
            load_snapshot(BASELINE) if BASELINE.exists() else (build_department(), None, None)
        )
        target = Path(args.template)
        target.write_bytes(to_workbook(source))
        print(f"  template written to {target}")
        print("  edit it, then re-run with --import <file>")
        return

    if args.import_path:
        path = Path(args.import_path)
        print(f"  reading {path}")
        inst, report = read_workbook(path.read_bytes(), name=path.stem)
        print(report.render())
        if not report.ok:
            print(f"\n  {len(report.errors)} error(s) - nothing was imported.")
            return
        published = None
    elif args.regenerate or not BASELINE.exists():
        inst = build_department()
        print(f"  {inst.describe()}")
        print("  source: seeded synthetic department (no real institutional data)")
        published = None
    else:
        inst, published, meta = load_snapshot(BASELINE)
        print(f"  {inst.describe()}")
        print(f"  published baseline loaded: {meta['label']} @ {meta['saved_at']}")

    lab_hours = sum(s.duration for s in inst.sessions if s.is_lab)
    print(
        f"  {len(inst.elective_groups())} parallel elective groups, "
        f"{lab_hours} contiguous lab hours, "
        f"{len(inst.calendar.teaching_slots)} teachable periods per week"
    )

    # ---- 02 GENERATE -------------------------------------------------
    banner("02", "GENERATE")
    if published is None:
        print(f"  solving with CP-SAT (limit {args.time_limit:.0f}s)...")
        out = generate(inst, time_limit=args.time_limit, workers=16)
        if not out.is_solved:
            print(f"  no feasible timetable: {out.status}")
            return
        published = out.timetable
        print(f"  model: {out.model_stats}")
        save_snapshot(BASELINE, inst, published, label="published-v1")
        print(f"  published -> {BASELINE.relative_to(ROOT)}")
    else:
        print("  using the timetable already published to students and faculty")

    technical_proof(inst, published, "PUBLISHED TIMETABLE")

    # ---- 03 INSPECT VIEWS -------------------------------------------
    banner("03", "INSPECT VIEWS")
    print("  Three views, one underlying schedule state.")
    print(batch_view(inst, published, "SE-B"))
    print(faculty_view(inst, published, "F01"))

    # ---- 04 SIMULATE DISRUPTION -------------------------------------
    banner("04", "SIMULATE DISRUPTION")
    available = builtin_scenarios(inst)
    if args.scenario not in available:
        print(f"  unknown scenario {args.scenario!r}; choose from {list(available)}")
        return
    chosen = available[args.scenario]
    story, disruptions, rules = chosen.story, chosen.disruptions, chosen.rule_changes
    print(f"  {story}")
    for d in disruptions:
        print(f"    - {d.description}  (blocks {len(d.timeslots)} periods)")
    for r in rules:
        print(f"    - rule change: {r.description}")

    disrupted = apply_rule_changes(apply_disruptions(inst, disruptions), rules)
    affected = sorted(
        set(directly_affected(inst, published, disruptions))
        | set(sessions_breaking_load_rules(disrupted, published))
    )
    print(f"\n  Directly affected sessions: {len(affected)}")
    for sid in affected:
        s = inst.session_by_id[sid]
        p = published.placements[sid]
        slot = inst.calendar.by_id[p.timeslot_id]
        extra = f"  [{s.duration}h block]" if s.duration > 1 else ""
        print(
            f"    - {s.subject_name} ({s.batch_id}) "
            f"{slot.long_label} in {p.room_id}{extra}"
        )

    # ---- 05/06 WHAT-IF + RE-OPTIMIZE --------------------------------
    banner("05/06", "WHAT-IF ANALYSIS  ->  MINIMUM-DISRUPTION RE-OPTIMIZATION")
    print("  The published timetable is the reference; deviation is penalised.")
    print("  Phase 1 minimises moves. Phase 2 maximises quality without adding moves.")

    result = repair(
        disrupted, published, disruptions, phase1_limit=10.0, phase2_limit=5.0
    )

    if not result.solved:
        banner("07", "INFEASIBILITY DIAGNOSIS")
        print(f"  No feasible repair exists  (solver status {result.status})")
        print(f"  {len(affected)} published sessions are directly hit.\n")
        if result.diagnosis:
            print(result.diagnosis.render())
            print(
                "  Each blocking finding is a necessary condition that fails, so it\n"
                "  proves no timetable exists. Passing every check would not prove\n"
                "  one does - rules can still conflict in combination."
            )
        print("\n  The published timetable is untouched.")
        return

    diff = result.diff
    print(
        f"\n  phase 1 {result.phase1_seconds:.2f}s  "
        f"phase 2 {result.phase2_seconds:.2f}s  "
        f"total {result.total_seconds:.2f}s  status {result.status}"
    )

    # ---- 07 EXPLAIN & MEASURE ---------------------------------------
    banner("07", "EXPLAIN & MEASURE")
    print(f"  {'DISRUPTION':<28}{story}")
    print(THIN)
    print(f"  {'Directly affected sessions':<28}{len(affected)}")
    print(f"  {'Total sessions':<28}{diff.total}")
    print(f"  {'Unchanged':<28}{diff.unchanged}")
    print(f"  {'Changed':<28}{diff.changed}")
    print(f"  {'  moved to a new time':<28}{diff.time_moves}")
    print(f"  {'  moved room only':<28}{diff.room_only_moves}")
    print(THIN)
    print(f"  {'SCHEDULE RETENTION':<28}{diff.retention_pct:.1f}%")
    print(f"  {'  time retention':<28}{diff.time_retention_pct:.1f}%")
    print(THIN)

    if diff.changes:
        print("\n  PROPOSED CHANGES (old -> new), with the rule that forced each\n")
        print(f"    {'subject':<30}{'batch':<7}{'from':<18}{'to':<18}")
        for c in diff.changes:
            frm = f"{c.from_label} {c.from_room}"
            to = f"{c.to_label} {c.to_room}"
            print(f"    {c.subject_name[:29]:<30}{c.batch_id:<7}{frm:<18}{to:<18}")
            if c.from_slot is not None and c.from_room is not None:
                for b in explain_move(
                    disrupted, result.timetable, c.session_id, c.from_slot, c.from_room
                ):
                    print(f"        could not stay: {b.message}")

        # Where else could the most constrained changed session have gone?
        focus = max(diff.changes, key=lambda c: inst.session_by_id[c.session_id].duration)
        ex = explain_session(disrupted, result.timetable, focus.session_id)
        print(f"\n  ALTERNATIVES for {ex.subject_name} ({ex.batch_id}, {ex.duration}h)")
        print(f"    {ex.headline}")
        rules: dict[str, int] = {}
        for opt in ex.options:
            if not opt.feasible and opt.blockers:
                rules[opt.blockers[0].rule] = rules.get(opt.blockers[0].rule, 0) + 1
        for rule, count in sorted(rules.items(), key=lambda kv: -kv[1]):
            print(f"      {rule:<24} blocks {count} start times")

    technical_proof(disrupted, result.timetable, "REPAIRED TIMETABLE")

    changed_ids = {c.session_id for c in diff.changes}
    touched = sorted({c.batch_id for c in diff.changes})
    if touched:
        print("\n  Revision highlighting (* marks a changed session)")
        print(batch_view(disrupted, result.timetable, touched[0], changed=changed_ids))

    # ---- 08 APPROVE & EXPORT ----------------------------------------
    banner("08", "APPROVE & EXPORT")
    save_snapshot(REPAIRED, disrupted, result.timetable, label="repaired-v1")
    print(f"  repaired timetable written to {REPAIRED.relative_to(ROOT)}")

    EXPORTS.mkdir(parents=True, exist_ok=True)
    written = [
        (EXPORTS / "chronosolve-repaired.xlsx",
         to_xlsx(disrupted, result.timetable, diff, label="repaired-v1")),
        (EXPORTS / "chronosolve-repaired.pdf",
         to_pdf(disrupted, result.timetable, diff, label="repaired-v1")),
    ]
    for path, payload in written:
        path.write_bytes(payload)
        print(f"  {path.relative_to(ROOT)}  ({len(payload):,} bytes)")

    ics_path = EXPORTS / "chronosolve-repaired.ics"
    ics = to_ics(disrupted, result.timetable)
    ics_path.write_text(ics, encoding="utf-8")
    print(f"  {ics_path.relative_to(ROOT)}  ({ics.count('BEGIN:VEVENT')} events)")

    print("\n  (the published baseline is untouched until a coordinator approves)")
    print(
        f"\n  ChronoSolve: {diff.unchanged}/{diff.total} sessions preserved, "
        f"{diff.retention_pct:.1f}% retention, "
        f"{validate(disrupted, result.timetable).total} hard conflicts."
    )


if __name__ == "__main__":
    main()
