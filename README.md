# ChronoSolve

**Adaptive Constraint-Optimized Academic Scheduling**
*Generate Once. Adapt Intelligently. Disrupt Minimally.*

Campusathon 2026 — PS1 (Smart Timetable Generator) — Team **4L's** (`su-cam-063`)

---

## What this is

Most timetable systems stop at generation. ChronoSolve solves the harder second
problem: when an **already-published** timetable breaks — a faculty member goes
on leave, a lab closes — it computes the **smallest feasible repair**, proves the
result is still conflict-free, shows exactly what changed, and measures how much
of the published schedule survived.

Schedule stability is not a post-processing step here. It is part of the
optimization objective.

## Who uses it

One application, three roles, one solver underneath.

| role | sees | can do |
|---|---|---|
| **Coordinator** (`ADMIN`) | the whole institution | edit the configuration and rules, generate, re-optimise, lock, move, decide requests, publish versions |
| **Faculty** | their own week and requests | report unavailability, compare ranked repairs, submit one for approval |
| **Student** | one division's published timetable | view and export |

A teacher never rewrites the institution's timetable. Their request carries a
repair the solver computed; only a coordinator's approval publishes it.

Demo sign-in is one click per role, and `CHRONOSOLVE_DEMO_LOGIN=0` turns it off.
The seeded accounts are:

| username | password | role |
|---|---|---|
| `admin` | `admin123` | coordinator |
| each teacher's surname, e.g. `mehta` | `faculty123` | faculty |
| `student` | `student123` | student |

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

No database server is needed to try it. Without `DATABASE_URL` the app uses
SQLite at `data/chronosolve.db`, runs its migrations on first start and seeds
the rehearsed institution and its demo accounts. Point `DATABASE_URL` at
PostgreSQL and exactly the same migrations run there instead — see
[Deploy](#deploy).

## Run the web app

Two terminals. Backend:

```bash
.venv/Scripts/python.exe -m uvicorn backend.app.api:app --port 8000
```

Frontend:

```bash
cd frontend && npm install && npm run dev
```

Then open http://localhost:5173 and sign in — one click as coordinator, faculty
or student, or with the usernames above. Vite proxies `/api` to the backend, so
the session cookie is same-origin and the UI works identically in dev and behind
a single deployed origin. Interactive API docs are at
http://localhost:8000/docs.

## Run it as one service

The API also serves the built UI, so production is a single process on a single
port — no CORS, no second server, no proxy:

```bash
cd frontend && npm run build && cd ..
.venv/Scripts/python.exe -m uvicorn backend.app.api:app --port 8000
```

Then open http://localhost:8000. `frontend/dist` is mounted last so it can never
shadow an `/api` route; when the bundle is absent the API simply runs alone.

## Deploy

```bash
docker build -t chronosolve .
docker run -p 8000:8000 chronosolve
```

`render.yaml` describes the same container as one free web service plus one free
PostgreSQL database, with `/api/health` as its health check. Any host that runs a
Dockerfile will do. The app migrates itself on start, so a deploy needs no
release command; if `DATABASE_URL` is absent it falls back to SQLite inside the
container, which a free instance loses on restart.

| variable | default | purpose |
|---|---|---|
| `DATABASE_URL` / `CHRONOSOLVE_DATABASE_URL` | SQLite in `data/` | PostgreSQL connection (`postgres://` is normalised) |
| `CHRONOSOLVE_SECURE_COOKIES` | `0` | mark the session cookie Secure — set to `1` behind HTTPS |
| `CHRONOSOLVE_DEMO_LOGIN` | `1` | one-click demo sign-in; `0` requires passwords everywhere |
| `CHRONOSOLVE_PBKDF2_ITERATIONS` | `240000` | password hashing cost |
| `CHRONOSOLVE_TODAY` | the real date | pin "today" for a rehearsed demo |
| `CHRONOSOLVE_WORKERS` | 8 (2 in Docker) | CP-SAT search workers |
| `CHRONOSOLVE_GENERATE_SECONDS` | 20 | ceiling on a full generate |
| `CHRONOSOLVE_REPAIR_SECONDS` | 10 (15 in Docker) | ceiling on each repair phase |

Requests cannot exceed the solver ceilings, so a hosted instance cannot be made
to solve for longer than its plan allows.

To run against PostgreSQL locally:

```bash
export DATABASE_URL=postgresql+psycopg://chronosolve:secret@localhost:5432/chronosolve
.venv/Scripts/python.exe -m uvicorn backend.app.api:app --port 8000
```

Nothing else changes: the solver never sees SQL. Migrations live in
`backend/migrations`, and the repository layer converts rows to domain objects
before anything reaches CP-SAT.

## Run the console demo

Everything the UI does is also available headless — the local fallback if
anything goes wrong with a browser during a live demo:

```bash
.venv/Scripts/python.exe -m backend.demo
```

Scenarios (`--scenario`):

| name | disruption |
|---|---|
| `faculty` *(default)* | Prof. Mehta unavailable Friday afternoon |
| `wednesday` | Prof. Mehta unavailable Wednesday afternoon |
| `lab` | Computer Lab 3 closed for maintenance on Friday |
| `seminar` | both second-year divisions away at an industry seminar |
| `tighter-hours` | **policy change**: nobody teaches more than 2 hours back to back |
| `labs-closed` | all six computer labs closed Monday and Tuesday |
| `leave` | Prof. Mehta on leave all week — **deliberately impossible** |

`--regenerate` solves a fresh baseline and republishes it. Without it the demo
uses the timetable already published in `data/published_baseline.json`, which is
what a real coordinator would have.

To work from a spreadsheet instead:

```bash
.venv/Scripts/python.exe -m backend.demo --template dept.xlsx   # write the format
.venv/Scripts/python.exe -m backend.demo --import dept.xlsx     # load it back
```

`--import` replaces the department and republishes, so it overwrites
`data/published_baseline.json` — keep a copy if you are rehearsing against a
specific schedule.

## Tests

```bash
.venv/Scripts/python.exe -m pytest    # backend: 278 tests
cd frontend && npm test               # frontend: 97 tests
```

The backend suite covers the solver, the feature layer and the HTTP API. Its
tests solve real CP-SAT models rather than mocking them, against a small
2-division fixture that still exercises multi-hour labs, parallel electives,
capacity limits and faculty unavailability.

The important half is the **mutation tests**. A validator that never fires would
report "0 conflicts" on a broken schedule just as happily as on a sound one, so
each hard-constraint family has a test that deliberately corrupts a valid
timetable — double-books a room, drops a class onto lunch, splits a parallel
elective, runs a 2-hour lab off the end of the day — and asserts the validator
catches exactly that. The zero-conflict claim rests on these.

The suite is checked for sensitivity, not just greenness: disabling lunch
protection in `Calendar.span` fails 6 tests, including
`test_nothing_is_scheduled_over_lunch` and
`test_repair_output_is_still_conflict_free`.

API tests run against a migrated throwaway database, copied from a template and
seeded from the same rehearsed fixture for each test, so the suite can generate,
approve, publish and restore freely without ever touching the demo data on disk.

The frontend suite (Vitest + Testing Library) drives the real components against
a stubbed `fetch`, so it needs no backend. It asserts the behaviour that carries
the demo: each role sees only its own menu and is sent home from another role's
page, what-if never publishes, generation produces a proposal rather than a
published version, a parsed sentence is previewed before it is solved, an
unreadable sentence is refused rather than guessed at, an infeasible result shows
its diagnosis, a manual move is checked before it is re-optimised, a teacher can
auto-select the best repair but never publish it, a coordinator's approval does
publish one, and export links follow the published/proposed toggle. Two tests are
named regressions for bugs found during the build — the proposed-repair toggle
being disabled after a solve, and a failed request with an empty status text
leaving the UI stuck on its loading state.

## Measured results

On the seeded 156-session department (6 divisions, 16 faculty, 20 rooms,
40 timeslots), verified by an independent checker that shares no code with the
solver model:

| scenario | directly affected | changed | retention | hard conflicts | repair time |
|---|---:|---:|---:|---:|---:|
| faculty | 3 | 5 | 96.8% | 0 | 4.6–5.8s |
| wednesday | 2 | 3 | 98.1% | 0 | ~3.9s |
| lab | 2 | 2 *(room only)* | 98.7% | 0 | ~3.7s |
| seminar | 5 | 5 | 96.8% | 0 | ~4.1s |
| tighter-hours | 30 | 17 | 89.1% | 0 | ~7.4s |
| labs-closed | 4 | 6 | 96.2% | 0 | ~5s |
| leave | 10 | — | *infeasible, diagnosed* | — | ~2s |

Retention and change counts are stable across runs; wall-clock repair time
varies by a second or so with machine load.

The two repair phases certify different things, and conflating them would be an
overclaim. **Phase 1** minimises the number of moves — when it returns `OPTIMAL`,
that change count is *provably* the smallest possible, and the UI says "fewest
moves proven". **Phase 2** only improves comfort within that budget; its status
concerns schedule quality and says nothing about minimality. Both are reported
separately. Across all six repairable scenarios phase 1 returns `OPTIMAL`, while
phase 2 sometimes exhausts its budget at `FEASIBLE`.

Every number is produced by the solver at run time; none is hard-coded.

## How it works

**Model.** One Boolean `x[session, start_timeslot, room]` per surviving
candidate. Rules that depend on a single placement (room type, capacity, faculty
and room availability, lunch protection, lab contiguity, day boundaries) are
applied as *candidate filters*, so illegal placements never become variables —
124,800 raw combinations reduce to ~13,500. Rules that couple placements
(clashes, consecutive hours, daily/weekly load, elective parallelism) become
constraints.

**Hard constraints.** No faculty / room / batch double-booking, room capacity,
room and lab type, faculty, room *and division* unavailability, weekly contact hours,
protected lunch, contiguous multi-hour labs, maximum consecutive teaching hours,
daily and weekly load limits, parallel elective requirements.

**Soft objectives.** Student idle periods, faculty idle windows, daily load
balance, subject spread, last-period penalty — weighted and tunable.

**Repair** is strictly lexicographic over one model:

1. **Phase 1** minimises disruption — fewest sessions moved to a new *time*,
   then fewest moved to a new *room*. Warm-started from the published schedule.
2. **Phase 2** maximises schedule quality *without* exceeding phase 1's
   disruption, so the repair is minimal **and** comfortable.

A time move is treated as strictly worse than a room move: students keep their
published week even when the room changes. That is why the lab-closure scenario
resolves with zero time changes.

**Rule changes, not just availability.** A what-if can tighten an institutional
rule — `max_consecutive`, `max_daily_load`, `max_weekly_load`, for one teacher or
for everyone. Nothing becomes unavailable; the published timetable simply stops
being legal, and repair has to make it legal again with the fewest moves. This
needs its own impact analysis, because a stricter consecutive-hours policy
breaks no single placement — it makes a *run* of them illegal, which
`sessions_breaking_load_rules` reports. Tightening the department to two hours
back to back implicates 30 sessions and is absorbed by moving 17, at 89.1%
retention with zero conflicts.

**Three kinds of disruption.** A teacher is away, a room is closed, or a
*division* is away — an assembly, an industry seminar, an exam. The third is not
expressible as the other two: blocking a division frees nobody else, and the
rooms and teachers involved stay available to everyone. `Batch.unavailable`
therefore runs the full width of the system — candidate filtering, an
independent `batch_availability` check, a `batch_unavailable` explanation, a
Division-availability diagnosis, the Unavailability sheet, and the language
parser ("SE-A is at a seminar on Wednesday afternoon").

**Verification.** `solver/validate.py` recounts all eighteen hard-constraint
families directly from the placements, sharing no logic with the CP-SAT builder.
If the model were wrong, this is what would catch it.

**Explanations.** `solver/explain.py` answers the two questions a coordinator
asks. *Why did this move?* — every proposed change carries the rule that forced
it (`Prof. Mehta is marked unavailable at Fri 12:00`, `TE-B already has CS351
then`, `this elective must run at the same time as Computer Vision`). *Why can't
it go there?* — clicking any session evaluates all 40 start times against the
rest of the schedule held fixed and reports what blocks each one. In the worked
example the 2-hour Data Structures Lab has **exactly 1 of 40** legal start times.

Explanations are evaluated from the same structured constraint data that builds
the solver model. They are **not** a solver unsatisfiable core and are not
claimed to be a minimal conflicting set — the UI says so on screen.

**Infeasibility diagnosis.** `solver/diagnose.py` runs counting checks over the
rule categories — faculty time budget, weekly and daily load, division capacity,
room supply by type, parallel-elective room and timeslot supply, and sessions
with no legal placement at all. Findings are ordered root-cause first, so the
`leave` scenario leads with *“Prof. Mehta must teach 11h but is available for
only 0 teaching periods”* rather than the ten downstream symptoms, and each
carries concrete relaxations.

Each **blocking** finding is a necessary condition that fails, so it *proves* no
timetable exists. Passing every check does **not** prove one does — rules can
still conflict in combination — and the report says so rather than implying
completeness.

The same distinction governs how a failed repair is reported. `INFEASIBLE` is a
proof from CP-SAT and is stated as one. `UNKNOWN` means only that the search
budget ran out: the UI says *“no repair found in the time available”*, names the
budget it exhausted, and states outright that this does not prove none exists.
Conflating the two would claim a result the solver never established, so tests
pin both directions.

**Spreadsheet import.** `data/excel.py` reads a workbook of five sheets —
Rooms, Faculty, Batches, Subjects and an optional Unavailability (`kind` may be
`faculty`, `room` or `batch`) — and expands
weekly *subject requirements* into individual sessions, because a coordinator
thinks in "three one-hour DSA classes a week", not in 156 session rows.

Nothing is applied unless the entire workbook validates; a half-imported
department is worse than a rejected one. Every problem is reported together with
its sheet and row: unknown batch or faculty references, duplicate ids, bad room
types, capacities that seat nobody, a teacher assigned beyond their weekly cap, a
division needing more periods than the week has. Warnings (an idle teacher, a
teacher with no slack) are surfaced without blocking. Times are accepted as
`14:00`, `2 PM` or Excel time cells.

`GET /api/import/template` returns the *current* department in that same format,
so the file a coordinator is handed is by construction the file the importer
accepts — download, edit, re-upload.

**Natural-language entry.** `app/nl.py` turns a coordinator's sentence into
*candidate* structured rules:

```
"Prof. Mehta is unavailable after 2 PM on Friday"
  -> {"kind": "FACULTY_UNAVAILABLE", "target": "F01",
      "day": 4, "start_time": "14:00", "end_time": "23:59"}
```

It handles named windows (`morning`, `afternoon`, `all day`), ranges
(`from 11:00 to 14:00`, `10:00-12:00`), open bounds (`after 2 PM`, `before 11`),
multiple days in one sentence (`Monday and Tuesday` yields two rules), faculty by
full name or surname, and rooms by id, name or common alias (`CL3`,
`Computer Lab 3`, `lab 3`).

It reads three kinds of sentence, and which kind it is decides what gets
recorded:

| sentence | becomes |
|---|---|
| `Prof. Mehta is unavailable after 2 PM on Friday` | a permanent availability rule |
| `Computer Lab 2 is unavailable tomorrow` | a **dated override** that expires on its own |
| `TE-A should not have lectures after 4 PM` | a standing division policy, every weekday |
| `Maximum three consecutive lectures for faculty` | a workload rule (`max_consecutive = 3`) |
| `Lock DBMS for TE-B on Monday at 10 AM` | a lock pinning that session, matched by code, name or short form |

A date (`tomorrow`, `all next week`, `15 September`) is what makes a rule
temporary, so it carries its own expiry instead of being a permanent rule
somebody must remember to remove. What it refuses matters as much: a weekend
date, a limit outside 1–40 hours, a lock with no start time or one landing on
lunch, and an absence with no day at all.

It is a regex-and-lexicon parser, **not** a language model: it runs offline,
costs nothing, and fails predictably. When it cannot read a sentence it says
which part was missing rather than guessing. Every inference it does make — such
as reading a bare `2` as 14:00 — is reported next to the rule. The parsed rule is
shown for confirmation and is never applied on its own, and CP-SAT remains the
only thing that produces a timetable.

**Export.** Excel (summary, a grid per division, all sessions, changes), PDF
(one landscape page per division plus a verification page carrying the solver
status and every constraint count), and RFC 5545 calendar with weekly
recurrence — for the whole department or for one batch, faculty member or room.
Exports work on the published timetable *or* on a proposed repair, so a
coordinator can circulate a draft before approving it.

## Layout

```
backend/
  demo.py                  end-to-end console workflow
  migrations/              Alembic revisions (0001 creates all 21 tables)
  app/
    api.py                 shared routes: state, grid, what-if, exports, import
    deps.py                database handle, current user, role guards, SSE
    schemas.py             request/response models and their converters
    routes/auth.py         sign in, sign out, demo accounts
    routes/requests.py     the teacher unavailability workflow
    routes/admin.py        configuration, rules, locks, moves, versions
    domain/models.py       entities, weekly calendar, placements, locks
    db/models.py           SQLAlchemy tables
    db/repository.py       rows <-> domain objects; versions, users, requests
    db/session.py          engine, migrations, SQLite fallback
    db/seed.py             rehearsed baseline and demo accounts
    services/scheduling.py the operations the routes call
    services/constraints.py base rules, dated overrides, lifecycle
    services/auth.py       password hashing, sessions, roles
    data/synthetic.py      seeded synthetic institution (no real data)
    data/excel.py          spreadsheet import and template export
    data/store.py          JSON snapshots (fixtures, console demo)
    export.py              Excel / PDF / calendar output
    nl.py                  natural-language constraint parser
    solver/
      model.py             CP-SAT variables, hard constraints, soft objectives
      engine.py            solve + extract; initial generation
      repair.py            minimum-disruption re-optimization  <- core innovation
      options.py           ranked repair alternatives for a request
      moves.py             validate a manual move, then repair around it
      locks.py             locks as hard constraints
      disruption.py        faculty/room/division disruptions, scenarios
      explain.py           constraint-aware explanations
      diagnose.py          infeasibility diagnosis and relaxations
      metrics.py           retention, old-vs-new diff, quality, workload
      validate.py          independent hard-constraint verification
frontend/
  src/api.ts               typed API client
  src/App.tsx              sign-in, role-based shell, routing
  src/workspace.tsx        shared context: user, state, progress, notices
  src/ui.tsx               buttons, cards, badges, modals, icons
  src/Grid.tsx             timetable grid with revision highlighting
  src/Panels.tsx           repair hero, explanation and technical proof panels
  src/pages/admin*.tsx     coordinator pages
  src/pages/faculty.tsx    my timetable, report unavailability, my requests
  src/pages/student.tsx    a division's published timetable
data/
  fixtures/rehearsed_v1.json  the baseline the database is seeded from
  published_baseline.json     the console demo's snapshot
```

## API

Sessions are HttpOnly cookies, so downloads and event streams carry them without
the page ever handling a token; a Bearer header is accepted for API clients.

**Accounts**

| method | path | purpose |
|---|---|---|
| POST | `/api/auth/login` | sign in with a username and password |
| GET | `/api/auth/demo-accounts` | the one-click accounts, when demo sign-in is on |
| POST | `/api/auth/demo` | sign in as a demo role |
| POST | `/api/auth/logout` | end the session |
| GET | `/api/auth/me` | who is signed in |

**Anyone signed in**

| method | path | purpose |
|---|---|---|
| GET | `/api/state` | published version, solver status, validation, quality, counts |
| GET | `/api/entities` | divisions / faculty / rooms for the selectors |
| GET | `/api/academic/structure` | department → programme → year → semester → division |
| GET | `/api/batches` | divisions with year, semester and load |
| GET | `/api/grid?view&id&source&version_id&request_id&rank` | any timetable, one view |
| GET | `/api/export/{xlsx,pdf,ics}?source&version_id` | workbook, printable timetable, calendar |

**Coordinator or faculty**

| method | path | purpose |
|---|---|---|
| GET | `/api/explain?session_id&source` | which rules block each alternative slot |
| GET | `/api/faculty`, `/api/rooms`, `/api/subjects` | the configuration, read-only |
| POST | `/api/parse` | read a sentence into a candidate rule — **proposes only** |
| POST | `/api/requests` (+`/stream`) | report unavailability, ranked repairs computed |
| GET | `/api/requests`, `/api/requests/{id}` | own requests; a coordinator sees all |
| POST | `/api/requests/{id}/recompute` (+`/stream`) | re-rank against today's timetable |
| POST | `/api/requests/{id}/submit` | choose an option, or auto-select the best |
| POST | `/api/requests/{id}/withdraw` | take it back |
| GET | `/api/requests/{id}/review` | the chosen option as a full what-if |

**Coordinator only**

| method | path | purpose |
|---|---|---|
| GET | `/api/admin/dashboard` | version, pending work, overrides, quality, structure |
| PATCH | `/api/faculty/{id}`, `/api/rooms/{id}`, `/api/subjects/{id}` | edit the configuration |
| PUT | `/api/faculty/{id}/availability`, `/api/rooms/{id}/blocks` | regular availability |
| POST | `/api/batches`, `/api/subjects` | add a division or a subject |
| GET POST PATCH DELETE | `/api/constraints` | base rules and dated overrides |
| POST | `/api/constraints/{id}/restore-preview` | after an override ends, preview going back |
| GET POST DELETE | `/api/locks` | pin a session, or release it |
| POST | `/api/moves/check` | which rules block a manual move — before solving |
| POST | `/api/moves/preview` (+`/stream`) | lock the move, repair around it |
| GET | `/api/scenarios` | rehearsed disruption scenarios |
| POST | `/api/what-if` (+`/stream`) | preview a disruption — **never mutates published** |
| POST | `/api/reoptimize` (+`/stream`) | repair the published timetable under today's rules |
| POST | `/api/requests/{id}/approve` | publish the teacher's chosen repair |
| POST | `/api/requests/{id}/reject` | decline it, with a note |
| POST | `/api/apply` / `/api/discard` | publish the proposal, or drop it |
| POST | `/api/generate` (+`/stream`) | solve from scratch (`publish: false` keeps it a proposal) |
| GET | `/api/versions` | every version, with who made it and why |
| GET | `/api/analytics` | workload, utilisation, retention history, request counts |
| GET PUT | `/api/settings/weights` | the objective weights |
| GET | `/api/import/template` | the current configuration as an editable workbook |
| POST | `/api/import` | replace the configuration from a workbook, then solve |
| POST | `/api/admin/restore-rehearsed` | republish the rehearsed baseline as a new version |

The `published` / `proposed` split is the product model: what-if, a manual move,
a re-optimisation and a from-scratch generate all compute a full timetable and
hold it as a version for review. Only `apply` — or a coordinator approving a
teacher's request — publishes one, and every earlier version stays browsable and
exportable.

### Solve progress

A solve takes tens of seconds, so both flows report their stages. The streaming
routes are server-sent events carrying `stage_started` / `stage_finished` frames
and ending with the identical payload the plain POST returns; the plain routes
return the same stage list in a `stages` field once finished. They call the same
functions, so the two can never disagree.

Generation reports **validating → building the CP-SAT model → solving**
(→ publishing). Repair reports **checking what the disruption broke → building
the model → phase 1, minimising moves → phase 2, best quality within that
budget → measuring retention**.

Every stage carries the duration it actually took and the fact it established —
model size, solver status, the retention it measured. **No stage reports a
percentage of completion.** CP-SAT cannot say how much search remains, so a
progress bar would have to invent its own position; what can be stated
truthfully is which step is running and how long each finished step ran.

## Honest limitations

- Initial generation returns `FEASIBLE`, not `OPTIMAL`, within its time limit.
  The reported objective and best bound are shown as-is; a feasible solution
  satisfies every hard constraint but is not proven optimal for the soft ones.
- Multi-worker CP-SAT is not bit-reproducible across runs, which is why the
  published baseline is persisted rather than regenerated. Repair against a
  fixed baseline is stable.
- Data is synthetic. No claim of validation against a specific institution.
- Everything is stored in SQLite or PostgreSQL behind a repository layer, and
  the solver never sees SQL. Render's free PostgreSQL expires after 30 days;
  after that an instance falls back to SQLite inside the container, which it
  loses on restart, until a new database is attached.
- Infeasibility diagnosis proves impossibility when a check fails, but a clean
  report is not a proof of feasibility (see above).
- The natural-language parser covers availability, dated overrides, workload
  rules and locks. Anything else is entered through the constraint form.
- Import replaces the whole configuration rather than merging into it, and it
  publishes what it solves instead of holding it for review.
- A teacher proposes a window, not a slot: they choose among the solver's ranked
  repairs rather than nominating a specific replacement time.
- The manual-move dialog offers start times and lets the solver pick the room;
  naming the room is possible through `POST /api/moves/preview` only.
- A student sees their division's whole timetable, including every parallel
  elective, rather than only the electives they personally take.
- Analytics are computed from the published version on each request. Beyond the
  version list there is no historical series.
- A what-if covers availability and workload rules. Other hard rules (lunch,
  contiguity, capacity) are fixed by the model rather than tunable per run.
- Repair re-solves the whole model warm-started from the published schedule
  rather than restricting itself to a neighbourhood of the disruption. That is
  what lets it report `OPTIMAL`, but it is not a partitioned solve.

## Cost

₹0. No paid API, hosting, dataset or licence is required to run any of this.
