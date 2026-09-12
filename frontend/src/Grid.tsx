import type { Cell, Grid as GridData } from './api'

function cellTone(cell: Cell): string {
  if (cell.changed && cell.moved_time)
    return 'border-move-500 bg-move-50 ring-1 ring-move-500/40'
  if (cell.changed) return 'border-room-500 bg-room-50 ring-1 ring-room-500/30'
  if (cell.duration > 1) return 'border-accent-200 bg-accent-50'
  if (cell.elective_group) return 'border-slate-200 bg-slate-50'
  return 'border-slate-200 bg-white'
}

/** The second line of a card: whatever the current view does not already say. */
function detail(cell: Cell, view: GridData['view']): string {
  const surname = cell.faculty_name.replace(/^(Prof\.|Dr\.)\s*/, '')
  if (view === 'faculty') return `${cell.batch_id} · ${cell.room_id}`
  if (view === 'room') return `${cell.batch_id} · ${surname}`
  return `${cell.room_id} · ${surname}`
}

function LockGlyph() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="h-3 w-3 shrink-0 text-slate-500"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      aria-label="locked"
    >
      <path d="M6 11h12v10H6zM8 11V7a4 4 0 1 1 8 0v4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

function SessionCard({
  cell,
  view,
  onSelect,
  selected,
}: {
  cell: Cell
  view: GridData['view']
  onSelect?: (cell: Cell) => void
  selected?: boolean
}) {
  const title = cell.from_label
    ? `${cell.subject_name} — moved from ${cell.from_label}. Click to see why.`
    : `${cell.subject_name} · ${cell.faculty_name} · ${cell.room_id}${
        cell.locked ? ' · locked by the coordinator' : ''
      }. Click to see why it sits here.`

  return (
    <button
      type="button"
      onClick={() => onSelect?.(cell)}
      title={title}
      className={`group flex h-full min-h-0 w-full cursor-pointer flex-col justify-center overflow-hidden rounded-lg border px-2 py-1 text-left transition hover:border-accent-400 hover:shadow-sm ${cellTone(cell)} ${
        selected ? 'ring-2 ring-accent-500' : ''
      }`}
    >
      <div className="flex items-center gap-1">
        <span className="truncate text-[12px] font-semibold text-slate-900">
          {cell.subject_code}
        </span>
        {cell.duration > 1 && (
          <span className="shrink-0 rounded bg-accent-100 px-1 text-[9px] font-semibold text-accent-700">
            {cell.duration}h
          </span>
        )}
        {cell.locked && <LockGlyph />}
      </div>
      <div className="truncate text-[10px] text-slate-500">{detail(cell, view)}</div>
      {cell.from_label && (
        <div className="truncate text-[10px] font-medium text-move-600">
          was {cell.from_label}
        </div>
      )}
    </button>
  )
}

export function TimetableGrid({
  grid,
  onSelect,
  selectedId,
}: {
  grid: GridData
  onSelect?: (cell: Cell) => void
  selectedId?: string | null
}) {
  const { calendar, cells } = grid
  const byslot = new Map<string, Cell[]>()
  for (const c of cells) {
    const key = `${c.day}-${c.period}`
    byslot.set(key, [...(byslot.get(key) ?? []), c])
  }

  return (
    <div className="overflow-x-auto">
      <div
        className="grid min-w-[760px] gap-1.5"
        style={{
          gridTemplateColumns: `64px repeat(${calendar.days.length}, minmax(0, 1fr))`,
          gridTemplateRows: `30px repeat(${calendar.period_start.length}, 56px)`,
        }}
      >
        <div />
        {calendar.days.map((d) => (
          <div
            key={d}
            className="flex items-center justify-center rounded-md bg-slate-50 text-[11px] font-semibold tracking-wide text-slate-600 uppercase"
          >
            {d}
          </div>
        ))}

        {calendar.period_start.map((start, period) => (
          <div
            key={start}
            className="flex flex-col justify-center pr-2 text-right"
            style={{ gridColumn: 1, gridRow: period + 2 }}
          >
            <span className="text-[11px] font-medium text-slate-600">{start}</span>
            {period !== calendar.lunch_period && (
              <span className="text-[9px] text-slate-400">{calendar.period_end[period]}</span>
            )}
          </div>
        ))}

        {calendar.period_start.map((_, period) =>
          calendar.days.map((day, d) => {
            if (period === calendar.lunch_period) {
              return (
                <div
                  key={`lunch-${day}`}
                  className="flex items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50/70 text-[10px] tracking-wide text-slate-400"
                  style={{ gridColumn: d + 2, gridRow: period + 2 }}
                >
                  lunch
                </div>
              )
            }
            const here = byslot.get(`${d}-${period}`) ?? []
            if (here.length === 0) {
              return (
                <div
                  key={`empty-${day}-${period}`}
                  className="rounded-lg border border-slate-100 bg-slate-50/40"
                  style={{ gridColumn: d + 2, gridRow: period + 2 }}
                />
              )
            }
            const span = Math.max(...here.map((c) => c.duration))
            return (
              <div
                key={`slot-${day}-${period}`}
                className="flex min-h-0 gap-1"
                style={{
                  gridColumn: d + 2,
                  gridRow: `${period + 2} / span ${span}`,
                }}
              >
                {here.map((c) => (
                  <div key={c.session_id} className="min-w-0 flex-1">
                    <SessionCard
                      cell={c}
                      view={grid.view}
                      onSelect={onSelect}
                      selected={selectedId === c.session_id}
                    />
                  </div>
                ))}
              </div>
            )
          }),
        )}
      </div>
    </div>
  )
}

export function GridLegend() {
  const items = [
    ['bg-white border-slate-300', 'unchanged'],
    ['bg-move-50 border-move-500', 'moved to a new time'],
    ['bg-room-50 border-room-500', 'moved room only'],
    ['bg-accent-50 border-accent-200', 'multi-hour lab'],
  ]
  return (
    <div className="flex flex-wrap items-center gap-4 text-[11px] text-slate-500">
      {items.map(([cls, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className={`h-3 w-4 rounded border ${cls}`} />
          {label}
        </span>
      ))}
      <span className="flex items-center gap-1.5">
        <LockGlyph />
        locked
      </span>
    </div>
  )
}
