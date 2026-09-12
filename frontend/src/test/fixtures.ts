import { vi } from 'vitest'
import type {
  Analytics,
  AppState,
  BatchInfo,
  Calendar,
  Cell,
  Constraint,
  Dashboard,
  DemoAccount,
  Entities,
  Explanation,
  FacultyInfo,
  Grid,
  MoveCheck,
  ParsedRule,
  RepairChoice,
  RoomInfo,
  Scenario,
  ScheduleRequest,
  SolveStage,
  Structure,
  SubjectInfo,
  User,
  Version,
  WhatIf,
} from '../api'

export const calendar: Calendar = {
  days: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
  period_start: [
    '09:00',
    '10:00',
    '11:00',
    '12:00',
    '13:00',
    '14:00',
    '15:00',
    '16:00',
  ],
  period_end: [
    '10:00',
    '11:00',
    '12:00',
    '13:00',
    '14:00',
    '15:00',
    '16:00',
    '17:00',
  ],
  lunch_period: 4,
}

export function cell(overrides: Partial<Cell> = {}): Cell {
  return {
    session_id: 'SE-A-CS301-1',
    subject_code: 'CS301',
    subject_name: 'Data Structures & Algorithms',
    batch_id: 'SE-A',
    faculty_id: 'F01',
    faculty_name: 'Prof. Mehta',
    room_id: 'LH108',
    day: 0,
    period: 0,
    duration: 1,
    elective_group: null,
    changed: false,
    moved_time: false,
    moved_room: false,
    from_label: null,
    ...overrides,
  }
}

/** A theory class, a 2-hour lab, and a parallel elective pair sharing a slot. */
export const grid: Grid = {
  view: 'batch',
  entity_id: 'SE-A',
  entity_label: 'SE-A - Second Year - Div A (62 students)',
  calendar,
  cells: [
    cell(),
    cell({
      session_id: 'SE-A-CS351-1',
      subject_code: 'CS351',
      subject_name: 'Data Structures Lab',
      room_id: 'CL3',
      day: 1,
      period: 1,
      duration: 2,
    }),
    cell({
      session_id: 'SE-A-CS401-1',
      subject_code: 'CS401',
      subject_name: 'Cloud Computing',
      room_id: 'LH112',
      day: 2,
      period: 5,
      elective_group: 'SE-A-ELECTIVE-1-1',
    }),
    cell({
      session_id: 'SE-A-CS402-1',
      subject_code: 'CS402',
      subject_name: 'Cyber Security',
      room_id: 'LH114',
      day: 2,
      period: 5,
      elective_group: 'SE-A-ELECTIVE-1-1',
    }),
  ],
  source_label: 'Published · version 1',
  version_number: 1,
}

export const gridWithChanges: Grid = {
  ...grid,
  cells: [
    cell({ changed: true, moved_time: true, from_label: 'Fri 12:00 LH110' }),
    cell({
      session_id: 'SE-A-CS352-1',
      subject_code: 'CS352',
      room_id: 'CL5',
      day: 3,
      period: 2,
      duration: 2,
      changed: true,
      moved_room: true,
      from_label: 'Thu 11:00 CL3',
    }),
  ],
}

// ---------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------

export const adminUser: User = {
  id: 1,
  username: 'admin',
  display_name: 'Academic Coordinator',
  role: 'ADMIN',
  faculty_id: null,
  batch_id: null,
}

export const facultyUser: User = {
  id: 2,
  username: 'mehta',
  display_name: 'Prof. Mehta',
  role: 'FACULTY',
  faculty_id: 'F01',
  batch_id: null,
}

export const studentUser: User = {
  id: 3,
  username: 'student',
  display_name: 'Student',
  role: 'STUDENT',
  faculty_id: null,
  batch_id: 'SE-A',
}

export const demoAccounts: DemoAccount[] = [
  { role: 'ADMIN', username: 'admin', display_name: 'Academic Coordinator' },
  { role: 'FACULTY', username: 'mehta', display_name: 'Prof. Mehta' },
  { role: 'STUDENT', username: 'student', display_name: 'Student' },
]

// ---------------------------------------------------------------------------
// Published state
// ---------------------------------------------------------------------------

export const version: Version = {
  id: 1,
  number: 1,
  label: 'published-v1',
  status: 'PUBLISHED',
  is_current: true,
  created_at: '2026-09-08T18:10:46Z',
  created_by: 'seed',
  reason: 'Rehearsed demo baseline',
  solver_status: 'FEASIBLE',
  objective: 156,
  changed_count: null,
  unchanged_count: null,
  retention_pct: null,
  source_version_id: null,
  request_id: null,
  effective_until: null,
}

export const state: AppState = {
  summary: {
    name: 'Computer Engineering Department (synthetic)',
    sessions: 156,
    contact_hours: 168,
    batches: 6,
    faculty: 16,
    rooms: 20,
    timeslots: 40,
    teaching_slots: 35,
    elective_groups: 18,
    lab_hours: 24,
  },
  calendar,
  published: {
    status: 'FEASIBLE',
    objective: 156,
    best_bound: 60,
    solve_seconds: 20.21,
  },
  published_label: 'published-v1',
  published_saved_at: '2026-09-08T18:10:46Z',
  validation: {
    total_violations: 0,
    clean: true,
    counts: {
      unscheduled_sessions: 0,
      faculty_conflicts: 0,
      room_conflicts: 0,
      batch_conflicts: 0,
      room_type_violations: 0,
      capacity_violations: 0,
      faculty_availability: 0,
      room_availability: 0,
      lab_contiguity: 0,
      lunch_violations: 0,
      max_consecutive_violations: 0,
      daily_load_violations: 0,
      weekly_load_violations: 0,
      elective_parallel_violations: 0,
    },
    samples: [],
  },
  quality: {
    student_idle_hours: 16,
    faculty_idle_hours: 21,
    room_utilisation_pct: 24,
    faculty_load_spread: 3,
    busiest_faculty_hours: 12,
    last_slot_sessions: 7,
  },
  has_pending: false,
  weights: {
    student_gaps: 6,
    faculty_gaps: 2,
    faculty_load_balance: 2,
    subject_spread: 3,
    last_slot: 1,
    room_wastage: 1,
    faculty_preferences: 2,
    faculty_daily_target: 3,
  },
  // GET /api/state reports stored facts rather than a solve, so it carries no
  // stages; only a solve produces them.
  stages: [],
  version,
  proposal: null,
  today: '2026-09-10',
  pending_requests: 1,
  active_overrides: 0,
  locked_sessions: 0,
}

/** Stage timings shaped exactly like a real generation run's. */
export const generateStages: SolveStage[] = [
  {
    key: 'validating',
    label: 'Validating the department data',
    seconds: 0.041,
    detail:
      '156 sessions, 16 faculty, 20 rooms, 168 contact hours over 35 teaching ' +
      'periods - no blocking rule category found',
  },
  {
    key: 'building',
    label: 'Building the CP-SAT model',
    seconds: 0.425,
    detail:
      '15699 boolean variables from 13500 candidate placements (of 124800 raw)',
  },
  {
    key: 'solving',
    label: 'Searching for a conflict-free timetable',
    seconds: 20.21,
    detail:
      'CP-SAT returned FEASIBLE after 20.21s, objective 156 (best bound 60), ' +
      '156/156 sessions placed',
  },
]

/** Stage timings shaped exactly like a real repair run's. */
export const repairStages: SolveStage[] = [
  {
    key: 'validating',
    label: 'Checking what the disruption broke',
    seconds: 0.0,
    detail: '3 of 156 published sessions sit in a slot the disruption blocks',
  },
  {
    key: 'building',
    label: 'Building the CP-SAT model',
    seconds: 0.445,
    detail:
      '15576 boolean variables from 13389 candidate placements (of 124800 raw)',
  },
  {
    key: 'minimising-disruption',
    label: 'Phase 1: minimising moves from the published plan',
    seconds: 1.42,
    detail: 'CP-SAT returned OPTIMAL after 1.42s - fewest possible moves proven',
  },
  {
    key: 'improving-quality',
    label: 'Phase 2: best schedule within that move budget',
    seconds: 3.0,
    detail:
      'CP-SAT returned OPTIMAL after 3.00s - quality improved without extra moves',
  },
  {
    key: 'measuring-retention',
    label: 'Measuring schedule retention',
    seconds: 0.001,
    detail:
      '151 of 156 sessions unchanged = 96.8% retention (5 moved time, 0 moved ' +
      'room only)',
  },
]

export const entities: Entities = {
  batches: [
    { id: 'SE-A', label: 'SE-A', detail: 'Second Year - Div A (62)' },
    { id: 'BE-B', label: 'BE-B', detail: 'Final Year - Div B (60)' },
  ],
  faculty: [{ id: 'F01', label: 'Prof. Mehta', detail: 'F01' }],
  rooms: [{ id: 'CL3', label: 'CL3', detail: 'Computer Lab 3 - 72 seats, LAB' }],
}

export const scenarios: Scenario[] = [
  {
    key: 'faculty',
    story: 'Prof. Mehta is unavailable on Friday afternoon',
    descriptions: ['Prof. Mehta unavailable Fri 12:00-17:00'],
    kind: 'availability',
  },
  {
    key: 'leave',
    story: 'Prof. Mehta is on leave for the whole week',
    descriptions: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
    kind: 'availability',
  },
  {
    key: 'tighter-hours',
    story: 'New policy: nobody teaches more than 2 hours back to back',
    descriptions: ['every teacher: maximum consecutive set to 2'],
    kind: 'rule',
  },
]

export const whatIf: WhatIf = {
  feasible: true,
  status: 'OPTIMAL',
  reason: null,
  diagnosis: null,
  story: 'Prof. Mehta is unavailable on Friday afternoon',
  disruptions: ['Prof. Mehta unavailable Fri 12:00-17:00'],
  directly_affected: [
    {
      session_id: 'BE-B-CS307-1',
      subject_name: 'Machine Learning',
      batch_id: 'BE-B',
      faculty_name: 'Prof. Mehta',
      slot_label: 'Fri 12:00-13:00',
      room_id: 'LH110',
      duration: 1,
    },
  ],
  total: 156,
  unchanged: 151,
  changed: 5,
  time_moves: 5,
  room_only_moves: 0,
  retention_pct: 96.8,
  time_retention_pct: 96.8,
  changes: [
    {
      session_id: 'BE-B-CS307-1',
      subject_code: 'CS307',
      subject_name: 'Machine Learning',
      batch_id: 'BE-B',
      faculty_name: 'Prof. Mehta',
      from_label: 'Fri 12:00',
      to_label: 'Thu 16:00',
      from_room: 'LH110',
      to_room: 'LH108',
      moved_time: true,
      moved_room: true,
      why: [
        {
          rule: 'faculty_unavailable',
          message: 'Prof. Mehta is marked unavailable at Fri 12:00',
        },
      ],
    },
  ],
  validation: state.validation,
  quality: state.quality,
  solver: {
    status: 'OPTIMAL',
    objective: 193,
    best_bound: 193,
    solve_seconds: 4.1,
  },
  phase1_seconds: 1.42,
  phase2_seconds: 3.0,
  total_seconds: 4.42,
  phase1_status: 'OPTIMAL',
  phase2_status: 'OPTIMAL',
  minimal_proven: true,
  stages: repairStages,
}

export const infeasibleWhatIf: WhatIf = {
  ...whatIf,
  feasible: false,
  status: 'INFEASIBLE',
  reason: 'No feasible repair exists under the current constraints.',
  story: 'Prof. Mehta is on leave for the whole week',
  changed: 0,
  changes: [],
  validation: null,
  quality: null,
  solver: null,
  diagnosis: {
    headline:
      '4 rule categories cannot be satisfied. No timetable exists until one of them is relaxed.',
    proven_infeasible: true,
    findings: [
      {
        category: 'Faculty availability',
        severity: 'blocking',
        message:
          'Prof. Mehta must teach 11h but is available for only 0 teaching periods.',
        suggestions: ['free up at least 11 more period(s) for Prof. Mehta'],
      },
      {
        category: 'Division timetable capacity',
        severity: 'tight',
        message: 'SE-A fills 25 of 35 periods.',
        suggestions: ['reduce contact hours'],
      },
    ],
  },
}

export const explanation: Explanation = {
  session_id: 'SE-A-CS351-1',
  subject_code: 'CS351',
  subject_name: 'Data Structures Lab',
  batch_id: 'SE-A',
  faculty_name: 'Prof. Mehta',
  duration: 2,
  current_label: 'Thu 11:00',
  current_room: 'CL3',
  headline:
    '1 of 40 start times can host this session with the rest of the timetable unchanged.',
  feasible_count: 1,
  options: [
    {
      timeslot_id: 0,
      label: 'Mon 09:00',
      feasible: false,
      room_id: null,
      blockers: [
        {
          rule: 'faculty_busy',
          message: 'Prof. Mehta already teaches CS307 then',
        },
      ],
    },
    {
      timeslot_id: 26,
      label: 'Thu 11:00',
      feasible: true,
      room_id: 'CL3',
      blockers: [],
    },
  ],
  locked: false,
}

export const moveCheck: MoveCheck = {
  session_id: 'SE-A-CS351-1',
  target_label: 'Mon 09:00',
  room_id: 'CL3',
  allowed: true,
  hard: [],
  resolvable: [{ rule: 'faculty_busy', message: 'Prof. Mehta already teaches CS307 then' }],
}

export const parsedRule: ParsedRule = {
  text: 'Prof. Mehta is unavailable after 2 PM on Friday',
  understood: true,
  summary: 'Prof. Mehta unavailable on Fri, 14:00-23:59',
  kind: 'FACULTY_UNAVAILABLE',
  target_id: 'F01',
  target_label: 'Prof. Mehta',
  days: [4],
  day_labels: ['Fri'],
  start_time: '14:00',
  end_time: '23:59',
  issues: [],
  assumptions: [],
  disruptions: [
    {
      kind: 'FACULTY_UNAVAILABLE',
      target: 'F01',
      day: 4,
      start_time: '14:00',
      end_time: '23:59',
    },
  ],
  category: 'availability',
  scope: 'BASE',
  start_date: null,
  end_date: null,
  rule_changes: [],
  locks: [],
}

export const unparsedRule: ParsedRule = {
  ...parsedRule,
  understood: false,
  summary: 'Could not build a rule from this sentence.',
  issues: ["No weekday found. Name a day, e.g. 'on Friday'."],
  disruptions: [],
}

export const importAccepted = {
  accepted: true,
  counts: { rooms: 20, faculty: 16, batches: 6, sessions: 156 },
  errors: [],
  warnings: [
    {
      severity: 'warning',
      sheet: 'Faculty',
      row: null,
      message: '2 faculty teach nothing: Prof. Idle, Prof. Spare',
    },
  ],
  state,
}

export const importRejected = {
  accepted: false,
  counts: {},
  errors: [
    {
      severity: 'error',
      sheet: 'Subjects',
      row: 7,
      message: "CS304: unknown batch 'GHOST'",
    },
  ],
  warnings: [],
  state: null,
}

// ---------------------------------------------------------------------------
// Teacher requests
// ---------------------------------------------------------------------------

export const bestOption: RepairChoice = {
  rank: 1,
  label: 'Fewest moves',
  recommended: true,
  moves: [
    {
      session_id: 'BE-B-CS307-1',
      subject_code: 'CS307',
      subject_name: 'Machine Learning',
      batch_id: 'BE-B',
      from_label: 'Fri 12:00',
      from_room: 'LH110',
      to_label: 'Thu 16:00',
      to_room: 'LH108',
      moved_time: true,
    },
  ],
  knock_on: 0,
  changed: 1,
  unchanged: 155,
  total: 156,
  time_moves: 1,
  room_only_moves: 0,
  retention_pct: 99.4,
  time_retention_pct: 99.4,
  hard_violations: 0,
  soft_cost: 190,
  quality: state.quality,
  quality_delta: { student_idle_hours: -1 },
  status: 'OPTIMAL',
  phase1_status: 'OPTIMAL',
  phase2_status: 'OPTIMAL',
  minimal_proven: true,
  solve_seconds: 1.8,
  changes: whatIf.changes,
}

export const secondOption: RepairChoice = {
  ...bestOption,
  rank: 2,
  label: 'Alternative 2',
  recommended: false,
  changed: 2,
  unchanged: 154,
  time_moves: 2,
  knock_on: 1,
  retention_pct: 98.7,
  time_retention_pct: 98.7,
  soft_cost: 184,
  quality_delta: { student_idle_hours: -2 },
  minimal_proven: false,
}

export const draftRequest: ScheduleRequest = {
  id: 7,
  faculty_id: 'F01',
  faculty_name: 'Prof. Mehta',
  status: 'DRAFT',
  reason: 'Examination duty',
  start_date: '2026-09-11',
  end_date: '2026-09-11',
  start_time: '09:00',
  end_time: '17:00',
  window_label: 'Fri 11 Sep 2026, 09:00–17:00',
  created_by: 'mehta',
  created_at: '2026-09-10T08:00:00Z',
  submitted_at: null,
  decided_by: null,
  decided_at: null,
  decision_note: null,
  base_version_id: 1,
  result_version_id: null,
  stale: false,
  affected: whatIf.directly_affected,
  options: [bestOption, secondOption],
  failure: null,
  selected_rank: null,
  auto_selected: false,
}

export const submittedRequest: ScheduleRequest = {
  ...draftRequest,
  status: 'SUBMITTED',
  submitted_at: '2026-09-10T08:05:00Z',
  selected_rank: 1,
  auto_selected: true,
}

// ---------------------------------------------------------------------------
// Coordinator pages
// ---------------------------------------------------------------------------

export const dashboard: Dashboard = {
  today: '2026-09-10',
  version,
  proposal: null,
  solver: { status: 'FEASIBLE', objective: 156, best_bound: 60, solve_seconds: 20.21 },
  violations: 0,
  families: 18,
  quality: state.quality,
  pending_count: 1,
  pending_requests: [submittedRequest],
  overrides: [],
  locked: 0,
  versions: [version],
  structure: { years: 3, divisions: 6, faculty: 16, rooms: 12, labs: 8, sessions: 156 },
}

export const structure: Structure = {
  departments: [
    {
      name: 'Computer Engineering',
      programs: [
        {
          name: 'B.E. Computer Engineering',
          years: [
            {
              number: 2,
              label: 'Second Year',
              semesters: [
                {
                  number: 3,
                  batches: [
                    { id: 'SE-A', name: 'Second Year - Div A', strength: 62, sessions: 26, contact_hours: 28 },
                  ],
                },
              ],
            },
            {
              number: 4,
              label: 'Final Year',
              semesters: [
                {
                  number: 7,
                  batches: [
                    { id: 'BE-B', name: 'Final Year - Div B', strength: 60, sessions: 25, contact_hours: 27 },
                  ],
                },
              ],
            },
          ],
        },
      ],
    },
  ],
  shared_faculty: 9,
  shared_rooms: 14,
  faculty: 16,
  rooms: 20,
  labs: 8,
}

export const batches: BatchInfo[] = [
  {
    id: 'SE-A',
    name: 'Second Year - Div A',
    strength: 62,
    department: 'Computer Engineering',
    program: 'B.E. Computer Engineering',
    year: 2,
    year_label: 'Second Year',
    semester: 3,
    sessions: 26,
    contact_hours: 28,
  },
  {
    id: 'BE-B',
    name: 'Final Year - Div B',
    strength: 60,
    department: 'Computer Engineering',
    program: 'B.E. Computer Engineering',
    year: 4,
    year_label: 'Final Year',
    semester: 7,
    sessions: 25,
    contact_hours: 27,
  },
]

export const facultyList: FacultyInfo[] = [
  {
    id: 'F01',
    name: 'Prof. Mehta',
    department: 'Computer Engineering',
    max_daily_load: 4,
    max_weekly_load: 14,
    max_consecutive: 3,
    unavailable: [],
    preferred_off: [7],
    subjects: ['CS301', 'CS307'],
    weekly_hours: 11,
    busiest_day_hours: 3,
    years_taught: ['Final Year', 'Second Year'],
    session_count: 9,
    username: 'mehta',
  },
]

export const rooms: RoomInfo[] = [
  {
    id: 'CL3',
    name: 'Computer Lab 3',
    building: 'Main',
    room_type: 'LAB',
    capacity: 72,
    active: true,
    capabilities: ['computer'],
    unavailable: [],
    booked_hours: 12,
    utilisation_pct: 34.3,
    avg_fill_pct: 86.1,
  },
]

export const subjects: SubjectInfo[] = [
  {
    id: 1,
    batch_id: 'SE-A',
    code: 'CS301',
    name: 'Data Structures & Algorithms',
    faculty_id: 'F01',
    faculty_name: 'Prof. Mehta',
    category: 'THEORY',
    sessions_per_week: 3,
    duration: 1,
    room_type: 'LECTURE',
    required_capability: null,
    elective_key: null,
    headcount: null,
  },
]

export const temporaryOverride: Constraint = {
  id: 3,
  category: 'TEMPORARY',
  kind: 'FACULTY_UNAVAILABLE',
  target_id: 'F01',
  summary: 'Prof. Mehta unavailable Fri 11 Sep 2026, 09:00–17:00',
  days: [4],
  start_time: '09:00',
  end_time: '17:00',
  start_date: '2026-09-11',
  end_date: '2026-09-11',
  rule_field: null,
  rule_value: null,
  session_id: null,
  lock_timeslot: null,
  lock_room: null,
  status: 'ACTIVE',
  lifecycle: 'UPCOMING',
  reason: 'Examination duty',
  created_by: 'admin',
  created_at: '2026-09-10T08:00:00Z',
  request_id: null,
}

export const lockRecord: Constraint = {
  ...temporaryOverride,
  id: 4,
  category: 'BASE',
  kind: 'LOCK',
  target_id: null,
  summary: 'CS351 Data Structures Lab (SE-A) locked at Thu 11:00 in CL3',
  start_date: null,
  end_date: null,
  session_id: 'SE-A-CS351-1',
  lock_timeslot: 26,
  lock_room: 'CL3',
  lifecycle: 'PERMANENT',
  reason: 'Locked by the coordinator',
}

export const analytics: Analytics = {
  validation: { total: 0, counts: state.validation.counts, families: 14 },
  quality: state.quality,
  solver: dashboard.solver,
  totals: { sessions: 156, scheduled: 156, contact_hours: 168, locked: 0 },
  faculty: [
    {
      faculty_id: 'F01',
      name: 'Prof. Mehta',
      weekly_hours: 11,
      weekly_cap: 14,
      busiest_day_hours: 3,
      daily_cap: 4,
      years_taught: ['Final Year'],
    },
  ],
  rooms: [
    {
      room_id: 'CL3',
      name: 'Computer Lab 3',
      room_type: 'LAB',
      capacity: 72,
      active: true,
      booked_hours: 12,
      utilisation_pct: 34.3,
      avg_fill_pct: 86.1,
    },
  ],
  versions: [
    {
      number: 1,
      label: 'published-v1',
      status: 'PUBLISHED',
      changed: null,
      retention_pct: null,
      created_at: '2026-09-08T18:10:46Z',
    },
  ],
  requests: { SUBMITTED: 1 },
  overrides: {},
}

// ---------------------------------------------------------------------------
// The fake server
// ---------------------------------------------------------------------------

export interface Call {
  url: string
  method: string
  body?: unknown
}

/** A route answers with a payload, a raw Response, or a function of the call. */
type Routes = Record<string, unknown>

/**
 * Encode a payload the way the streaming endpoints do: the stages it reports,
 * then the same JSON body the plain POST would have returned.
 *
 * The stages come from the payload itself, so a fixture and its stream can
 * never disagree about what the solver did.
 */
function sseBody(payload: unknown): string {
  const stages =
    (payload as { stages?: SolveStage[] } | null)?.stages ?? ([] as SolveStage[])
  const frames = stages.flatMap((s) => [
    { event: 'stage_started', key: s.key, label: s.label },
    { event: 'stage_finished', ...s },
  ])
  frames.push({ event: 'result', data: payload } as never)
  return frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')
}

const USERS = { ADMIN: adminUser, FACULTY: facultyUser, STUDENT: studentUser }

/**
 * Stub `fetch` with a tiny router keyed by URL prefix. Returns the calls it saw
 * so tests can assert on what the UI actually requested.
 */
export function mockApi(routes: Routes = {}) {
  const table: Routes = {
    '/api/auth/me': adminUser,
    '/api/auth/demo-accounts': demoAccounts,
    '/api/auth/demo': (call: Call) =>
      USERS[((call.body as { role?: keyof typeof USERS })?.role ?? 'ADMIN')],
    '/api/auth/login': adminUser,
    '/api/auth/logout': { signed_out: true },
    '/api/state': state,
    '/api/entities': entities,
    '/api/scenarios': scenarios,
    '/api/grid': grid,
    '/api/what-if': whatIf,
    '/api/reoptimize': whatIf,
    '/api/discard': state,
    '/api/apply': state,
    '/api/reset': state,
    '/api/generate': { ...state, stages: generateStages },
    '/api/explain': explanation,
    '/api/parse': parsedRule,
    '/api/import': importAccepted,
    '/api/admin/dashboard': dashboard,
    '/api/academic/structure': structure,
    '/api/batches': batches,
    '/api/faculty': facultyList,
    '/api/rooms': rooms,
    '/api/subjects': subjects,
    '/api/constraints': (call: Call) =>
      call.method === 'POST' ? temporaryOverride : [temporaryOverride],
    '/api/locks': (call: Call) => (call.method === 'POST' ? lockRecord : []),
    '/api/moves/check': moveCheck,
    '/api/moves/preview': whatIf,
    '/api/versions': [version],
    '/api/analytics': analytics,
    '/api/settings/weights': state.weights,
    '/api/requests': (call: Call) => (call.method === 'POST' ? draftRequest : [submittedRequest]),
    '/api/requests/7': submittedRequest,
    '/api/requests/7/submit': submittedRequest,
    '/api/requests/7/withdraw': { ...draftRequest, status: 'WITHDRAWN' },
    '/api/requests/7/review': whatIf,
    '/api/requests/7/approve': {
      request: { ...submittedRequest, status: 'APPROVED' },
      version: { ...version, id: 2, number: 2, label: 'published-request-7' },
    },
    '/api/requests/7/reject': { ...submittedRequest, status: 'REJECTED' },
    ...routes,
  }
  const calls: Call[] = []

  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    // Only JSON bodies can be parsed; an upload sends FormData, and parsing
    // that would throw before the call was ever recorded.
    let body: unknown
    if (typeof init?.body === 'string') {
      body = JSON.parse(init.body)
    } else if (init?.body instanceof FormData) {
      body = Object.fromEntries(
        [...init.body.entries()].map(([k, v]) => [
          k,
          v instanceof File ? { name: v.name, size: v.size } : v,
        ]),
      )
    }
    const call: Call = { url, method: init?.method ?? 'GET', body }
    calls.push(call)

    // A streaming route answers for the same resource as its plain sibling, so
    // resolve it against the base path -- otherwise a test that overrides
    // '/api/what-if' would not reach '/api/what-if/stream'.
    const streaming = url.endsWith('/stream')
    const lookup = streaming ? url.slice(0, -'/stream'.length) : url

    const key = Object.keys(table)
      .sort((a, b) => b.length - a.length)
      .find((k) => lookup.startsWith(k))

    if (!key) {
      return new Response(JSON.stringify({ detail: `no route for ${url}` }), {
        status: 404,
      })
    }
    // A route may supply a raw Response to simulate a failure; pass it through
    // rather than serialising the Response object itself.
    const route = table[key]
    const payload = typeof route === 'function' ? (route as (c: Call) => unknown)(call) : route
    if (payload instanceof Response) return payload.clone()
    if (streaming) {
      return new Response(sseBody(payload), {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    return new Response(JSON.stringify(payload), { status: 200 })
  })

  vi.stubGlobal('fetch', fetchMock)
  return { calls, fetchMock }
}

/** A refusal shaped the way FastAPI sends one. */
export function refusal(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), { status })
}
