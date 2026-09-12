// Pieces the coordinator pages share: small stats, status badges, the version
// table, publishing a proposal, and the timetable explorer.

import { useEffect, useState, type ReactNode } from 'react'

import { api, type Explanation, type Source, type Version, type ViewKind, type WhatIf } from '../api'
import { ProofPanel, RepairPanel } from '../Panels'
import { Badge, Select, Spinner, Tabs, cx, type Tone } from '../ui'
import { formatDateTime, useWorkspace } from '../workspace'
import { TimetablePanel, useLoad } from './shared'

export const round1 = (n: number) => Math.round(n * 10) / 10

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-16 text-sm text-slate-500">
      <Spinner /> {label}
    </div>
  )
}

export function MiniStat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className="text-lg font-semibold text-slate-900 tabular-nums">{value}</div>
    </div>
  )
}

/** A measured quantity against its limit -- a load, not a progress bar. */
export function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0
  const color = pct >= 100 ? 'bg-alarm-500' : pct >= 85 ? 'bg-move-500' : 'bg-accent-500'
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-100">
      <div className={cx('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
    </div>
  )
}

const LIFECYCLE: Record<string, [string, Tone]> = {
  PERMANENT: ['Permanent', 'neutral'],
  UPCOMING: ['Upcoming', 'blue'],
  IN_EFFECT: ['In effect', 'amber'],
  EXPIRED: ['Expired', 'neutral'],
}

export function LifecycleBadge({ value }: { value: string }) {
  const [label, tone] = LIFECYCLE[value] ?? [value, 'neutral']
  return <Badge tone={tone}>{label}</Badge>
}

const RULE_STATUS: Record<string, [string, Tone]> = {
  ACTIVE: ['Active', 'green'],
  INACTIVE: ['Inactive', 'neutral'],
  PENDING: ['Pending approval', 'amber'],
  REJECTED: ['Rejected', 'red'],
}

export function RuleStatusBadge({ value }: { value: string }) {
  const [label, tone] = RULE_STATUS[value] ?? [value, 'neutral']
  return <Badge tone={tone}>{label}</Badge>
}

const VERSION_STATUS: Record<string, [string, Tone]> = {
  PUBLISHED: ['Published', 'green'],
  PROPOSED: ['Proposal', 'amber'],
  SUPERSEDED: ['Superseded', 'neutral'],
  DISCARDED: ['Discarded', 'neutral'],
  STALE: ['Stale', 'amber'],
}

export function VersionStatusBadge({ version }: { version: Version }) {
  const [label, tone] = VERSION_STATUS[version.status] ?? [version.status, 'neutral']
  return (
    <span className="inline-flex flex-wrap gap-1">
      <Badge tone={tone}>{label}</Badge>
      {version.is_current && <Badge tone="blue">current</Badge>}
    </span>
  )
}

export function VersionTable({
  versions,
  actions,
}: {
  versions: Version[]
  actions?: (v: Version) => ReactNode
}) {
  if (versions.length === 0) return <p className="text-sm text-slate-500">No versions yet.</p>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-left text-[11px] font-medium text-slate-500">
            <th className="py-2 pr-3 font-medium">Version</th>
            <th className="py-2 pr-3 font-medium">Status</th>
            <th className="py-2 pr-3 font-medium">Created</th>
            <th className="py-2 pr-3 font-medium">Why</th>
            <th className="py-2 pr-3 text-right font-medium">Changed</th>
            <th className="py-2 pr-3 text-right font-medium">Retention</th>
            <th className="py-2 pr-3 font-medium">Solver</th>
            {actions && <th className="py-2" />}
          </tr>
        </thead>
        <tbody>
          {versions.map((v) => (
            <tr key={v.id} className="border-b border-slate-50 align-top">
              <td className="py-2.5 pr-3">
                <div className="font-medium text-slate-900">v{v.number}</div>
                <div className="text-[11px] text-slate-500">{v.label}</div>
              </td>
              <td className="py-2.5 pr-3">
                <VersionStatusBadge version={v} />
              </td>
              <td className="py-2.5 pr-3 text-xs whitespace-nowrap text-slate-500">
                {formatDateTime(v.created_at)}
                <div>{v.created_by}</div>
              </td>
              <td className="max-w-[300px] py-2.5 pr-3 text-xs text-slate-600">
                {v.reason || '—'}
                {v.request_id !== null && (
                  <div className="text-slate-400">teacher request #{v.request_id}</div>
                )}
                {v.effective_until && (
                  <div className="text-slate-400">override until {v.effective_until}</div>
                )}
              </td>
              <td className="py-2.5 pr-3 text-right text-slate-700 tabular-nums">
                {v.changed_count ?? '—'}
              </td>
              <td className="py-2.5 pr-3 text-right text-slate-700 tabular-nums">
                {v.retention_pct !== null ? `${round1(v.retention_pct)}%` : '—'}
              </td>
              <td className="py-2.5 pr-3 text-xs text-slate-600">
                {v.solver_status}
                {v.objective !== null && <div className="text-slate-400">objective {v.objective}</div>}
              </td>
              {actions && <td className="py-2.5 text-right whitespace-nowrap">{actions(v)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Publishing a proposal
// ---------------------------------------------------------------------------

/** Publish or discard the open proposal. Publishing is always a separate,
 * explicit step: no preview ever goes live on its own. */
export function useRepairActions(onDone: () => void) {
  const ws = useWorkspace()
  async function apply() {
    const s = await ws.run('Publishing', () => api.apply())
    if (!s) return
    await ws.refresh()
    ws.notify(`Published as version ${s.version?.number ?? ''}.`, 'success')
    onDone()
  }
  async function discard() {
    const s = await ws.run('Discarding', () => api.discard())
    if (!s) return
    await ws.refresh()
    ws.notify('Proposal discarded. The published timetable is unchanged.', 'info')
    onDone()
  }
  return { apply, discard }
}

/** A repair result: impact, the proposed grid for the busiest division, and
 * the independent proof for the repaired timetable. */
export function RepairOutcome({
  result,
  onClose,
  showGrid = true,
}: {
  result: WhatIf
  onClose: () => void
  showGrid?: boolean
}) {
  const ws = useWorkspace()
  const { apply, discard } = useRepairActions(onClose)
  const batch = result.changes[0]?.batch_id ?? result.directly_affected[0]?.batch_id ?? null
  return (
    <div className="space-y-5">
      <RepairPanel
        result={result}
        onApply={() => void apply()}
        onDiscard={() => void discard()}
        busy={ws.busy !== null}
      />
      {result.feasible && showGrid && batch && (
        <TimetablePanel
          view="batch"
          id={batch}
          options={{ source: 'pending' }}
          title={`Proposed repair — ${batch}`}
          explain
          reloadKey={result}
        />
      )}
      {result.feasible && result.solver && result.validation && result.quality && (
        <ProofPanel
          title="Repaired timetable — technical proof"
          solver={result.solver}
          validation={result.validation}
          quality={result.quality}
        />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// The timetable explorer
// ---------------------------------------------------------------------------

const VIEW_TABS: { value: ViewKind; label: string }[] = [
  { value: 'batch', label: 'Division' },
  { value: 'faculty', label: 'Faculty' },
  { value: 'room', label: 'Room' },
]

/** Any division, teacher or room, from the published timetable, the open
 * proposal, or a past version. */
export function TimetableExplorer({
  source,
  onSource,
  pendingAvailable = false,
  versionId = null,
  focus = null,
  explainFooter,
  reloadKey,
}: {
  source: Source
  onSource?: (s: Source) => void
  pendingAvailable?: boolean
  versionId?: number | null
  focus?: string | null
  explainFooter?: (ex: Explanation, done: () => void) => ReactNode
  reloadKey?: unknown
}) {
  const entities = useLoad(() => api.entities(), [])
  const [view, setView] = useState<ViewKind>('batch')
  const [id, setId] = useState('')

  useEffect(() => {
    if (entities.data && !id) setId(entities.data.batches[0]?.id ?? '')
  }, [entities.data, id])

  // Follow a repair to the division it changed.
  useEffect(() => {
    if (focus) {
      setView('batch')
      setId(focus)
    }
  }, [focus])

  const lists = entities.data
  const list = !lists
    ? []
    : view === 'batch'
      ? lists.batches
      : view === 'faculty'
        ? lists.faculty
        : lists.rooms

  function switchView(next: ViewKind) {
    setView(next)
    if (!lists) return
    const l = next === 'batch' ? lists.batches : next === 'faculty' ? lists.faculty : lists.rooms
    setId(l[0]?.id ?? '')
  }

  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <Tabs tabs={VIEW_TABS} value={view} onChange={switchView} />
      <Select
        aria-label="Show the timetable of"
        value={id}
        onChange={(e) => setId(e.target.value)}
        className="w-auto min-w-[220px] py-1.5 text-xs"
      >
        {list.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label} — {o.detail}
          </option>
        ))}
      </Select>
      {onSource && (
        <Tabs<Source>
          tabs={[
            { value: 'published', label: 'published' },
            { value: 'pending', label: 'proposed repair', disabled: !pendingAvailable },
          ]}
          value={source}
          onChange={onSource}
        />
      )}
    </div>
  )

  return (
    <TimetablePanel
      view={view}
      id={id}
      options={{ source, versionId }}
      controls={controls}
      explain
      explainFooter={explainFooter}
      reloadKey={reloadKey}
      exportLabel={versionId ? 'this version' : undefined}
    />
  )
}
