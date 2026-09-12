import { useMemo, useState, type FormEvent } from 'react'

import { api, type Grid, type ScheduleRequest } from '../api'
import {
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  Input,
  Metric,
  PageHeader,
  Spinner,
} from '../ui'
import { addDays, formatDateTime, useWorkspace, weekday } from '../workspace'
import {
  AffectedList,
  FailureNotice,
  OptionCards,
  RequestStatusBadge,
  TimetablePanel,
  TodayClasses,
  useLoad,
} from './shared'

export function FacultyPage({ path, params }: { path: string; params: URLSearchParams }) {
  const { user } = useWorkspace()
  if (!user.faculty_id) {
    return (
      <EmptyState
        title="This account is not linked to a teacher"
        body="Ask the coordinator to link your account to your faculty record."
        icon="user"
      />
    )
  }
  if (path === 'faculty/report') return <ReportUnavailability />
  if (path === 'faculty/requests') return <MyRequests focus={params.get('id')} />
  return <MyTimetable facultyId={user.faculty_id} />
}

// ---------------------------------------------------------------------------
// My timetable
// ---------------------------------------------------------------------------

const OPEN = ['DRAFT', 'SUBMITTED', 'STALE']

function MyTimetable({ facultyId }: { facultyId: string }) {
  const ws = useWorkspace()
  const [grid, setGrid] = useState<Grid | null>(null)
  const requests = useLoad(() => api.requests(), [])

  const week = useMemo(() => {
    if (!grid) return null
    const perDay = new Map<number, number>()
    let hours = 0
    for (const c of grid.cells) {
      hours += c.duration
      perDay.set(c.day, (perDay.get(c.day) ?? 0) + c.duration)
    }
    return {
      hours,
      sessions: grid.cells.length,
      divisions: new Set(grid.cells.map((c) => c.batch_id)).size,
      busiest: Math.max(0, ...perDay.values()),
    }
  }, [grid])

  const open = (requests.data ?? []).filter((r) => OPEN.includes(r.status))

  return (
    <>
      <PageHeader
        eyebrow="Faculty"
        title="My timetable"
        description="Your published teaching week. To report an absence, send a request: the solver proposes repairs, and the coordinator approves one before anything changes."
        actions={
          <Button icon="alert" onClick={() => ws.navigate('faculty/report')}>
            Report unavailability
          </Button>
        }
      />

      {week && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Metric label="Teaching hours / week" value={week.hours} />
          <Metric label="Sessions" value={week.sessions} />
          <Metric label="Divisions taught" value={week.divisions} />
          <Metric label="Busiest day" value={`${week.busiest}h`} />
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <TimetablePanel view="faculty" id={facultyId} explain onGrid={setGrid} />
        </div>
        <div className="space-y-5">
          <TodayClasses grid={grid} today={ws.state?.today} who="faculty" />
          <Card
            title="Open requests"
            actions={
              <Button variant="ghost" size="sm" onClick={() => ws.navigate('faculty/requests')}>
                View all
              </Button>
            }
          >
            {requests.loading && !requests.data ? (
              <Spinner />
            ) : open.length === 0 ? (
              <p className="text-sm text-slate-500">No open requests.</p>
            ) : (
              <ul className="space-y-2">
                {open.slice(0, 4).map((r) => (
                  <li key={r.id}>
                    <button
                      type="button"
                      onClick={() => ws.navigate(`faculty/requests?id=${r.id}`)}
                      className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left transition hover:border-accent-400"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium text-slate-800">
                          {r.window_label}
                        </span>
                        <RequestStatusBadge status={r.status} />
                      </div>
                      <div className="truncate text-xs text-slate-500">
                        {r.reason || 'No reason given'}
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Report unavailability -> ranked options -> choose -> submit
// ---------------------------------------------------------------------------

interface WindowForm {
  start_date: string
  end_date: string
  start_time: string
  end_time: string
  reason: string
}

function ReportUnavailability() {
  const ws = useWorkspace()
  const today = ws.state?.today ?? ''
  const [form, setForm] = useState<WindowForm>({
    start_date: today,
    end_date: today,
    start_time: '09:00',
    end_time: '17:00',
    reason: '',
  })
  const [request, setRequest] = useState<ScheduleRequest | null>(null)
  const [selected, setSelected] = useState<number | null>(null)

  function set<K extends keyof WindowForm>(key: K, value: WindowForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function preset(kind: 'today' | 'tomorrow' | 'afternoon' | 'next-week') {
    if (!today) return
    if (kind === 'today') setForm((f) => ({ ...f, start_date: today, end_date: today, start_time: '09:00', end_time: '17:00' }))
    if (kind === 'tomorrow') {
      const t = addDays(today, 1)
      setForm((f) => ({ ...f, start_date: t, end_date: t, start_time: '09:00', end_time: '17:00' }))
    }
    if (kind === 'afternoon') setForm((f) => ({ ...f, start_date: today, end_date: today, start_time: '13:00', end_time: '17:00' }))
    if (kind === 'next-week') {
      const monday = addDays(today, 7 - weekday(today))
      setForm((f) => ({
        ...f,
        start_date: monday,
        end_date: addDays(monday, 4),
        start_time: '09:00',
        end_time: '17:00',
      }))
    }
  }

  async function findOptions(e: FormEvent) {
    e.preventDefault()
    const r = await ws.run('Finding repair options', (p) => api.createRequest(form, p))
    if (r) {
      setRequest(r)
      setSelected(r.options[0]?.rank ?? null)
    }
  }

  async function submit(auto: boolean) {
    if (!request) return
    const r = await ws.run(auto ? 'Auto-selecting the best option' : 'Submitting', () =>
      api.submitRequest(request.id, auto ? { auto: true } : { rank: selected ?? undefined }),
    )
    if (r) {
      setRequest(r)
      ws.notify('Sent to the coordinator. Nothing changes until they approve it.', 'success')
      void ws.refresh()
    }
  }

  async function withdraw() {
    if (!request) return
    const r = await ws.run('Withdrawing', () => api.withdrawRequest(request.id))
    if (r) {
      setRequest(null)
      ws.notify('Request withdrawn.', 'info')
    }
  }

  const draft = request !== null && (request.status === 'DRAFT' || request.status === 'STALE')

  return (
    <>
      <PageHeader
        eyebrow="Faculty"
        title="Report unavailability"
        description="Tell the system when you cannot teach. CP-SAT computes ranked repairs that move as few classes as possible; you choose one, and the coordinator approves it before the timetable changes."
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card title="When are you unavailable?">
          <form onSubmit={findOptions} className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" size="sm" onClick={() => preset('today')}>
                Today
              </Button>
              <Button variant="secondary" size="sm" onClick={() => preset('tomorrow')}>
                Tomorrow
              </Button>
              <Button variant="secondary" size="sm" onClick={() => preset('afternoon')}>
                This afternoon
              </Button>
              <Button variant="secondary" size="sm" onClick={() => preset('next-week')}>
                All next week
              </Button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="From date">
                <Input
                  type="date"
                  required
                  value={form.start_date}
                  onChange={(e) => set('start_date', e.target.value)}
                />
              </Field>
              <Field label="To date">
                <Input
                  type="date"
                  required
                  value={form.end_date}
                  min={form.start_date}
                  onChange={(e) => set('end_date', e.target.value)}
                />
              </Field>
              <Field label="From time">
                <Input
                  type="time"
                  required
                  value={form.start_time}
                  onChange={(e) => set('start_time', e.target.value)}
                />
              </Field>
              <Field label="To time">
                <Input
                  type="time"
                  required
                  value={form.end_time}
                  onChange={(e) => set('end_time', e.target.value)}
                />
              </Field>
            </div>
            <Field label="Reason" hint="Shown to the coordinator with your request.">
              <Input
                value={form.reason}
                maxLength={300}
                placeholder="e.g. Examination duty"
                onChange={(e) => set('reason', e.target.value)}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" icon="sparkles" disabled={ws.busy !== null}>
                Find repair options
              </Button>
              <span className="text-xs text-slate-500">
                Computes options only. Nothing is published.
              </span>
            </div>
          </form>
        </Card>

        <Card title="How this works">
          <ol className="space-y-3 text-sm text-slate-600">
            {[
              'You report the window you cannot teach.',
              'CP-SAT finds up to three ranked repairs: fewest moved classes first, then fewest room changes, then the best schedule quality.',
              'Pick one, or let the system pick the best.',
              'The coordinator reviews it. Only their approval publishes a new version.',
            ].map((step, i) => (
              <li key={step} className="flex gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-50 text-xs font-semibold text-accent-700">
                  {i + 1}
                </span>
                <span>{step}</span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      {request && (
        <div className="mt-6 space-y-5">
          <Card
            title={`Request #${request.id} · ${request.window_label}`}
            subtitle={request.reason || undefined}
            actions={<RequestStatusBadge status={request.status} />}
          >
            <h3 className="mb-2 text-xs font-semibold text-slate-500">
              Classes your absence affects
            </h3>
            <AffectedList items={request.affected} />
          </Card>

          {request.failure ? (
            <FailureNotice failure={request.failure} />
          ) : request.options.length > 0 ? (
            <Card
              title="Repair options"
              subtitle="Ranked by the solver. Each keeps every hard rule; they differ in how much of the week they move."
            >
              <OptionCards
                options={request.options}
                selected={draft ? selected : request.selected_rank}
                onSelect={draft ? setSelected : undefined}
                chosen={request.selected_rank}
                auto={request.auto_selected}
              />
            </Card>
          ) : null}

          {request.options.length > 0 && selected !== null && ws.user.faculty_id && (
            <TimetablePanel
              view="faculty"
              id={ws.user.faculty_id}
              options={{ requestId: request.id, rank: draft ? selected : request.selected_rank }}
              title={`Preview — option ${draft ? selected : request.selected_rank} on your week`}
              exports={false}
            />
          )}

          {draft ? (
            <div className="flex flex-wrap items-center gap-2">
              {request.options.length > 0 && (
                <>
                  <Button variant="success" icon="sparkles" onClick={() => void submit(true)}>
                    Auto-select best &amp; submit
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={selected === null}
                    onClick={() => void submit(false)}
                  >
                    Submit option {selected ?? ''}
                  </Button>
                </>
              )}
              <Button variant="ghost" onClick={() => void withdraw()}>
                Withdraw
              </Button>
            </div>
          ) : request.status === 'SUBMITTED' ? (
            <Callout tone="green" title="Sent for approval">
              The coordinator will review option {request.selected_rank}
              {request.auto_selected ? ' (auto-selected as the best)' : ''}. The published timetable
              stays as it is until they approve it.{' '}
              <button
                type="button"
                className="font-semibold underline"
                onClick={() => ws.navigate(`faculty/requests?id=${request.id}`)}
              >
                Track it in My requests
              </button>
            </Callout>
          ) : null}
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// My requests
// ---------------------------------------------------------------------------

function MyRequests({ focus }: { focus: string | null }) {
  const ws = useWorkspace()
  const list = useLoad(() => api.requests(), [])
  const [open, setOpen] = useState<number | null>(focus ? Number(focus) : null)
  const [choice, setChoice] = useState<Record<number, number>>({})

  async function act(label: string, fn: (p: Parameters<Parameters<typeof ws.run>[1]>[0]) => Promise<ScheduleRequest>) {
    const r = await ws.run(label, fn)
    if (r) {
      list.reload()
      void ws.refresh()
    }
  }

  const requests = list.data ?? []

  return (
    <>
      <PageHeader
        eyebrow="Faculty"
        title="My requests"
        description="Every unavailability you reported, with the option you chose and the coordinator's decision."
        actions={
          <Button icon="alert" onClick={() => ws.navigate('faculty/report')}>
            New request
          </Button>
        }
      />

      {list.error && <Callout tone="red">{list.error}</Callout>}
      {list.loading && !list.data ? (
        <div className="flex items-center gap-2 text-sm text-slate-500">
          <Spinner /> Loading…
        </div>
      ) : requests.length === 0 ? (
        <EmptyState
          title="No requests yet"
          body="When you report unavailability, it appears here with its status."
          icon="inbox"
        />
      ) : (
        <div className="space-y-3">
          {requests.map((r) => {
            const expanded = open === r.id
            const editable = r.status === 'DRAFT' || r.status === 'STALE'
            const pick = choice[r.id] ?? r.selected_rank ?? r.options[0]?.rank ?? null
            return (
              <Card
                key={r.id}
                title={`#${r.id} · ${r.window_label}`}
                subtitle={`Reported ${formatDateTime(r.created_at)}${r.reason ? ` · ${r.reason}` : ''}`}
                actions={
                  <>
                    {r.stale && OPEN.includes(r.status) && (
                      <span className="text-[11px] font-medium text-move-700">
                        timetable changed since
                      </span>
                    )}
                    <RequestStatusBadge status={r.status} />
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setOpen(expanded ? null : r.id)}
                    >
                      {expanded ? 'Hide' : 'Details'}
                    </Button>
                  </>
                }
              >
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-500">
                  <span>{r.affected.length} affected class{r.affected.length === 1 ? '' : 'es'}</span>
                  <span>{r.options.length} option{r.options.length === 1 ? '' : 's'}</span>
                  {r.selected_rank !== null && (
                    <span>
                      chose option {r.selected_rank}
                      {r.auto_selected ? ' (auto-selected)' : ''}
                    </span>
                  )}
                  {r.decided_at && (
                    <span>
                      decided {formatDateTime(r.decided_at)} by {r.decided_by}
                    </span>
                  )}
                </div>
                {r.decision_note && (
                  <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-700">
                    “{r.decision_note}”
                  </p>
                )}

                {expanded && (
                  <div className="mt-4 space-y-4 border-t border-slate-100 pt-4">
                    <AffectedList items={r.affected} />
                    {r.failure && <FailureNotice failure={r.failure} />}
                    {r.options.length > 0 && (
                      <OptionCards
                        options={r.options}
                        selected={pick}
                        onSelect={
                          editable ? (rank) => setChoice((c) => ({ ...c, [r.id]: rank })) : undefined
                        }
                        chosen={r.selected_rank}
                        auto={r.auto_selected}
                      />
                    )}
                    <div className="flex flex-wrap gap-2">
                      {r.status === 'DRAFT' && r.options.length > 0 && (
                        <>
                          <Button
                            variant="success"
                            size="sm"
                            onClick={() =>
                              void act('Auto-selecting the best option', () =>
                                api.submitRequest(r.id, { auto: true }),
                              )
                            }
                          >
                            Auto-select best &amp; submit
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={pick === null}
                            onClick={() =>
                              void act('Submitting', () =>
                                api.submitRequest(r.id, { rank: pick ?? undefined }),
                              )
                            }
                          >
                            Submit option {pick ?? ''}
                          </Button>
                        </>
                      )}
                      {editable && (
                        <Button
                          variant="secondary"
                          size="sm"
                          icon="refresh"
                          onClick={() =>
                            void act('Recomputing options', (p) => api.recompute(r.id, p))
                          }
                        >
                          Recompute against today's timetable
                        </Button>
                      )}
                      {(r.status === 'DRAFT' ||
                        r.status === 'SUBMITTED' ||
                        r.status === 'STALE') && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            void act('Withdrawing', () => api.withdrawRequest(r.id))
                          }
                        >
                          Withdraw
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </>
  )
}
