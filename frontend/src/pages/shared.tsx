// Building blocks every role's pages share: data loading, the timetable card,
// exports, today's classes, and the ranked repair options of a request.

import { useCallback, useEffect, useState, type ReactNode } from 'react'

import {
  api,
  exportUrl,
  type Affected,
  type Cell,
  type Explanation,
  type Grid,
  type GridOptions,
  type RepairChoice,
  type ScheduleRequest,
  type Source,
  type ViewKind,
} from '../api'
import { GridLegend, TimetableGrid } from '../Grid'
import { ExplainPanel } from '../Panels'
import { Badge, Callout, Card, EmptyState, Icon, Spinner, cx, type Tone } from '../ui'
import { WEEKDAY_NAMES, longDate, useWorkspace, weekday } from '../workspace'

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

export interface Loaded<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

/** Fetch on mount and whenever `deps` change; `reload` fetches again. */
export function useLoad<T>(fn: () => Promise<T>, deps: readonly unknown[]): Loaded<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fn()
      .then(
        (d) => {
          if (cancelled) return
          setData(d)
          setError(null)
        },
        (e: unknown) => {
          if (!cancelled) setError(e instanceof Error ? e.message : String(e))
        },
      )
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // `fn` is re-created every render; `deps` says when it really changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, error, loading, reload }
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

const exportCls =
  'inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 shadow-sm transition hover:border-accent-400 hover:text-accent-700'

export function ExportBar({
  source = 'published',
  versionId = null,
  view,
  id,
  label,
}: {
  source?: Source
  versionId?: number | null
  view?: ViewKind
  id?: string
  label?: string
}) {
  const scope = { source, versionId }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-1 text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
        Export {label ?? (source === 'pending' ? 'proposed repair' : 'published')}
      </span>
      <a href={exportUrl('xlsx', scope)} className={exportCls}>
        <Icon name="download" className="h-3.5 w-3.5" />
        Excel workbook
      </a>
      <a href={exportUrl('pdf', scope)} className={exportCls}>
        <Icon name="download" className="h-3.5 w-3.5" />
        PDF timetable
      </a>
      {view && id && (
        <a href={exportUrl('ics', { ...scope, view, id })} className={exportCls}>
          <Icon name="calendar" className="h-3.5 w-3.5" />
          Calendar — this view
        </a>
      )}
      <a href={exportUrl('ics', scope)} className={exportCls}>
        <Icon name="calendar" className="h-3.5 w-3.5" />
        Calendar — everything
      </a>
    </div>
  )
}

// ---------------------------------------------------------------------------
// The timetable card
// ---------------------------------------------------------------------------

/**
 * One timetable view -- a division, a teacher or a room -- from the published
 * version, the coordinator's proposal, a past version, or a request's option.
 * Clicking a session explains why it sits there (staff only).
 */
export function TimetablePanel({
  view,
  id,
  options = {},
  title,
  subtitle,
  controls,
  explain = false,
  explainFooter,
  exports = true,
  exportLabel,
  reloadKey,
  onGrid,
}: {
  view: ViewKind
  id: string
  options?: GridOptions
  title?: ReactNode
  subtitle?: ReactNode
  controls?: ReactNode
  explain?: boolean
  /** Extra actions under an explanation, e.g. lock or move. `done` closes it
   * and reloads the grid. */
  explainFooter?: (ex: Explanation, done: () => void) => ReactNode
  exports?: boolean
  exportLabel?: string
  reloadKey?: unknown
  onGrid?: (g: Grid | null) => void
}) {
  const ws = useWorkspace()
  const source = options.source ?? 'published'
  const versionId = options.versionId ?? null
  const requestId = options.requestId ?? null
  const rank = options.rank ?? null
  const [explanation, setExplanation] = useState<Explanation | null>(null)

  const { data: grid, error, loading, reload } = useLoad<Grid | null>(
    () =>
      id
        ? api.grid(view, id, { source, versionId, requestId, rank })
        : Promise.resolve(null),
    [view, id, source, versionId, requestId, rank, reloadKey],
  )

  useEffect(() => {
    onGrid?.(grid)
  }, [grid, onGrid])

  useEffect(() => {
    setExplanation(null)
  }, [view, id, source, versionId, requestId, rank])

  // Explanations are computed against the published timetable or the open
  // proposal; for a past version or a request option they would mislead.
  const canExplain = explain && versionId === null && requestId === null

  async function explainCell(cell: Cell) {
    if (explanation?.session_id === cell.session_id) {
      setExplanation(null)
      return
    }
    const ex = await ws.run('Explaining', () => api.explain(cell.session_id, source))
    if (ex) setExplanation(ex)
  }

  const done = useCallback(() => {
    setExplanation(null)
    reload()
  }, [reload])

  return (
    <div className="space-y-5">
      <Card
        title={title ?? grid?.entity_label ?? 'Timetable'}
        subtitle={subtitle ?? grid?.source_label}
        actions={controls}
      >
        {!id ? (
          <EmptyState title="Choose what to show" icon="calendar" />
        ) : error ? (
          <Callout tone="red">{error}</Callout>
        ) : loading && !grid ? (
          <div className="flex items-center gap-2 py-10 text-sm text-slate-500">
            <Spinner /> Loading the timetable…
          </div>
        ) : grid ? (
          <>
            <TimetableGrid
              grid={grid}
              onSelect={canExplain ? (c) => void explainCell(c) : undefined}
              selectedId={explanation?.session_id ?? null}
            />
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <GridLegend />
              {canExplain && (
                <span className="text-[11px] text-slate-400">
                  click any session to see why it sits there
                </span>
              )}
            </div>
            {exports && (
              <div className="mt-4 border-t border-slate-100 pt-4">
                <ExportBar
                  source={source}
                  versionId={versionId}
                  view={view}
                  id={id}
                  label={exportLabel}
                />
              </div>
            )}
          </>
        ) : null}
      </Card>

      {explanation && grid && (
        <ExplainPanel
          explanation={explanation}
          calendar={grid.calendar}
          onClose={() => setExplanation(null)}
          footer={explainFooter?.(explanation, done)}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Today
// ---------------------------------------------------------------------------

export function TodayClasses({
  grid,
  today,
  who,
}: {
  grid: Grid | null
  today: string | undefined
  who: 'faculty' | 'batch'
}) {
  if (!today) return null
  const wd = weekday(today)
  const cal = grid?.calendar
  const teaching = cal ? wd < cal.days.length : true
  const cells = grid
    ? grid.cells.filter((c) => c.day === wd).sort((a, b) => a.period - b.period)
    : []

  return (
    <Card title="Today's classes" subtitle={longDate(today)}>
      {!grid || !cal ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner /> Loading…
        </div>
      ) : !teaching ? (
        <p className="text-sm text-slate-500">No teaching on {WEEKDAY_NAMES[wd]}.</p>
      ) : cells.length === 0 ? (
        <p className="text-sm text-slate-500">No classes today.</p>
      ) : (
        <ol className="space-y-2">
          {cells.map((c) => (
            <li
              key={c.session_id}
              className="flex items-start gap-3 rounded-lg border border-slate-200 px-3 py-2"
            >
              <div className="w-12 shrink-0 text-xs font-semibold text-slate-700 tabular-nums">
                {cal.period_start[c.period]}
                <div className="text-[10px] font-normal text-slate-400">
                  {cal.period_end[Math.min(c.period + c.duration - 1, cal.period_end.length - 1)]}
                </div>
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-slate-800">{c.subject_name}</div>
                <div className="text-xs text-slate-500">
                  {who === 'faculty'
                    ? `${c.batch_id} · ${c.room_id}`
                    : `${c.room_id} · ${c.faculty_name}`}
                </div>
              </div>
              {c.duration > 1 && <Badge tone="blue">{c.duration}h</Badge>}
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Teacher requests
// ---------------------------------------------------------------------------

const REQUEST_STATUS: Record<string, [string, Tone]> = {
  DRAFT: ['Draft', 'neutral'],
  SUBMITTED: ['Awaiting approval', 'amber'],
  APPROVED: ['Approved', 'green'],
  REJECTED: ['Rejected', 'red'],
  WITHDRAWN: ['Withdrawn', 'neutral'],
  STALE: ['Needs recompute', 'amber'],
}

export function RequestStatusBadge({ status }: { status: string }) {
  const [label, tone] = REQUEST_STATUS[status] ?? [status, 'neutral']
  return <Badge tone={tone}>{label}</Badge>
}

export function AffectedList({ items }: { items: Affected[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        None of the published classes fall in this window, so nothing needs to move.
      </p>
    )
  }
  return (
    <ul className="space-y-1.5">
      {items.map((a) => (
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
  )
}

/** Quality measures that changed, and which direction is better for each. */
const DELTAS: Record<string, [string, 'lower' | 'higher']> = {
  student_idle_hours: ['student idle h', 'lower'],
  faculty_idle_hours: ['faculty idle h', 'lower'],
  faculty_load_spread: ['load spread h', 'lower'],
  busiest_faculty_hours: ['busiest teacher h', 'lower'],
  last_slot_sessions: ['last-period classes', 'lower'],
  wasted_seats: ['empty seats', 'lower'],
  oversized_sessions: ['oversized rooms', 'lower'],
  preference_hits: ['preferred-off clashes', 'lower'],
  room_utilisation_pct: ['room use %', 'higher'],
  seat_efficiency_pct: ['seat efficiency %', 'higher'],
}

function DeltaChips({ delta }: { delta: Record<string, number> }) {
  const shown = Object.entries(delta).filter(([k, v]) => k in DELTAS && Math.abs(v) >= 0.05)
  if (shown.length === 0) {
    return <p className="text-[11px] text-slate-400">No change in schedule quality.</p>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map(([k, v]) => {
        const [label, better] = DELTAS[k]
        const good = better === 'lower' ? v < 0 : v > 0
        const value = Number.isInteger(v) ? v : Number(v.toFixed(1))
        return (
          <span
            key={k}
            className={cx(
              'rounded px-1.5 py-0.5 text-[10px] font-medium',
              good ? 'bg-keep-50 text-keep-700' : 'bg-alarm-50 text-alarm-700',
            )}
          >
            {value > 0 ? '+' : '−'}
            {Math.abs(value)} {label}
          </span>
        )
      })}
    </div>
  )
}

/**
 * The solver's ranked repair alternatives. Rank 1 is the lexicographic optimum
 * -- fewest moved start times, then fewest room changes, then the lowest soft
 * cost -- so "Best" is a property the solver established, not a heuristic.
 */
export function OptionCards({
  options,
  selected = null,
  onSelect,
  chosen = null,
  auto = false,
}: {
  options: RepairChoice[]
  selected?: number | null
  onSelect?: (rank: number) => void
  chosen?: number | null
  auto?: boolean
}) {
  return (
    <div
      role={onSelect ? 'radiogroup' : undefined}
      aria-label={onSelect ? 'Repair options' : undefined}
      className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3"
    >
      {options.map((o) => {
        const isSelected = selected === o.rank
        return (
          <div
            key={o.rank}
            role={onSelect ? 'radio' : undefined}
            aria-checked={onSelect ? isSelected : undefined}
            aria-label={onSelect ? `Option ${o.rank}` : undefined}
            tabIndex={onSelect ? 0 : undefined}
            onClick={() => onSelect?.(o.rank)}
            onKeyDown={(e) => {
              if (onSelect && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault()
                onSelect(o.rank)
              }
            }}
            className={cx(
              'rounded-xl border bg-white p-4 transition',
              onSelect && 'cursor-pointer hover:border-accent-400',
              isSelected ? 'border-accent-500 ring-2 ring-accent-100' : 'border-slate-200',
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold tracking-wide text-slate-400 uppercase">
                  Option {o.rank}
                </div>
                <div className="text-sm font-semibold text-slate-900">{o.label}</div>
              </div>
              <div className="flex flex-wrap justify-end gap-1">
                {o.recommended && <Badge tone="green">Best</Badge>}
                {chosen === o.rank && (
                  <Badge tone="blue">{auto ? 'Auto-selected' : 'Chosen'}</Badge>
                )}
              </div>
            </div>

            <div className="mt-3 flex items-baseline gap-2">
              <span className="text-2xl font-bold text-keep-600 tabular-nums">
                {o.retention_pct.toFixed(1)}%
              </span>
              <span className="text-xs text-slate-500">
                retained · {o.unchanged}/{o.total} unchanged
              </span>
            </div>

            <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <dt className="text-slate-500">Sessions moved</dt>
              <dd className="text-right font-medium text-slate-800 tabular-nums">{o.changed}</dd>
              <dt className="text-slate-500">New time</dt>
              <dd className="text-right text-slate-700 tabular-nums">{o.time_moves}</dd>
              <dt className="text-slate-500">Room only</dt>
              <dd className="text-right text-slate-700 tabular-nums">{o.room_only_moves}</dd>
              <dt className="text-slate-500">Knock-on moves</dt>
              <dd className="text-right text-slate-700 tabular-nums">{o.knock_on}</dd>
              <dt className="text-slate-500">Hard conflicts</dt>
              <dd
                className={cx(
                  'text-right font-medium tabular-nums',
                  o.hard_violations ? 'text-alarm-600' : 'text-keep-600',
                )}
              >
                {o.hard_violations}
              </dd>
              <dt className="text-slate-500">Soft cost</dt>
              <dd className="text-right text-slate-700 tabular-nums">{o.soft_cost}</dd>
            </dl>

            <div className="mt-3">
              <DeltaChips delta={o.quality_delta} />
            </div>

            {o.moves.length > 0 && (
              <details className="mt-3" onClick={(e) => e.stopPropagation()}>
                <summary className="cursor-pointer text-xs font-medium text-accent-600">
                  {o.moves.length} move{o.moves.length === 1 ? '' : 's'}
                </summary>
                <ul className="mt-2 space-y-1">
                  {o.moves.map((m) => (
                    <li key={m.session_id} className="text-[11px] leading-snug text-slate-600">
                      <span className="font-medium text-slate-800">
                        {m.subject_code} {m.batch_id}
                      </span>{' '}
                      <span className="text-slate-400 line-through">
                        {m.from_label} {m.from_room}
                      </span>{' '}
                      → {m.to_label} {m.to_room}
                    </li>
                  ))}
                </ul>
              </details>
            )}

            <div className="mt-3 text-[10px] text-slate-400">
              {o.status} · {o.solve_seconds}s
              {o.minimal_proven && ' · fewest moves proven'}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function FailureNotice({
  failure,
}: {
  failure: NonNullable<ScheduleRequest['failure']>
}) {
  // INFEASIBLE is a proof; anything else means the search ran out of time.
  const proved = failure.status === 'INFEASIBLE'
  return (
    <Callout
      tone="red"
      title={proved ? 'No feasible repair exists' : 'No repair found in the time available'}
    >
      {failure.reason && <p>{failure.reason}</p>}
      {failure.diagnosis && (
        <>
          <p className="mt-1">{failure.diagnosis.headline}</p>
          <ul className="mt-2 space-y-1">
            {failure.diagnosis.findings?.map((f, i) => (
              <li key={`${f.category}-${i}`}>
                <span className="font-semibold">{f.category}:</span> {f.message}
              </li>
            ))}
          </ul>
        </>
      )}
    </Callout>
  )
}
