// Typed client for the ChronoSolve API. Shapes mirror the pydantic models in
// backend/app/schemas.py. The session is an HttpOnly cookie, so no request
// here ever handles a token.

export type Role = 'ADMIN' | 'FACULTY' | 'STUDENT'

export interface User {
  id: number
  username: string
  display_name: string
  role: Role
  faculty_id: string | null
  batch_id: string | null
}

export interface DemoAccount {
  role: Role
  username: string
  display_name: string
}

export interface Calendar {
  days: string[]
  period_start: string[]
  period_end: string[]
  lunch_period: number
}

export interface Summary {
  name: string
  sessions: number
  contact_hours: number
  batches: number
  faculty: number
  rooms: number
  timeslots: number
  teaching_slots: number
  elective_groups: number
  lab_hours: number
  years?: number
}

export interface Solver {
  status: string
  objective: number | null
  best_bound: number | null
  solve_seconds: number
}

export interface Validation {
  total_violations: number
  clean: boolean
  counts: Record<string, number>
  samples: string[]
}

export interface Quality {
  student_idle_hours: number
  faculty_idle_hours: number
  room_utilisation_pct: number
  faculty_load_spread: number
  busiest_faculty_hours: number
  last_slot_sessions: number
  wasted_seats?: number
  seat_efficiency_pct?: number
  oversized_sessions?: number
  preference_hits?: number
}

export interface Weights {
  student_gaps: number
  faculty_gaps: number
  faculty_load_balance: number
  subject_spread: number
  last_slot: number
  room_wastage?: number
  faculty_preferences?: number
  faculty_daily_target: number
}

export interface Version {
  id: number
  number: number
  label: string
  status: string
  is_current: boolean
  created_at: string | null
  created_by: string
  reason: string
  solver_status: string
  objective: number | null
  changed_count: number | null
  unchanged_count: number | null
  retention_pct: number | null
  source_version_id: number | null
  request_id: number | null
  effective_until: string | null
}

/**
 * One finished step of a solve, with the time it actually took.
 *
 * There is deliberately no percentage anywhere in this type: CP-SAT cannot say
 * how much search is left, so a completion figure would be invented.
 */
export interface SolveStage {
  key: string
  label: string
  seconds: number
  detail: string
}

/** What the UI knows about a solve while it is still running. */
export interface SolveProgress {
  /** The step currently executing, or null once the solve has finished. */
  running: { key: string; label: string } | null
  /** Steps that have completed, in order, each with its measured duration. */
  done: SolveStage[]
}

export interface AppState {
  summary: Summary
  calendar: Calendar
  published: Solver
  published_label: string | null
  published_saved_at: string | null
  validation: Validation
  quality: Quality
  has_pending: boolean
  weights: Weights
  stages: SolveStage[]
  version?: Version | null
  proposal?: Version | null
  today?: string
  pending_requests?: number
  active_overrides?: number
  locked_sessions?: number
}

export interface Entity {
  id: string
  label: string
  detail: string
}

export interface Entities {
  batches: Entity[]
  faculty: Entity[]
  rooms: Entity[]
}

export type ViewKind = 'batch' | 'faculty' | 'room'
export type Source = 'published' | 'pending'

export interface Cell {
  session_id: string
  subject_code: string
  subject_name: string
  batch_id: string
  faculty_id: string
  faculty_name: string
  room_id: string
  day: number
  period: number
  duration: number
  elective_group: string | null
  category?: string
  changed: boolean
  moved_time: boolean
  moved_room: boolean
  from_label: string | null
  locked?: boolean
}

export interface Grid {
  view: ViewKind
  entity_id: string
  entity_label: string
  calendar: Calendar
  cells: Cell[]
  source_label?: string
  version_number?: number | null
}

export interface Scenario {
  key: string
  story: string
  descriptions: string[]
  /** availability | rule | mixed — a rule change alters policy, not who is free. */
  kind: string
}

export interface Affected {
  session_id: string
  subject_name: string
  batch_id: string
  faculty_name: string
  slot_label: string
  room_id: string
  duration: number
  subject_code?: string
}

export interface Blocker {
  rule: string
  message: string
}

export interface Change {
  session_id: string
  subject_code: string
  subject_name: string
  batch_id: string
  faculty_name: string
  from_label: string
  to_label: string
  from_room: string | null
  to_room: string
  moved_time: boolean
  moved_room: boolean
  why: Blocker[]
}

export interface SlotOption {
  timeslot_id: number
  label: string
  feasible: boolean
  room_id: string | null
  blockers: Blocker[]
}

export interface Explanation {
  session_id: string
  subject_code: string
  subject_name: string
  batch_id: string
  faculty_name: string
  duration: number
  current_label: string | null
  current_room: string | null
  headline: string
  feasible_count: number
  options: SlotOption[]
  locked?: boolean
}

export interface Finding {
  category: string
  severity: string
  message: string
  suggestions: string[]
}

export interface Diagnosis {
  headline: string
  proven_infeasible: boolean
  findings: Finding[]
}

export interface WhatIf {
  feasible: boolean
  status: string
  reason: string | null
  diagnosis: Diagnosis | null
  story: string
  disruptions: string[]
  directly_affected: Affected[]
  total: number
  unchanged: number
  changed: number
  time_moves: number
  room_only_moves: number
  retention_pct: number
  time_retention_pct: number
  changes: Change[]
  validation: Validation | null
  quality: Quality | null
  solver: Solver | null
  phase1_seconds: number
  phase2_seconds: number
  total_seconds: number
  phase1_status: string
  phase2_status: string
  /** Phase 1 proved no smaller repair exists. Phase 2 only tunes quality. */
  minimal_proven: boolean
  stages: SolveStage[]
  proposal?: Version | null
}

export interface ImportIssue {
  severity: string
  sheet: string
  row: number | null
  message: string
}

export interface ImportResult {
  accepted: boolean
  counts: Record<string, number>
  errors: ImportIssue[]
  warnings: ImportIssue[]
  state: AppState | null
}

export interface RuleChangeInput {
  rule: 'max_consecutive' | 'max_daily_load' | 'max_weekly_load'
  value: number
  faculty?: string | null
}

export interface DisruptionInput {
  kind: 'FACULTY_UNAVAILABLE' | 'ROOM_UNAVAILABLE' | 'BATCH_UNAVAILABLE'
  target: string
  day: number
  start_time: string
  end_time: string
}

export interface ParsedRule {
  text: string
  understood: boolean
  summary: string
  kind: string | null
  target_id: string | null
  target_label: string | null
  days: number[]
  day_labels: string[]
  start_time: string
  end_time: string
  issues: string[]
  assumptions: string[]
  disruptions: DisruptionInput[]
  category?: 'availability' | 'rule' | 'lock'
  scope?: 'BASE' | 'TEMPORARY'
  start_date?: string | null
  end_date?: string | null
  rule_changes?: { rule: string; value: number; faculty: string | null; faculty_label: string | null }[]
  locks?: {
    session_id: string
    subject_code: string
    batch_id: string
    timeslot_id: number
    label: string
    room_id: string | null
  }[]
}

export interface Constraint {
  id: number
  category: 'BASE' | 'TEMPORARY'
  kind: string
  target_id: string | null
  summary: string
  days: number[]
  start_time: string
  end_time: string
  start_date: string | null
  end_date: string | null
  rule_field: string | null
  rule_value: number | null
  session_id: string | null
  lock_timeslot: number | null
  lock_room: string | null
  status: string
  lifecycle: string
  reason: string
  created_by: string
  created_at: string | null
  request_id: number | null
}

export interface ConstraintInput {
  category: 'BASE' | 'TEMPORARY'
  kind: 'FACULTY_UNAVAILABLE' | 'ROOM_UNAVAILABLE' | 'BATCH_UNAVAILABLE' | 'RULE'
  target_id?: string | null
  days?: number[]
  start_time?: string
  end_time?: string
  start_date?: string | null
  end_date?: string | null
  rule_field?: 'max_consecutive' | 'max_daily_load' | 'max_weekly_load' | null
  rule_value?: number | null
  reason?: string
}

export interface MoveCheck {
  session_id: string
  target_label: string
  room_id: string | null
  allowed: boolean
  hard: Blocker[]
  resolvable: Blocker[]
}

export interface OptionMove {
  session_id: string
  subject_code: string
  subject_name: string
  batch_id: string
  from_label: string
  from_room: string
  to_label: string
  to_room: string
  moved_time: boolean
}

export interface RepairChoice {
  rank: number
  label: string
  recommended: boolean
  moves: OptionMove[]
  knock_on: number
  changed: number
  unchanged: number
  total: number
  time_moves: number
  room_only_moves: number
  retention_pct: number
  time_retention_pct: number
  hard_violations: number
  soft_cost: number
  quality: Quality
  quality_delta: Record<string, number>
  status: string
  phase1_status: string
  phase2_status: string
  minimal_proven: boolean
  solve_seconds: number
  changes: Change[]
}

export interface ScheduleRequest {
  id: number
  faculty_id: string
  faculty_name: string
  status: string
  reason: string
  start_date: string
  end_date: string
  start_time: string
  end_time: string
  window_label: string
  created_by: string
  created_at: string | null
  submitted_at: string | null
  decided_by: string | null
  decided_at: string | null
  decision_note: string | null
  base_version_id: number | null
  result_version_id: number | null
  stale: boolean
  affected: Affected[]
  options: RepairChoice[]
  failure: { status: string; reason: string | null; diagnosis: Diagnosis | null } | null
  selected_rank: number | null
  auto_selected: boolean
}

export interface RequestInput {
  faculty_id?: string | null
  start_date: string
  end_date: string
  start_time: string
  end_time: string
  reason?: string
}

export interface FacultyInfo {
  id: string
  name: string
  department: string
  max_daily_load: number
  max_weekly_load: number
  max_consecutive: number
  unavailable: number[]
  preferred_off: number[]
  subjects: string[]
  weekly_hours: number
  busiest_day_hours: number
  years_taught: string[]
  session_count: number
  username: string | null
}

export interface RoomInfo {
  id: string
  name: string
  building: string
  room_type: string
  capacity: number
  active: boolean
  capabilities: string[]
  unavailable: number[]
  booked_hours: number
  utilisation_pct: number
  avg_fill_pct: number
}

export interface BatchInfo {
  id: string
  name: string
  strength: number
  department: string
  program: string
  year: number
  year_label: string
  semester: number
  sessions: number
  contact_hours: number
}

export interface SubjectInfo {
  id: number
  batch_id: string
  code: string
  name: string
  faculty_id: string
  faculty_name: string
  category: string
  sessions_per_week: number
  duration: number
  room_type: string
  required_capability: string | null
  elective_key: string | null
  headcount: number | null
}

export interface StructureBatch {
  id: string
  name: string
  strength: number
  sessions: number
  contact_hours: number
}

export interface Structure {
  departments: {
    name: string
    programs: {
      name: string
      years: {
        number: number
        label: string
        semesters: { number: number; batches: StructureBatch[] }[]
      }[]
    }[]
  }[]
  shared_faculty: number
  shared_rooms: number
  faculty: number
  rooms: number
  labs: number
}

export interface Dashboard {
  today: string
  version: Version
  proposal: Version | null
  solver: { status: string; objective: number | null; best_bound: number | null; solve_seconds: number }
  violations: number
  families: number
  quality: Quality
  pending_count: number
  pending_requests: ScheduleRequest[]
  overrides: Constraint[]
  locked: number
  versions: Version[]
  structure: { years: number; divisions: number; faculty: number; rooms: number; labs: number; sessions: number }
}

export interface Analytics {
  validation: { total: number; counts: Record<string, number>; families: number }
  quality: Quality
  solver: { status: string; objective: number | null; best_bound: number | null; solve_seconds: number }
  totals: { sessions: number; scheduled: number; contact_hours: number; locked: number }
  faculty: {
    faculty_id: string
    name: string
    weekly_hours: number
    weekly_cap: number
    busiest_day_hours: number
    daily_cap: number
    years_taught: string[]
  }[]
  rooms: {
    room_id: string
    name: string
    room_type: string
    capacity: number
    active: boolean
    booked_hours: number
    utilisation_pct: number
    avg_fill_pct: number
  }[]
  versions: { number: number; label: string; status: string; changed: number | null; retention_pct: number | null; created_at: string }[]
  requests: Record<string, number>
  overrides: Record<string, number>
}

/** A refused request, carrying the HTTP status so callers can react to 401. */
export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function failure(res: Response): Promise<ApiError> {
  // statusText is often empty, and an Error with a falsy message would leave
  // the UI stuck on its loading state instead of surfacing the failure.
  let detail = res.statusText || `Request failed with status ${res.status}`
  try {
    const body = await res.json()
    if (typeof body?.detail === 'string') detail = body.detail
    else if (Array.isArray(body?.detail) && body.detail[0]?.msg) detail = body.detail[0].msg
  } catch {
    // response had no JSON body; keep the fallback above
  }
  return new ApiError(detail, res.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies must keep the browser's own multipart content type.
  const isForm = init?.body instanceof FormData
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: isForm ? {} : { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!res.ok) throw await failure(res)
  return res.json() as Promise<T>
}

const send = <T,>(method: string, path: string, body?: unknown) =>
  request<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  })

const post = <T,>(path: string, body: unknown = {}) => send<T>('POST', path, body)

/** The stream could not be read at all; the plain POST is still worth trying. */
class StreamUnavailable extends Error {}

/**
 * Run a solve through its streaming endpoint, reporting each stage as the
 * server reaches it, and resolve with the same payload the plain POST returns.
 *
 * Falls back to the plain POST when the stream never produced a single stage —
 * a buffering proxy, or an old server without the route — unless `fallback`
 * is false, as for creating a request, which the stream route has already
 * recorded and a second POST would duplicate. The fallback is never used once
 * stages have arrived: the solve is already running server-side.
 */
async function streamed<T>(
  path: string,
  body: unknown,
  onProgress: (p: SolveProgress) => void,
  fallback = true,
): Promise<T> {
  const done: SolveStage[] = []
  let saw = false

  try {
    const res = await fetch(`/api${path}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw await failure(res)
    if (!res.body) throw new StreamUnavailable()

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let result: T | undefined
    let failed: string | undefined

    for (;;) {
      const { value, done: finished } = await reader.read()
      if (finished) break
      buffer += decoder.decode(value, { stream: true })

      // SSE frames are separated by a blank line; lines opening with ':' are
      // keep-alive comments and carry nothing.
      let split = buffer.indexOf('\n\n')
      while (split >= 0) {
        const frame = buffer.slice(0, split)
        buffer = buffer.slice(split + 2)
        for (const line of frame.split('\n')) {
          if (!line.startsWith('data:')) continue
          const event = JSON.parse(line.slice(5).trim())
          if (event.event === 'stage_started') {
            saw = true
            onProgress({ running: { key: event.key, label: event.label }, done })
          } else if (event.event === 'stage_finished') {
            saw = true
            done.push({
              key: event.key,
              label: event.label,
              seconds: event.seconds,
              detail: event.detail ?? '',
            })
            onProgress({ running: null, done })
          } else if (event.event === 'result') {
            result = event.data as T
          } else if (event.event === 'error') {
            failed = event.detail
          }
        }
        split = buffer.indexOf('\n\n')
      }
    }

    if (failed !== undefined) throw new Error(failed)
    if (result === undefined) throw new StreamUnavailable()
    return result
  } catch (e) {
    if (saw || !fallback || !(e instanceof StreamUnavailable)) throw e
    return post<T>(path, body)
  }
}

function query(params: Record<string, string | number | null | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') q.set(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export interface GridOptions {
  source?: Source
  versionId?: number | null
  requestId?: number | null
  rank?: number | null
}

/** A download link for the timetable being viewed. The session cookie goes
 * with it, so no token ever appears in the URL. */
export function exportUrl(
  kind: 'xlsx' | 'pdf' | 'ics',
  opts: { source?: Source; versionId?: number | null; view?: ViewKind; id?: string } = {},
): string {
  return `/api/export/${kind}${query({
    source: opts.source ?? 'published',
    version_id: opts.versionId,
    view: kind === 'ics' && opts.id ? opts.view : undefined,
    id: kind === 'ics' ? opts.id : undefined,
  })}`
}

type Progress = ((p: SolveProgress) => void) | undefined

export const api = {
  // -- accounts --------------------------------------------------------
  me: () => request<User>('/auth/me'),
  login: (username: string, password: string) =>
    post<User>('/auth/login', { username, password }),
  demo: (role: Role, username?: string) => post<User>('/auth/demo', { role, username }),
  demoAccounts: () => request<DemoAccount[]>('/auth/demo-accounts'),
  logout: () => post<{ signed_out: boolean }>('/auth/logout'),

  // -- shared views ----------------------------------------------------
  state: () => request<AppState>('/state'),
  entities: () => request<Entities>('/entities'),
  structure: () => request<Structure>('/academic/structure'),
  batches: () => request<BatchInfo[]>('/batches'),
  grid: (view: ViewKind, id: string, source: Source | GridOptions = 'published') => {
    const o: GridOptions = typeof source === 'string' ? { source } : source
    return request<Grid>(
      `/grid${query({
        view,
        id,
        source: o.source ?? 'published',
        version_id: o.versionId,
        request_id: o.requestId,
        rank: o.rank,
      })}`,
    )
  },
  explain: (sessionId: string, source: Source) =>
    request<Explanation>(`/explain${query({ session_id: sessionId, source })}`),

  // -- coordinator: scheduling -----------------------------------------
  scenarios: () => request<Scenario[]>('/scenarios'),
  whatIfScenario: (scenario: string, weights?: Weights, onProgress?: Progress) =>
    onProgress
      ? streamed<WhatIf>('/what-if', { scenario, weights }, onProgress)
      : post<WhatIf>('/what-if', { scenario, weights }),
  whatIfCustom: (
    disruptions: DisruptionInput[],
    weights?: Weights,
    onProgress?: Progress,
    ruleChanges: RuleChangeInput[] = [],
  ) =>
    onProgress
      ? streamed<WhatIf>(
          '/what-if',
          { disruptions, rule_changes: ruleChanges, weights },
          onProgress,
        )
      : post<WhatIf>('/what-if', { disruptions, rule_changes: ruleChanges, weights }),
  reoptimize: (onProgress?: Progress) =>
    onProgress ? streamed<WhatIf>('/reoptimize', {}, onProgress) : post<WhatIf>('/reoptimize'),
  parse: (text: string) => post<ParsedRule>('/parse', { text }),
  importWorkbook: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    // Let the browser set the multipart boundary; do not send a JSON header.
    return request<ImportResult>('/import', { method: 'POST', body: form, headers: {} })
  },
  apply: () => post<AppState>('/apply'),
  discard: () => post<AppState>('/discard'),
  /** Solve from scratch. By default the result waits as a proposal for review;
   * `publish` makes it the published version immediately. */
  generate: (weights: Weights, time_limit: number, onProgress?: Progress, publish = false) =>
    onProgress
      ? streamed<AppState>('/generate', { weights, time_limit, publish }, onProgress)
      : post<AppState>('/generate', { weights, time_limit, publish }),
  reset: () => post<AppState>('/reset'),

  // -- coordinator: administration -------------------------------------
  dashboard: () => request<Dashboard>('/admin/dashboard'),
  faculty: () => request<FacultyInfo[]>('/faculty'),
  patchFaculty: (id: string, patch: Partial<FacultyInfo>) =>
    send<FacultyInfo>('PATCH', `/faculty/${id}`, patch),
  setAvailability: (id: string, unavailable: number[], preferred_off: number[]) =>
    send<FacultyInfo>('PUT', `/faculty/${id}/availability`, { unavailable, preferred_off }),
  rooms: () => request<RoomInfo[]>('/rooms'),
  patchRoom: (id: string, patch: Partial<RoomInfo>) =>
    send<RoomInfo>('PATCH', `/rooms/${id}`, patch),
  setRoomBlocks: (id: string, unavailable: number[]) =>
    send<RoomInfo>('PUT', `/rooms/${id}/blocks`, { unavailable }),
  patchBatch: (id: string, patch: { name?: string; strength?: number }) =>
    send<BatchInfo>('PATCH', `/batches/${id}`, patch),
  createBatch: (input: {
    id: string
    name: string
    strength: number
    department: string
    program?: string
    year: number
    year_label?: string
    semester: number
  }) => post<BatchInfo>('/batches', input),
  subjects: (batchId?: string) =>
    request<SubjectInfo[]>(`/subjects${query({ batch_id: batchId })}`),
  createSubject: (input: {
    batch_id: string
    code: string
    name: string
    faculty_id: string
    category: 'THEORY' | 'TUTORIAL' | 'LAB'
    sessions_per_week: number
    duration: number
    required_capability?: string | null
  }) => post<SubjectInfo>('/subjects', input),
  patchSubject: (id: number, patch: Partial<SubjectInfo>) =>
    send<SubjectInfo>('PATCH', `/subjects/${id}`, patch),
  deleteSubject: (id: number) => send<{ deleted: number }>('DELETE', `/subjects/${id}`),
  constraints: () => request<Constraint[]>('/constraints'),
  createConstraint: (input: ConstraintInput) => post<Constraint>('/constraints', input),
  setConstraintStatus: (id: number, status: 'ACTIVE' | 'INACTIVE') =>
    send<Constraint>('PATCH', `/constraints/${id}`, { status }),
  deleteConstraint: (id: number) => send<{ deleted: number }>('DELETE', `/constraints/${id}`),
  restorePreview: (id: number) => post<WhatIf>(`/constraints/${id}/restore-preview`),
  locks: () => request<Constraint[]>('/locks'),
  lock: (sessionId: string, keepRoom = true, reason = '') =>
    post<Constraint>('/locks', { session_id: sessionId, keep_room: keepRoom, reason }),
  unlock: (sessionId: string) => send<{ unlocked: string }>('DELETE', `/locks/${sessionId}`),
  moveCheck: (sessionId: string, timeslotId: number, roomId?: string | null) =>
    post<MoveCheck>('/moves/check', { session_id: sessionId, timeslot_id: timeslotId, room_id: roomId ?? null }),
  movePreview: (sessionId: string, timeslotId: number, roomId?: string | null, onProgress?: Progress) => {
    const body = { session_id: sessionId, timeslot_id: timeslotId, room_id: roomId ?? null }
    return onProgress
      ? streamed<WhatIf>('/moves/preview', body, onProgress)
      : post<WhatIf>('/moves/preview', body)
  },
  versions: () => request<Version[]>('/versions'),
  analytics: () => request<Analytics>('/analytics'),
  saveWeights: (w: Weights) => send<Weights>('PUT', '/settings/weights', w),
  restoreRehearsed: () => post<Version>('/admin/restore-rehearsed'),

  // -- teacher requests -------------------------------------------------
  requests: (status?: string) => request<ScheduleRequest[]>(`/requests${query({ status })}`),
  request: (id: number) => request<ScheduleRequest>(`/requests/${id}`),
  createRequest: (input: RequestInput, onProgress?: Progress) =>
    onProgress
      ? streamed<ScheduleRequest>('/requests', input, onProgress, false)
      : post<ScheduleRequest>('/requests', input),
  recompute: (id: number, onProgress?: Progress) =>
    onProgress
      ? streamed<ScheduleRequest>(`/requests/${id}/recompute`, {}, onProgress, false)
      : post<ScheduleRequest>(`/requests/${id}/recompute`),
  submitRequest: (id: number, choice: { rank?: number; auto?: boolean }) =>
    post<ScheduleRequest>(`/requests/${id}/submit`, choice),
  withdrawRequest: (id: number) => post<ScheduleRequest>(`/requests/${id}/withdraw`),
  reviewRequest: (id: number, rank?: number) =>
    request<WhatIf>(`/requests/${id}/review${query({ rank })}`),
  approveRequest: (id: number, note = '') =>
    post<{ request: ScheduleRequest; version: Version }>(`/requests/${id}/approve`, { note }),
  rejectRequest: (id: number, note = '') =>
    post<ScheduleRequest>(`/requests/${id}/reject`, { note }),
}
