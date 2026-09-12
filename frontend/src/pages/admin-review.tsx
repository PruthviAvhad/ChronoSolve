// Review and insight: the teacher-request approval queue, analytics computed
// from the published timetable, and the version history with exports.

import { useEffect, useState } from 'react'

import { api, exportUrl, type WhatIf } from '../api'
import { CHECK_LABELS, RepairPanel } from '../Panels'
import { Badge, Button, Callout, Card, EmptyState, Input, Metric, Modal, PageHeader, Tabs, cx } from '../ui'
import { formatDateTime, useWorkspace } from '../workspace'
import { Bar, Loading, MiniStat, VersionTable, round1 } from './admin-common'
import {
  AffectedList,
  ExportBar,
  FailureNotice,
  OptionCards,
  RequestStatusBadge,
  TimetablePanel,
  useLoad,
} from './shared'

// ---------------------------------------------------------------------------
// Teacher requests: review, approve, reject
// ---------------------------------------------------------------------------

export function RequestsPage({ focus }: { focus: string | null }) {
  const ws = useWorkspace()
  const [filter, setFilter] = useState<'SUBMITTED' | 'ALL'>(focus ? 'ALL' : 'SUBMITTED')
  const list = useLoad(
    () => api.requests(filter === 'ALL' ? undefined : 'SUBMITTED'),
    [filter, ws.state?.version?.id, ws.state?.pending_requests],
  )
  const [selectedId, setSelectedId] = useState<number | null>(focus ? Number(focus) : null)
  const [tick, setTick] = useState(0)
  const detail = useLoad(
    () => (selectedId !== null ? api.request(selectedId) : Promise.resolve(null)),
    [selectedId, tick],
  )
  const [review, setReview] = useState<WhatIf | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (selectedId === null && list.data?.length) setSelectedId(list.data[0].id)
  }, [list.data, selectedId])

  useEffect(() => {
    setReview(null)
    setNote('')
  }, [selectedId])

  const r = detail.data

  async function openReview() {
    if (!r) return
    const w = await ws.run('Preparing the review', () =>
      api.reviewRequest(r.id, r.selected_rank ?? undefined),
    )
    if (w) setReview(w)
  }

  async function approve() {
    if (!r) return
    const out = await ws.run('Approving and publishing', () => api.approveRequest(r.id, note))
    if (!out) return
    ws.notify(`Approved. Published as version ${out.version.number}.`, 'success')
    setReview(null)
    setTick((t) => t + 1)
    list.reload()
    await ws.refresh()
  }

  async function reject() {
    if (!r) return
    const out = await ws.run('Rejecting', () => api.rejectRequest(r.id, note))
    if (!out) return
    ws.notify('Rejected. The published timetable is unchanged.', 'info')
    setReview(null)
    setTick((t) => t + 1)
    list.reload()
    await ws.refresh()
  }

  const decision = r?.status === 'SUBMITTED' && (
    <div className="flex w-full flex-wrap items-center gap-2">
      <Input
        aria-label="Note to the teacher"
        placeholder="Note to the teacher (optional)"
        value={note}
        maxLength={300}
        onChange={(e) => setNote(e.target.value)}
        className="max-w-sm flex-1"
      />
      <Button variant="success" icon="check" onClick={() => void approve()} disabled={ws.busy !== null}>
        Approve &amp; publish
      </Button>
      <Button variant="danger" onClick={() => void reject()} disabled={ws.busy !== null}>
        Reject
      </Button>
    </div>
  )

  const reviewBatch = review?.changes[0]?.batch_id ?? r?.affected[0]?.batch_id ?? null

  return (
    <>
      <PageHeader
        eyebrow="Scheduling"
        title="Teacher requests"
        description="Each request carries the repair option its teacher chose from the solver's ranking. Review it against today's published timetable, then approve — which publishes a new version — or reject it."
      />
      <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <Card
          title="Queue"
          actions={
            <Tabs
              tabs={[
                { value: 'SUBMITTED', label: 'Awaiting' },
                { value: 'ALL', label: 'All' },
              ]}
              value={filter}
              onChange={setFilter}
            />
          }
          bodyClassName="p-2"
        >
          {list.error ? (
            <Callout tone="red">{list.error}</Callout>
          ) : !list.data ? (
            <Loading />
          ) : list.data.length === 0 ? (
            <div className="p-3">
              <EmptyState
                title={filter === 'SUBMITTED' ? 'Nothing awaiting approval' : 'No requests yet'}
                icon="inbox"
              />
            </div>
          ) : (
            <ul className="space-y-1">
              {list.data.map((q) => (
                <li key={q.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(q.id)}
                    className={cx(
                      'w-full rounded-lg px-3 py-2.5 text-left transition',
                      selectedId === q.id ? 'bg-accent-50 ring-1 ring-accent-200' : 'hover:bg-slate-50',
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium text-slate-800">{q.faculty_name}</span>
                      <RequestStatusBadge status={q.status} />
                    </div>
                    <div className="truncate text-xs text-slate-500">{q.window_label}</div>
                    <div className="mt-0.5 flex gap-2 text-[11px] text-slate-400">
                      <span>#{q.id}</span>
                      <span>{formatDateTime(q.submitted_at ?? q.created_at)}</span>
                      {q.stale && q.status === 'SUBMITTED' && (
                        <span className="text-move-700">timetable changed since</span>
                      )}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="min-w-0 space-y-5">
          {selectedId === null ? (
            <EmptyState title="Select a request" icon="inbox" />
          ) : detail.error ? (
            <Callout tone="red">{detail.error}</Callout>
          ) : !r ? (
            <Loading />
          ) : (
            <>
              <Card
                title={`${r.faculty_name} · ${r.window_label}`}
                subtitle={`Request #${r.id} by ${r.created_by}${r.reason ? ` · ${r.reason}` : ''}`}
                actions={<RequestStatusBadge status={r.status} />}
              >
                {r.stale && r.status === 'SUBMITTED' && (
                  <div className="mb-4">
                    <Callout tone="amber" title="The published timetable changed after these options were computed">
                      Approval re-checks the option against today's version and refuses it if it no longer
                      applies; the teacher can then recompute.
                    </Callout>
                  </div>
                )}
                <h3 className="mb-2 text-xs font-semibold text-slate-500">Classes the absence affects</h3>
                <AffectedList items={r.affected} />
                {r.decided_at && (
                  <p className="mt-4 text-sm text-slate-600">
                    {r.status === 'APPROVED' ? 'Approved' : 'Decided'} by {r.decided_by},{' '}
                    {formatDateTime(r.decided_at)}
                    {r.decision_note && <> — “{r.decision_note}”</>}
                    {r.result_version_id !== null && <> · published as a new version</>}
                  </p>
                )}
              </Card>

              {r.failure && <FailureNotice failure={r.failure} />}

              {r.options.length > 0 && (
                <Card
                  title="Repair options"
                  subtitle={
                    r.selected_rank !== null
                      ? `The teacher chose option ${r.selected_rank}${r.auto_selected ? ' (auto-selected as the best)' : ''}.`
                      : 'No option has been chosen yet.'
                  }
                >
                  <OptionCards
                    options={r.options}
                    selected={r.selected_rank}
                    chosen={r.selected_rank}
                    auto={r.auto_selected}
                  />
                </Card>
              )}

              {r.status === 'SUBMITTED' &&
                (review ? (
                  <RepairPanel
                    result={review}
                    title="Review — current vs proposed"
                    onApply={() => undefined}
                    onDiscard={() => undefined}
                    busy={ws.busy !== null}
                    actions={decision}
                  />
                ) : (
                  <Card title="Decision">
                    <div className="mb-3">
                      <Button variant="secondary" icon="calendar" onClick={() => void openReview()}>
                        Review chosen option
                      </Button>
                      <span className="ml-3 text-xs text-slate-500">
                        Shows every change against today's published timetable, with its independent
                        validation.
                      </span>
                    </div>
                    {decision}
                  </Card>
                ))}

              {review && reviewBatch && r.selected_rank !== null && (
                <TimetablePanel
                  view="batch"
                  id={reviewBatch}
                  options={{ requestId: r.id, rank: r.selected_rank }}
                  title={`Proposed — ${reviewBatch}`}
                  exports={false}
                />
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Analytics
// ---------------------------------------------------------------------------

export function AnalyticsPage() {
  const ws = useWorkspace()
  const { data: a, error } = useLoad(() => api.analytics(), [ws.state?.version?.id])

  if (error) return <Callout tone="red">{error}</Callout>
  if (!a) return <Loading label="Computing analytics…" />

  const q = a.quality
  const rooms = [...a.rooms].sort((x, y) => y.utilisation_pct - x.utilisation_pct)
  const faculty = [...a.faculty].sort((x, y) => y.weekly_hours - x.weekly_hours)
  const overLimit = a.faculty.filter(
    (f) => f.weekly_hours > f.weekly_cap || f.busiest_day_hours > f.daily_cap,
  ).length

  return (
    <>
      <PageHeader
        eyebrow="Insight"
        title="Analytics"
        description="Computed now from the published timetable and the rules in force today. Nothing on this page is stored, sampled or estimated."
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-6">
        <Metric
          label="Sessions scheduled"
          value={`${a.totals.scheduled}/${a.totals.sessions}`}
          tone={a.totals.scheduled === a.totals.sessions ? 'good' : 'bad'}
        />
        <Metric label="Contact hours / week" value={a.totals.contact_hours} />
        <Metric
          label="Hard-rule violations"
          value={a.validation.total}
          hint={`${a.validation.families} rule families checked`}
          tone={a.validation.total ? 'bad' : 'good'}
        />
        <Metric
          label="Seat efficiency"
          value={`${round1(q.seat_efficiency_pct ?? 0)}%`}
          hint={`${q.wasted_seats ?? 0} empty seats across all sessions`}
        />
        <Metric label="Room use" value={`${round1(q.room_utilisation_pct)}%`} hint="of teaching periods booked" />
        <Metric label="Teachers over a limit" value={overLimit} tone={overLimit ? 'bad' : 'good'} />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card title="Faculty workload" subtitle="Published week against each teacher's limits" bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-medium">Teacher</th>
                  <th className="px-3 py-2 font-medium">Week</th>
                  <th className="px-3 py-2 font-medium">Busiest day</th>
                  <th className="px-5 py-2 font-medium">Years</th>
                </tr>
              </thead>
              <tbody>
                {faculty.map((f) => (
                  <tr key={f.faculty_id} className="border-t border-slate-100">
                    <td className="px-5 py-2 text-slate-800">{f.name}</td>
                    <td className="w-36 px-3 py-2">
                      <div className="text-xs text-slate-600 tabular-nums">
                        {f.weekly_hours}/{f.weekly_cap}h
                      </div>
                      <Bar value={f.weekly_hours} max={f.weekly_cap} />
                    </td>
                    <td className="w-32 px-3 py-2">
                      <div className="text-xs text-slate-600 tabular-nums">
                        {f.busiest_day_hours}/{f.daily_cap}h
                      </div>
                      <Bar value={f.busiest_day_hours} max={f.daily_cap} />
                    </td>
                    <td className="px-5 py-2 text-xs text-slate-500">{f.years_taught.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="Room & lab utilisation" subtitle="Booked periods and how well classes fit" bodyClassName="p-0">
          <div className="max-h-[420px] overflow-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-5 py-2 font-medium">Room</th>
                  <th className="px-3 py-2 text-right font-medium">Seats</th>
                  <th className="px-3 py-2 font-medium">Booked</th>
                  <th className="px-5 py-2 text-right font-medium">Avg fill</th>
                </tr>
              </thead>
              <tbody>
                {rooms.map((r) => (
                  <tr key={r.room_id} className="border-t border-slate-100">
                    <td className="px-5 py-2">
                      <span className="text-slate-800">{r.room_id}</span>{' '}
                      <span className="text-[11px] text-slate-400">{r.room_type === 'LAB' ? 'lab' : 'lecture'}</span>
                      {!r.active && <Badge tone="red" className="ml-1">out of service</Badge>}
                    </td>
                    <td className="px-3 py-2 text-right text-xs text-slate-600 tabular-nums">{r.capacity}</td>
                    <td className="w-40 px-3 py-2">
                      <div className="text-xs text-slate-600 tabular-nums">
                        {r.booked_hours}h · {round1(r.utilisation_pct)}%
                      </div>
                      <Bar value={r.utilisation_pct} max={100} />
                    </td>
                    <td className="px-5 py-2 text-right text-xs text-slate-600 tabular-nums">
                      {r.booked_hours ? `${round1(r.avg_fill_pct)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="Schedule quality">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <MiniStat label="Student idle hours" value={q.student_idle_hours} />
            <MiniStat label="Faculty idle hours" value={q.faculty_idle_hours} />
            <MiniStat label="Load spread" value={`${q.faculty_load_spread}h`} />
            <MiniStat label="Busiest teacher" value={`${q.busiest_faculty_hours}h`} />
            <MiniStat label="Last-period classes" value={q.last_slot_sessions} />
            <MiniStat label="Oversized rooms" value={q.oversized_sessions ?? 0} />
            <MiniStat label="Preferred-off clashes" value={q.preference_hits ?? 0} />
            <MiniStat label="Locked sessions" value={a.totals.locked} />
            <MiniStat label="Solver" value={a.solver.status} />
          </div>
        </Card>

        <Card title="Hard-rule verification" subtitle="Independent of the solver model">
          <div className="grid gap-x-6 sm:grid-cols-2">
            {Object.entries(a.validation.counts).map(([k, v]) => (
              <div key={k} className="flex justify-between border-b border-slate-100 py-1.5 text-xs">
                <span className="text-slate-600">{CHECK_LABELS[k] ?? k}</span>
                <span className={cx('font-semibold tabular-nums', v ? 'text-alarm-600' : 'text-keep-600')}>{v}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Published versions" subtitle="How much each change kept">
          {a.versions.length === 0 ? (
            <p className="text-sm text-slate-500">No versions yet.</p>
          ) : (
            <ul className="space-y-2">
              {a.versions.map((v) => (
                <li key={v.number} className="flex items-center gap-3 text-sm">
                  <span className="w-10 font-medium text-slate-800">v{v.number}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-slate-500">{v.label}</span>
                  <span className="text-xs text-slate-500 tabular-nums">
                    {v.changed !== null ? `${v.changed} changed` : '—'}
                  </span>
                  <span className="w-16 text-right text-xs text-slate-700 tabular-nums">
                    {v.retention_pct !== null ? `${round1(v.retention_pct)}%` : '—'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Requests and overrides">
          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <h3 className="mb-2 text-xs font-semibold text-slate-500">Teacher requests</h3>
              {Object.keys(a.requests).length === 0 ? (
                <p className="text-sm text-slate-500">None yet.</p>
              ) : (
                <ul className="space-y-1.5">
                  {Object.entries(a.requests).map(([status, n]) => (
                    <li key={status} className="flex items-center justify-between">
                      <RequestStatusBadge status={status} />
                      <span className="text-sm text-slate-800 tabular-nums">{n}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <h3 className="mb-2 text-xs font-semibold text-slate-500">Active temporary overrides</h3>
              {Object.keys(a.overrides).length === 0 ? (
                <p className="text-sm text-slate-500">None.</p>
              ) : (
                <ul className="space-y-1.5">
                  {Object.entries(a.overrides).map(([life, n]) => (
                    <li key={life} className="flex items-center justify-between text-sm">
                      <span className="text-slate-600">{life.replace('_', ' ').toLowerCase()}</span>
                      <span className="text-slate-800 tabular-nums">{n}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </Card>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Versions & exports
// ---------------------------------------------------------------------------

const linkCls = 'text-xs font-medium text-accent-600 hover:text-accent-700'

export function VersionsPage() {
  const ws = useWorkspace()
  const list = useLoad(() => api.versions(), [ws.state?.version?.id, ws.state?.proposal?.id])
  const [confirm, setConfirm] = useState(false)

  async function restore() {
    const v = await ws.run('Restoring the rehearsed baseline', () => api.restoreRehearsed())
    setConfirm(false)
    if (!v) return
    ws.notify(`Rehearsed baseline republished as version ${v.number}. History is kept.`, 'success')
    await ws.refresh()
    list.reload()
  }

  return (
    <>
      <PageHeader
        eyebrow="Insight"
        title="Versions & exports"
        description="Every timetable that was ever proposed or published, with who made it and why. Past versions stay browsable and exportable; nothing is overwritten."
        actions={
          <Button variant="secondary" icon="history" onClick={() => setConfirm(true)}>
            Restore rehearsed baseline
          </Button>
        }
      />

      <div className="mb-5">
        <Card title="Published timetable">
          <ExportBar source="published" />
        </Card>
      </div>

      <Card title={`${list.data?.length ?? 0} versions`}>
        {list.error ? (
          <Callout tone="red">{list.error}</Callout>
        ) : !list.data ? (
          <Loading />
        ) : (
          <VersionTable
            versions={list.data}
            actions={(v) => (
              <span className="inline-flex items-center gap-3">
                <button
                  type="button"
                  className={linkCls}
                  onClick={() => ws.navigate(`admin/timetables?version=${v.id}`)}
                >
                  View
                </button>
                <a className={linkCls} href={exportUrl('xlsx', { versionId: v.id })}>
                  Excel
                </a>
                <a className={linkCls} href={exportUrl('pdf', { versionId: v.id })}>
                  PDF
                </a>
              </span>
            )}
          />
        )}
      </Card>

      <Modal
        open={confirm}
        title="Restore the rehearsed baseline?"
        onClose={() => setConfirm(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              Cancel
            </Button>
            <Button onClick={() => void restore()}>Republish baseline</Button>
          </>
        }
      >
        <p className="text-sm text-slate-600">
          This publishes the rehearsed demo timetable as a new version. The current version and all
          history are kept, so it can be undone by publishing again.
        </p>
      </Modal>
    </>
  )
}
