import type { ReactNode } from 'react'
import type {
  Calendar,
  Explanation,
  Quality,
  SolveProgress,
  Solver,
  Validation,
  WhatIf,
} from './api'

export function Section({
  title,
  right,
  children,
}: {
  title: string
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-3.5">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        {right}
      </header>
      <div className="p-5">{children}</div>
    </section>
  )
}

export function Stat({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string | number
  tone?: 'default' | 'good' | 'warn'
}) {
  const toneCls =
    tone === 'good' ? 'text-keep-600' : tone === 'warn' ? 'text-move-600' : 'text-slate-900'
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
      <div className="text-[11px] font-medium text-slate-500">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${toneCls}`}>{value}</div>
    </div>
  )
}

export function StatusPill({ status }: { status: string }) {
  const good = status === 'OPTIMAL'
  const ok = good || status === 'FEASIBLE'
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold tracking-wider ring-1 ring-inset ${
        good
          ? 'bg-keep-500/10 text-keep-500 ring-keep-500/25'
          : ok
            ? 'bg-accent-400/10 text-accent-400 ring-accent-400/25'
            : 'bg-alarm-500/10 text-alarm-500 ring-alarm-500/25'
      }`}
    >
      {status}
    </span>
  )
}

/**
 * Live solve progress: the step running now, and every finished step with the
 * time it actually took.
 *
 * There is no progress bar and no percentage, because CP-SAT cannot report how
 * much search remains — a bar would have to invent its own position. What is
 * shown instead is true: which stage is executing, what it found, and how long
 * each completed stage ran.
 */
export function SolveProgressPanel({
  label,
  progress,
}: {
  label: string
  progress: SolveProgress | null
}) {
  const done = progress?.done ?? []
  const running = progress?.running ?? null
  const elapsed = done.reduce((total, s) => total + s.seconds, 0)

  return (
    <div className="fixed right-5 bottom-5 z-40 w-[min(27rem,calc(100vw-2.5rem))] rounded-2xl border border-slate-200 bg-white p-4 shadow-xl">
      <div className="flex items-center gap-3">
        <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-accent-500 border-t-transparent" />
        <span className="text-sm font-semibold text-slate-900">{label}…</span>
        {done.length > 0 && (
          <span className="ml-auto text-[11px] tabular-nums text-slate-500">
            {elapsed.toFixed(1)}s elapsed
          </span>
        )}
      </div>

      {(done.length > 0 || running) && (
        <ol className="mt-3 max-h-[50vh] space-y-2 overflow-y-auto border-t border-slate-100 pt-3">
          {done.map((stage) => (
            <li key={stage.key} className="flex gap-2">
              <span className="mt-px shrink-0 text-[11px] font-bold text-keep-500">✓</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[12px] font-medium text-slate-700">{stage.label}</span>
                  <span className="shrink-0 text-[10px] tabular-nums text-slate-400">
                    {stage.seconds.toFixed(2)}s
                  </span>
                </div>
                {stage.detail && (
                  <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">{stage.detail}</p>
                )}
              </div>
            </li>
          ))}
          {running && (
            <li className="flex gap-2">
              <span className="mt-px shrink-0 animate-pulse text-[11px] text-accent-500">▸</span>
              <span className="text-[12px] font-medium text-accent-600">{running.label}</span>
            </li>
          )}
        </ol>
      )}
    </div>
  )
}

/** The hero panel: disruption impact and the repair that answers it. */
export function RepairPanel({
  result,
  onApply,
  onDiscard,
  busy,
  title,
  actions,
}: {
  result: WhatIf
  onApply: () => void
  onDiscard: () => void
  busy: boolean
  title?: string
  /** Replace the Apply/Discard buttons, e.g. with Approve/Reject. */
  actions?: ReactNode
}) {
  if (!result.feasible) {
    const d = result.diagnosis
    // INFEASIBLE is a proof from the solver; UNKNOWN means the search ran out
    // of time. Only the first justifies saying no repair exists.
    const proved = result.status === 'INFEASIBLE'
    return (
      <Section title="What-if analysis" right={<StatusPill status={result.status} />}>
        <div className="mb-4 rounded-lg border border-alarm-200 bg-alarm-50 p-4">
          <div className="mb-1 font-semibold text-alarm-700">
            {proved
              ? 'No feasible repair exists'
              : result.status === 'REFUSED'
                ? 'This change is not allowed'
                : 'No repair found in the time available'}
          </div>
          <p className="text-sm text-slate-700">{result.story}</p>
          {result.reason && <p className="mt-2 text-sm text-slate-800">{result.reason}</p>}
          {d && proved && <p className="mt-2 text-sm text-slate-800">{d.headline}</p>}
        </div>

        {result.directly_affected.length > 0 && (
          <p className="mb-4 text-xs text-slate-500">
            {result.directly_affected.length} session
            {result.directly_affected.length === 1 ? '' : 's'} in the published timetable are
            directly hit by this disruption.
          </p>
        )}

        {d && d.findings.length > 0 && (
          <div className="space-y-3">
            <h3 className="text-xs font-semibold text-slate-500">
              {proved
                ? 'Diagnosis — what cannot be satisfied'
                : 'Diagnosis — the tightest constraints found'}
            </h3>
            {d.findings.map((f, i) => (
              <div
                key={`${f.category}-${i}`}
                className={`rounded-lg border p-3 ${
                  f.severity === 'blocking'
                    ? 'border-alarm-200 bg-alarm-50/60'
                    : 'border-move-200 bg-move-50/60'
                }`}
              >
                <div className="mb-1 flex items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[9px] font-semibold tracking-wider uppercase ${
                      f.severity === 'blocking'
                        ? 'bg-alarm-100 text-alarm-700'
                        : 'bg-move-100 text-move-700'
                    }`}
                  >
                    {f.severity === 'blocking' ? 'blocking' : 'no slack'}
                  </span>
                  <span className="text-xs font-semibold text-slate-800">{f.category}</span>
                </div>
                <p className="text-xs text-slate-700">{f.message}</p>
                {f.suggestions.length > 0 && (
                  <ul className="mt-2 space-y-0.5">
                    {f.suggestions.map((s) => (
                      <li key={s} className="text-[11px] text-slate-500">
                        → {s}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            <p className="text-[11px] leading-relaxed text-slate-400">
              Each blocking finding is a necessary condition that fails, so it proves no timetable
              exists. Passing every check would not prove one does — rules can still conflict in
              combination.
            </p>
          </div>
        )}
      </Section>
    )
  }

  return (
    <Section
      title={title ?? 'Disruption impact & repair'}
      right={
        <div className="flex items-center gap-2">
          {result.minimal_proven && (
            <span className="rounded-full bg-keep-50 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-keep-700 ring-1 ring-keep-200 ring-inset">
              FEWEST MOVES PROVEN
            </span>
          )}
          <StatusPill status={result.status} />
        </div>
      }
    >
      <p className="mb-4 text-sm text-slate-700">{result.story}</p>

      <div className="mb-5 grid gap-3 sm:grid-cols-[minmax(0,210px)_1fr]">
        <div className="rounded-xl border border-keep-200 bg-gradient-to-b from-keep-50 to-white p-4 text-center">
          <div className="text-[11px] font-semibold tracking-[0.12em] text-keep-700 uppercase">
            Schedule retention
          </div>
          <div className="text-4xl font-bold tabular-nums text-keep-600">
            {result.retention_pct.toFixed(1)}%
          </div>
          <div className="mt-1 text-[12px] text-slate-500">
            {result.unchanged} of {result.total} sessions preserved
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Directly affected" value={result.directly_affected.length} />
          <Stat label="Changed" value={result.changed} tone="warn" />
          <Stat label="Unchanged" value={result.unchanged} tone="good" />
          <Stat label="New time" value={result.time_moves} />
          <Stat label="Room only" value={result.room_only_moves} />
          <Stat
            label="Hard conflicts"
            value={result.validation?.total_violations ?? 0}
            tone={result.validation?.clean ? 'good' : 'warn'}
          />
        </div>
      </div>

      {result.directly_affected.length > 0 && (
        <div className="mb-5">
          <h3 className="mb-2 text-xs font-semibold text-slate-500">Directly affected</h3>
          <ul className="space-y-1">
            {result.directly_affected.map((a) => (
              <li
                key={a.session_id}
                className="flex flex-wrap items-baseline gap-x-2 rounded-lg border border-alarm-100 bg-alarm-50/50 px-3 py-1.5 text-xs"
              >
                <span className="font-medium text-slate-800">{a.subject_name}</span>
                <span className="text-slate-500">{a.batch_id}</span>
                <span className="ml-auto text-slate-500">
                  {a.slot_label} · {a.room_id}
                  {a.duration > 1 && ` · ${a.duration}h block`}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {result.changes.length > 0 && (
        <div className="mb-5">
          <h3 className="mb-2 text-xs font-semibold text-slate-500">
            Proposed changes — current vs proposed
          </h3>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full text-xs">
              <thead className="bg-slate-50 text-[11px] text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Subject</th>
                  <th className="px-3 py-2 text-left font-medium">Batch</th>
                  <th className="px-3 py-2 text-left font-medium">From</th>
                  <th className="px-3 py-2 text-left font-medium">To</th>
                  <th className="px-3 py-2 text-left font-medium">Kind</th>
                </tr>
              </thead>
              <tbody>
                {result.changes.map((c) => (
                  <tr key={c.session_id} className="border-t border-slate-100 align-top text-slate-700">
                    <td className="px-3 py-2">
                      <div className="font-medium text-slate-800">{c.subject_name}</div>
                      {c.why.length > 0 && (
                        <div className="mt-0.5 text-[11px] leading-snug text-slate-500">
                          could not stay: {c.why[0].message}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2 text-slate-500">{c.batch_id}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-400 line-through decoration-slate-300">
                      {c.from_label} {c.from_room}
                    </td>
                    <td className="px-3 py-2 font-medium tabular-nums text-slate-900">
                      {c.to_label} {c.to_room}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                          c.moved_time ? 'bg-move-50 text-move-700' : 'bg-room-50 text-room-700'
                        }`}
                      >
                        {c.moved_time ? 'time' : 'room'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {actions ?? (
          <>
            <button
              type="button"
              onClick={onApply}
              disabled={busy}
              className="rounded-lg bg-keep-500 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-keep-600 disabled:opacity-40"
            >
              Apply repair
            </button>
            <button
              type="button"
              onClick={onDiscard}
              disabled={busy}
              className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm text-slate-700 transition hover:bg-slate-50 disabled:opacity-40"
            >
              Discard
            </button>
          </>
        )}
        <span className="ml-auto text-[11px] tabular-nums text-slate-400">
          phase 1 {result.phase1_seconds}s ({result.phase1_status.toLowerCase()}) · phase 2{' '}
          {result.phase2_seconds}s ({result.phase2_status.toLowerCase()})
        </span>
      </div>
    </Section>
  )
}

export const RULE_LABELS: Record<string, string> = {
  faculty_unavailable: 'Faculty unavailable',
  faculty_busy: 'Faculty already teaching',
  batch_busy: 'Batch already in class',
  batch_unavailable: 'Division away',
  room_busy: 'Room occupied',
  room_unavailable: 'Room unavailable',
  room_type: 'Wrong room type',
  room_capacity: 'Room too small',
  room_capability: 'Room lacks the equipment',
  room_inactive: 'Room out of service',
  locked: 'Locked by the coordinator',
  no_room: 'No suitable room free',
  lunch: 'Crosses protected lunch',
  day_boundary: 'Runs past end of day',
  max_consecutive: 'Consecutive-hours limit',
  daily_load: 'Daily load limit',
  elective_rooms: 'Parallel electives need rooms',
  elective_parallel: 'Elective parallelism',
  not_forced: 'Not forced by a hard rule',
}

/** Why a session sits where it does, and what blocks every alternative. */
export function ExplainPanel({
  explanation,
  calendar,
  onClose,
  footer,
}: {
  explanation: Explanation
  calendar: Calendar
  onClose: () => void
  footer?: ReactNode
}) {
  const byId = new Map(explanation.options.map((o) => [o.timeslot_id, o]))
  const periods = calendar.period_start.length

  const tally = new Map<string, number>()
  for (const o of explanation.options) {
    if (o.feasible) continue
    const rule = o.blockers[0]?.rule
    if (rule) tally.set(rule, (tally.get(rule) ?? 0) + 1)
  }
  const ranked = [...tally.entries()].sort((a, b) => b[1] - a[1])

  return (
    <Section
      title="Why is this session here?"
      right={
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-slate-200 px-2 py-0.5 text-[11px] text-slate-500 hover:bg-slate-50"
        >
          close
        </button>
      }
    >
      <div className="mb-3">
        <div className="text-sm font-semibold text-slate-900">
          {explanation.subject_name}
          <span className="ml-2 text-xs font-normal text-slate-500">
            {explanation.batch_id} · {explanation.faculty_name}
            {explanation.duration > 1 && ` · ${explanation.duration}h block`}
          </span>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Currently {explanation.current_label} in {explanation.current_room}.{' '}
          <span className="text-slate-700">{explanation.headline}</span>
        </p>
      </div>

      <div
        className="mb-3 grid gap-1"
        style={{ gridTemplateColumns: `56px repeat(${calendar.days.length}, minmax(0,1fr))` }}
      >
        <div />
        {calendar.days.map((d) => (
          <div key={d} className="text-center text-[10px] font-semibold text-slate-500 uppercase">
            {d}
          </div>
        ))}
        {calendar.period_start.map((start, period) => (
          <div key={start} className="contents">
            <div className="pr-1 text-right text-[10px] text-slate-400">{start}</div>
            {calendar.days.map((day, d) => {
              const opt = byId.get(d * periods + period)
              const isLunch = period === calendar.lunch_period
              const here =
                explanation.current_label === `${day} ${calendar.period_start[period]}`
              if (isLunch) {
                return (
                  <div
                    key={`${day}-${period}`}
                    className="h-6 rounded border border-dashed border-slate-200"
                  />
                )
              }
              return (
                <div
                  key={`${day}-${period}`}
                  title={
                    opt?.feasible ? `Available — ${opt.room_id}` : (opt?.blockers[0]?.message ?? '')
                  }
                  className={`flex h-6 items-center justify-center rounded border text-[9px] font-medium ${
                    here
                      ? 'border-accent-500 bg-accent-100 text-accent-700'
                      : opt?.feasible
                        ? 'border-keep-200 bg-keep-50 text-keep-700'
                        : 'border-slate-100 bg-slate-50 text-slate-300'
                  }`}
                >
                  {here ? 'now' : opt?.feasible ? 'ok' : '·'}
                </div>
              )
            })}
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-3 text-[11px] text-slate-500">
        <span className="flex items-center gap-1">
          <span className="h-3 w-4 rounded border border-accent-500 bg-accent-100" />
          current
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-4 rounded border border-keep-200 bg-keep-50" />
          available
        </span>
        <span className="flex items-center gap-1">
          <span className="h-3 w-4 rounded border border-slate-200 bg-slate-50" />
          blocked (hover for the rule)
        </span>
      </div>

      {ranked.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-2 text-xs font-semibold text-slate-500">What blocks the alternatives</h3>
          <div className="space-y-1">
            {ranked.map(([rule, count]) => (
              <div
                key={rule}
                className="flex items-center justify-between border-b border-slate-100 py-1 text-xs"
              >
                <span className="text-slate-600">{RULE_LABELS[rule] ?? rule}</span>
                <span className="tabular-nums text-slate-800">
                  {count} slot{count === 1 ? '' : 's'}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
            Derived from the same structured constraint data that builds the solver model. Not a
            solver unsatisfiable core, and not claimed to be a minimal conflicting set.
          </p>
        </div>
      )}
      {footer && <div className="mt-4 border-t border-slate-100 pt-4">{footer}</div>}
    </Section>
  )
}

export const CHECK_LABELS: Record<string, string> = {
  unscheduled_sessions: 'All sessions scheduled',
  faculty_conflicts: 'Faculty conflicts',
  room_conflicts: 'Room conflicts',
  batch_conflicts: 'Batch conflicts',
  room_type_violations: 'Room / lab type',
  capacity_violations: 'Room capacity',
  capability_violations: 'Lab equipment',
  inactive_room_violations: 'Rooms in service',
  faculty_availability: 'Faculty availability',
  batch_availability: 'Division availability',
  room_availability: 'Room availability',
  lab_contiguity: 'Contiguous labs',
  lunch_violations: 'Protected lunch',
  max_consecutive_violations: 'Max consecutive hours',
  daily_load_violations: 'Daily load limit',
  weekly_load_violations: 'Weekly load limit',
  elective_parallel_violations: 'Parallel electives',
  lock_violations: 'Locked sessions',
}

export function ProofPanel({
  solver,
  validation,
  quality,
  title,
}: {
  solver: Solver
  validation: Validation
  quality: Quality
  title: string
}) {
  return (
    <Section title={title} right={<StatusPill status={solver.status} />}>
      <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Solver" value="CP-SAT" />
        <Stat label="Objective" value={solver.objective ?? '—'} />
        <Stat label="Best bound" value={solver.best_bound ?? '—'} />
        <Stat label="Solve time" value={`${solver.solve_seconds}s`} />
      </div>

      <h3 className="mb-2 text-xs font-semibold text-slate-500">
        Hard constraints — verified independently of the solver model
      </h3>
      <div className="mb-5 grid gap-x-6 gap-y-0.5 sm:grid-cols-2">
        {Object.entries(validation.counts).map(([key, count]) => (
          <div
            key={key}
            className="flex items-center justify-between border-b border-slate-100 py-1.5 text-xs"
          >
            <span className="text-slate-600">{CHECK_LABELS[key] ?? key}</span>
            <span
              className={`font-semibold tabular-nums ${
                count === 0 ? 'text-keep-600' : 'text-alarm-600'
              }`}
            >
              {count}
            </span>
          </div>
        ))}
      </div>

      <h3 className="mb-2 text-xs font-semibold text-slate-500">Schedule quality</h3>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="Student idle h" value={quality.student_idle_hours} />
        <Stat label="Faculty idle h" value={quality.faculty_idle_hours} />
        <Stat label="Room use" value={`${quality.room_utilisation_pct}%`} />
        {quality.seat_efficiency_pct !== undefined && (
          <Stat label="Seat efficiency" value={`${quality.seat_efficiency_pct}%`} />
        )}
        <Stat label="Load spread" value={`${quality.faculty_load_spread}h`} />
        <Stat label="Busiest" value={`${quality.busiest_faculty_hours}h`} />
        <Stat label="Last period" value={quality.last_slot_sessions} />
        {quality.oversized_sessions !== undefined && (
          <Stat label="Oversized rooms" value={quality.oversized_sessions} />
        )}
      </div>
    </Section>
  )
}
