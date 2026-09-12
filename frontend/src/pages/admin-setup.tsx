// Institution setup: the academic hierarchy, faculty, rooms and labs, and
// subjects. These edit the *base configuration* only; the published timetable
// never changes here. Any rule it now breaks shows on the dashboard, and
// re-optimising repairs it with the fewest moves.

import { useMemo, useState, type FormEvent } from 'react'

import {
  api,
  type Calendar,
  type FacultyInfo,
  type RoomInfo,
  type StructureBatch,
  type SubjectInfo,
} from '../api'
import {
  Badge,
  Button,
  Callout,
  Card,
  EmptyState,
  Field,
  Input,
  Metric,
  Modal,
  PageHeader,
  Select,
  Tabs,
  cx,
} from '../ui'
import { useWorkspace } from '../workspace'
import { Bar, Loading, round1 } from './admin-common'
import { useLoad } from './shared'

const SAVED_NOTE =
  'Saved to the base configuration. The published timetable is unchanged — re-optimise to repair it around the new rules with the fewest moves.'

// ---------------------------------------------------------------------------
// Slot editor
// ---------------------------------------------------------------------------

type Mark = 'off' | 'pref'

/** Click a period to cycle its mark. Lunch is protected and cannot be marked. */
function SlotEditor({
  calendar,
  marks,
  cycle,
  onChange,
}: {
  calendar: Calendar
  marks: Record<number, Mark>
  cycle: Mark[]
  onChange: (next: Record<number, Mark>) => void
}) {
  const periods = calendar.period_start.length
  function toggle(slot: number) {
    const order: (Mark | undefined)[] = [undefined, ...cycle]
    const next = order[(order.indexOf(marks[slot]) + 1) % order.length]
    const copy = { ...marks }
    if (next) copy[slot] = next
    else delete copy[slot]
    onChange(copy)
  }
  return (
    <div className="overflow-x-auto">
      <div
        className="grid min-w-[460px] gap-1"
        style={{ gridTemplateColumns: `52px repeat(${calendar.days.length}, minmax(0,1fr))` }}
      >
        <div />
        {calendar.days.map((d) => (
          <div key={d} className="text-center text-[10px] font-semibold text-slate-500 uppercase">
            {d}
          </div>
        ))}
        {calendar.period_start.map((start, p) => (
          <div key={start} className="contents">
            <div className="pr-1 text-right text-[10px] leading-7 text-slate-400">{start}</div>
            {calendar.days.map((day, d) => {
              if (p === calendar.lunch_period) {
                return <div key={day} className="h-7 rounded border border-dashed border-slate-200" />
              }
              const slot = d * periods + p
              const mark = marks[slot]
              return (
                <button
                  key={day}
                  type="button"
                  aria-label={`${day} ${start}`}
                  aria-pressed={Boolean(mark)}
                  onClick={() => toggle(slot)}
                  className={cx(
                    'h-7 rounded border text-[10px] font-medium transition',
                    mark === 'off'
                      ? 'border-alarm-200 bg-alarm-50 text-alarm-700'
                      : mark === 'pref'
                        ? 'border-move-200 bg-move-50 text-move-700'
                        : 'border-slate-200 bg-white text-slate-300 hover:border-accent-400',
                  )}
                >
                  {mark === 'off' ? 'off' : mark === 'pref' ? 'pref' : '·'}
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

function toMarks(off: number[], pref: number[] = []): Record<number, Mark> {
  const m: Record<number, Mark> = {}
  for (const s of pref) m[s] = 'pref'
  for (const s of off) m[s] = 'off'
  return m
}

function slotsWith(marks: Record<number, Mark>, mark: Mark): number[] {
  return Object.entries(marks)
    .filter(([, v]) => v === mark)
    .map(([k]) => Number(k))
    .sort((a, b) => a - b)
}

// ---------------------------------------------------------------------------
// Academic structure
// ---------------------------------------------------------------------------

interface NewDivision {
  id: string
  name: string
  strength: number
  department: string
  program: string
  year: number
  year_label: string
  semester: number
}

export function StructurePage() {
  const ws = useWorkspace()
  const tree = useLoad(() => api.structure(), [])
  const [editing, setEditing] = useState<StructureBatch | null>(null)
  const [adding, setAdding] = useState<NewDivision | null>(null)

  const data = tree.data
  const divisions = useMemo(
    () =>
      data?.departments.flatMap((d) =>
        d.programs.flatMap((p) => p.years.flatMap((y) => y.semesters.flatMap((s) => s.batches))),
      ) ?? [],
    [data],
  )

  async function saveEdit(e: FormEvent) {
    e.preventDefault()
    if (!editing) return
    const r = await ws.run('Saving', () =>
      api.patchBatch(editing.id, { name: editing.name, strength: editing.strength }),
    )
    if (r) {
      setEditing(null)
      tree.reload()
      void ws.refresh()
      ws.notify(SAVED_NOTE, 'success')
    }
  }

  async function saveNew(e: FormEvent) {
    e.preventDefault()
    if (!adding) return
    const r = await ws.run('Adding the division', () => api.createBatch(adding))
    if (r) {
      setAdding(null)
      tree.reload()
      void ws.refresh()
      ws.notify(`Division ${r.id} added. Give it subjects, then generate or re-optimise.`, 'success')
    }
  }

  function startAdding() {
    const dept = data?.departments[0]
    setAdding({
      id: '',
      name: '',
      strength: 60,
      department: dept?.name ?? '',
      program: dept?.programs[0]?.name ?? '',
      year: 1,
      year_label: 'First Year',
      semester: 1,
    })
  }

  return (
    <>
      <PageHeader
        eyebrow="Institution"
        title="Academic structure"
        description="Department → programme → year → semester → division. Every division is scheduled in one CP-SAT model, so faculty and rooms shared across years can never be double-booked."
        actions={
          <Button icon="layers" onClick={startAdding} disabled={!data}>
            Add division
          </Button>
        }
      />
      {tree.error && <Callout tone="red">{tree.error}</Callout>}
      {!data ? (
        !tree.error && <Loading />
      ) : (
        <>
          <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            <Metric label="Departments" value={data.departments.length} />
            <Metric label="Divisions" value={divisions.length} />
            <Metric label="Faculty" value={data.faculty} />
            <Metric label="Rooms & labs" value={data.rooms} hint={`${data.labs} labs`} />
            <Metric label="Teachers shared across years" value={data.shared_faculty} tone="info" />
            <Metric label="Rooms shared across years" value={data.shared_rooms} tone="info" />
          </div>

          <div className="space-y-5">
            {data.departments.map((dept) => (
              <Card key={dept.name} title={dept.name}>
                <div className="space-y-6">
                  {dept.programs.map((prog) => (
                    <div key={prog.name}>
                      <h3 className="mb-3 text-xs font-semibold tracking-wide text-slate-500 uppercase">
                        {prog.name}
                      </h3>
                      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
                        {prog.years.map((year) => (
                          <div
                            key={year.number}
                            className="rounded-xl border border-slate-200 bg-slate-50/60 p-3"
                          >
                            <div className="mb-2 flex items-baseline justify-between">
                              <span className="text-sm font-semibold text-slate-800">
                                {year.label}
                              </span>
                              <span className="text-[11px] text-slate-400">Year {year.number}</span>
                            </div>
                            {year.semesters.map((sem) => (
                              <div key={sem.number} className="mb-2 last:mb-0">
                                <div className="mb-1 text-[11px] font-medium text-slate-500">
                                  Semester {sem.number}
                                </div>
                                <ul className="space-y-1.5">
                                  {sem.batches.map((b) => (
                                    <li
                                      key={b.id}
                                      className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-2"
                                    >
                                      <div className="min-w-0 flex-1">
                                        <div className="text-sm font-medium text-slate-800">
                                          {b.id}
                                        </div>
                                        <div className="truncate text-[11px] text-slate-500">
                                          {b.strength} students · {b.sessions} sessions ·{' '}
                                          {b.contact_hours}h/week
                                        </div>
                                      </div>
                                      <Button
                                        variant="ghost"
                                        size="sm"
                                        aria-label={`Edit ${b.id}`}
                                        onClick={() => setEditing({ ...b })}
                                      >
                                        Edit
                                      </Button>
                                    </li>
                                  ))}
                                </ul>
                              </div>
                            ))}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </Card>
            ))}
          </div>
        </>
      )}

      <Modal open={editing !== null} title={`Edit division ${editing?.id ?? ''}`} onClose={() => setEditing(null)}>
        {editing && (
          <form onSubmit={saveEdit} className="space-y-4">
            <Field label="Name">
              <Input
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </Field>
            <Field label="Students" hint="Rooms must seat the whole division.">
              <Input
                type="number"
                min={1}
                max={500}
                value={editing.strength}
                onChange={(e) => setEditing({ ...editing, strength: Number(e.target.value) })}
              />
            </Field>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setEditing(null)}>
                Cancel
              </Button>
              <Button type="submit">Save</Button>
            </div>
          </form>
        )}
      </Modal>

      <Modal open={adding !== null} title="Add a division" onClose={() => setAdding(null)} wide>
        {adding && (
          <form onSubmit={saveNew} className="grid gap-4 sm:grid-cols-2">
            <Field label="Division id" hint="Letters, digits, - and _ (e.g. FE-A)">
              <Input
                required
                value={adding.id}
                onChange={(e) => setAdding({ ...adding, id: e.target.value.toUpperCase() })}
              />
            </Field>
            <Field label="Name">
              <Input
                required
                value={adding.name}
                onChange={(e) => setAdding({ ...adding, name: e.target.value })}
              />
            </Field>
            <Field label="Department">
              <Input
                required
                value={adding.department}
                onChange={(e) => setAdding({ ...adding, department: e.target.value })}
              />
            </Field>
            <Field label="Programme">
              <Input
                value={adding.program}
                onChange={(e) => setAdding({ ...adding, program: e.target.value })}
              />
            </Field>
            <Field label="Year">
              <Input
                type="number"
                min={1}
                max={6}
                value={adding.year}
                onChange={(e) => setAdding({ ...adding, year: Number(e.target.value) })}
              />
            </Field>
            <Field label="Year label">
              <Input
                value={adding.year_label}
                onChange={(e) => setAdding({ ...adding, year_label: e.target.value })}
              />
            </Field>
            <Field label="Semester">
              <Input
                type="number"
                min={1}
                max={12}
                value={adding.semester}
                onChange={(e) => setAdding({ ...adding, semester: Number(e.target.value) })}
              />
            </Field>
            <Field label="Students">
              <Input
                type="number"
                min={1}
                max={500}
                value={adding.strength}
                onChange={(e) => setAdding({ ...adding, strength: Number(e.target.value) })}
              />
            </Field>
            <div className="flex justify-end gap-2 sm:col-span-2">
              <Button variant="secondary" onClick={() => setAdding(null)}>
                Cancel
              </Button>
              <Button type="submit">Add division</Button>
            </div>
          </form>
        )}
      </Modal>
    </>
  )
}

// ---------------------------------------------------------------------------
// Faculty
// ---------------------------------------------------------------------------

interface FacultyDraft {
  id: string
  name: string
  max_daily_load: number
  max_weekly_load: number
  max_consecutive: number
  marks: Record<number, Mark>
}

export function FacultyAdmin() {
  const ws = useWorkspace()
  const list = useLoad(() => api.faculty(), [ws.state?.version?.id])
  const [draft, setDraft] = useState<FacultyDraft | null>(null)
  const [filter, setFilter] = useState('')
  const calendar = ws.state?.calendar

  const rows = (list.data ?? []).filter((f) =>
    `${f.name} ${f.id} ${f.department} ${f.subjects.join(' ')}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  )

  function edit(f: FacultyInfo) {
    setDraft({
      id: f.id,
      name: f.name,
      max_daily_load: f.max_daily_load,
      max_weekly_load: f.max_weekly_load,
      max_consecutive: f.max_consecutive,
      marks: toMarks(f.unavailable, f.preferred_off),
    })
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    const r = await ws.run('Saving', async () => {
      await api.patchFaculty(draft.id, {
        name: draft.name,
        max_daily_load: draft.max_daily_load,
        max_weekly_load: draft.max_weekly_load,
        max_consecutive: draft.max_consecutive,
      })
      return api.setAvailability(
        draft.id,
        slotsWith(draft.marks, 'off'),
        slotsWith(draft.marks, 'pref'),
      )
    })
    if (r) {
      setDraft(null)
      list.reload()
      void ws.refresh()
      ws.notify(SAVED_NOTE, 'success')
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Institution"
        title="Faculty"
        description="Workload limits and regular availability are base constraints. Unavailable periods are hard rules; preferred-off periods are soft, weighed by the teacher-preference objective."
      />
      {list.error && <Callout tone="red">{list.error}</Callout>}
      <Card
        title={`${list.data?.length ?? 0} teachers`}
        actions={
          <Input
            aria-label="Filter teachers"
            placeholder="Filter by name, subject…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="w-56 py-1.5 text-xs"
          />
        }
        bodyClassName="p-0"
      >
        {!list.data ? (
          <Loading />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Teacher</th>
                  <th className="px-3 py-2.5 font-medium">Subjects</th>
                  <th className="px-3 py-2.5 font-medium">Weekly load</th>
                  <th className="px-3 py-2.5 font-medium">Busiest day</th>
                  <th className="px-3 py-2.5 font-medium">Max run</th>
                  <th className="px-3 py-2.5 font-medium">Years taught</th>
                  <th className="px-3 py-2.5 font-medium">Availability</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {rows.map((f) => (
                  <tr key={f.id} className="border-t border-slate-100 align-top">
                    <td className="px-5 py-3">
                      <div className="font-medium text-slate-900">{f.name}</div>
                      <div className="text-[11px] text-slate-500">
                        {f.id} · {f.department}
                        {f.username && ` · @${f.username}`}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex max-w-[220px] flex-wrap gap-1">
                        {f.subjects.slice(0, 4).map((s) => (
                          <Badge key={s}>{s}</Badge>
                        ))}
                        {f.subjects.length > 4 && <Badge>+{f.subjects.length - 4}</Badge>}
                      </div>
                    </td>
                    <td className="w-40 px-3 py-3">
                      <div className="mb-1 text-xs text-slate-700 tabular-nums">
                        {f.weekly_hours} / {f.max_weekly_load}h
                      </div>
                      <Bar value={f.weekly_hours} max={f.max_weekly_load} />
                    </td>
                    <td className="w-32 px-3 py-3">
                      <div className="mb-1 text-xs text-slate-700 tabular-nums">
                        {f.busiest_day_hours} / {f.max_daily_load}h
                      </div>
                      <Bar value={f.busiest_day_hours} max={f.max_daily_load} />
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-700 tabular-nums">
                      {f.max_consecutive}h
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-600">
                      {f.years_taught.join(', ') || '—'}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-600">
                      {f.unavailable.length} off · {f.preferred_off.length} preferred off
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button variant="secondary" size="sm" onClick={() => edit(f)}>
                        Edit
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal open={draft !== null} title={`Edit ${draft?.name ?? ''}`} onClose={() => setDraft(null)} wide>
        {draft && calendar && (
          <form onSubmit={save} className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-4">
              <Field label="Name">
                <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>
              <Field label="Max hours / day">
                <Input
                  type="number"
                  min={1}
                  max={8}
                  value={draft.max_daily_load}
                  onChange={(e) => setDraft({ ...draft, max_daily_load: Number(e.target.value) })}
                />
              </Field>
              <Field label="Max hours / week">
                <Input
                  type="number"
                  min={1}
                  max={40}
                  value={draft.max_weekly_load}
                  onChange={(e) => setDraft({ ...draft, max_weekly_load: Number(e.target.value) })}
                />
              </Field>
              <Field label="Max consecutive hours">
                <Input
                  type="number"
                  min={1}
                  max={8}
                  value={draft.max_consecutive}
                  onChange={(e) => setDraft({ ...draft, max_consecutive: Number(e.target.value) })}
                />
              </Field>
            </div>
            <div>
              <div className="mb-2 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="font-medium text-slate-700">Regular availability</span>
                <span>click to cycle:</span>
                <Badge tone="red">off — never scheduled</Badge>
                <Badge tone="amber">pref — avoided if possible</Badge>
              </div>
              <SlotEditor
                calendar={calendar}
                marks={draft.marks}
                cycle={['off', 'pref']}
                onChange={(marks) => setDraft({ ...draft, marks })}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button type="submit">Save</Button>
            </div>
          </form>
        )}
      </Modal>
    </>
  )
}

// ---------------------------------------------------------------------------
// Rooms and labs
// ---------------------------------------------------------------------------

interface RoomDraft {
  id: string
  name: string
  building: string
  capacity: number
  active: boolean
  capabilities: string
  marks: Record<number, Mark>
}

export function RoomsAdmin() {
  const ws = useWorkspace()
  const list = useLoad(() => api.rooms(), [ws.state?.version?.id])
  const [kind, setKind] = useState<'ALL' | 'LECTURE' | 'LAB'>('ALL')
  const [draft, setDraft] = useState<RoomDraft | null>(null)
  const calendar = ws.state?.calendar

  const rows = (list.data ?? []).filter((r) => kind === 'ALL' || r.room_type === kind)

  function edit(r: RoomInfo) {
    setDraft({
      id: r.id,
      name: r.name,
      building: r.building,
      capacity: r.capacity,
      active: r.active,
      capabilities: r.capabilities.join(', '),
      marks: toMarks(r.unavailable),
    })
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    const r = await ws.run('Saving', async () => {
      await api.patchRoom(draft.id, {
        name: draft.name,
        building: draft.building,
        capacity: draft.capacity,
        active: draft.active,
        capabilities: draft.capabilities
          .split(',')
          .map((c) => c.trim().toLowerCase())
          .filter(Boolean),
      })
      return api.setRoomBlocks(draft.id, slotsWith(draft.marks, 'off'))
    })
    if (r) {
      setDraft(null)
      list.reload()
      void ws.refresh()
      ws.notify(SAVED_NOTE, 'success')
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Institution"
        title="Rooms & labs"
        description="A session may only use an active room of the right type, with enough seats and the equipment it needs. Among the rooms that fit, the solver prefers the tightest — empty seats are penalised by the room-fit objective."
      />
      {list.error && <Callout tone="red">{list.error}</Callout>}
      <Card
        title={`${rows.length} rooms`}
        actions={
          <Tabs
            tabs={[
              { value: 'ALL', label: 'All' },
              { value: 'LECTURE', label: 'Lecture rooms' },
              { value: 'LAB', label: 'Labs' },
            ]}
            value={kind}
            onChange={setKind}
          />
        }
        bodyClassName="p-0"
      >
        {!list.data ? (
          <Loading />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Room</th>
                  <th className="px-3 py-2.5 font-medium">Type</th>
                  <th className="px-3 py-2.5 text-right font-medium">Seats</th>
                  <th className="px-3 py-2.5 font-medium">Equipment</th>
                  <th className="px-3 py-2.5 font-medium">Booked (published)</th>
                  <th className="px-3 py-2.5 text-right font-medium">Average fill</th>
                  <th className="px-3 py-2.5 font-medium">Status</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-slate-100 align-top">
                    <td className="px-5 py-3">
                      <div className="font-medium text-slate-900">{r.id}</div>
                      <div className="text-[11px] text-slate-500">
                        {r.name}
                        {r.building && ` · ${r.building}`}
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <Badge tone={r.room_type === 'LAB' ? 'indigo' : 'neutral'}>
                        {r.room_type === 'LAB' ? 'Lab' : 'Lecture'}
                      </Badge>
                    </td>
                    <td className="px-3 py-3 text-right text-slate-700 tabular-nums">{r.capacity}</td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-1">
                        {r.capabilities.length === 0 ? (
                          <span className="text-xs text-slate-400">—</span>
                        ) : (
                          r.capabilities.map((c) => (
                            <Badge key={c} tone="blue">
                              {c}
                            </Badge>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="w-44 px-3 py-3">
                      <div className="mb-1 text-xs text-slate-700 tabular-nums">
                        {r.booked_hours}h · {round1(r.utilisation_pct)}%
                      </div>
                      <Bar value={r.utilisation_pct} max={100} />
                    </td>
                    <td className="px-3 py-3 text-right text-xs text-slate-700 tabular-nums">
                      {r.booked_hours ? `${round1(r.avg_fill_pct)}%` : '—'}
                    </td>
                    <td className="px-3 py-3">
                      {r.active ? (
                        <Badge tone="green">In service</Badge>
                      ) : (
                        <Badge tone="red">Out of service</Badge>
                      )}
                      {r.unavailable.length > 0 && (
                        <div className="mt-1 text-[11px] text-slate-500">
                          {r.unavailable.length} blocked periods
                        </div>
                      )}
                    </td>
                    <td className="px-5 py-3 text-right">
                      <Button variant="secondary" size="sm" onClick={() => edit(r)}>
                        Edit
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal open={draft !== null} title={`Edit ${draft?.id ?? ''}`} onClose={() => setDraft(null)} wide>
        {draft && calendar && (
          <form onSubmit={save} className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Name">
                <Input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </Field>
              <Field label="Building">
                <Input
                  value={draft.building}
                  onChange={(e) => setDraft({ ...draft, building: e.target.value })}
                />
              </Field>
              <Field label="Seats">
                <Input
                  type="number"
                  min={1}
                  max={1000}
                  value={draft.capacity}
                  onChange={(e) => setDraft({ ...draft, capacity: Number(e.target.value) })}
                />
              </Field>
              <Field label="Equipment" hint="Comma-separated, e.g. computer, networking">
                <Input
                  value={draft.capabilities}
                  onChange={(e) => setDraft({ ...draft, capabilities: e.target.value })}
                />
              </Field>
              <label className="flex items-center gap-2 self-end pb-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={draft.active}
                  onChange={(e) => setDraft({ ...draft, active: e.target.checked })}
                  className="h-4 w-4 rounded border-slate-300 accent-[var(--color-accent-500)]"
                />
                In service
              </label>
            </div>
            <div>
              <div className="mb-2 text-xs text-slate-500">
                <span className="font-medium text-slate-700">Regularly blocked periods</span> —
                click to toggle
              </div>
              <SlotEditor
                calendar={calendar}
                marks={draft.marks}
                cycle={['off']}
                onChange={(marks) => setDraft({ ...draft, marks })}
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button type="submit">Save</Button>
            </div>
          </form>
        )}
      </Modal>
    </>
  )
}

// ---------------------------------------------------------------------------
// Subjects
// ---------------------------------------------------------------------------

interface SubjectDraft {
  id: number | null
  batch_id: string
  code: string
  name: string
  faculty_id: string
  category: 'THEORY' | 'TUTORIAL' | 'LAB'
  sessions_per_week: number
  duration: number
  required_capability: string
}

export function SubjectsAdmin() {
  const ws = useWorkspace()
  const [division, setDivision] = useState('')
  const batches = useLoad(() => api.batches(), [])
  const faculty = useLoad(() => api.faculty(), [])
  const list = useLoad(() => api.subjects(division || undefined), [division])
  const [draft, setDraft] = useState<SubjectDraft | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  function blank(): SubjectDraft {
    return {
      id: null,
      batch_id: division || batches.data?.[0]?.id || '',
      code: '',
      name: '',
      faculty_id: faculty.data?.[0]?.id ?? '',
      category: 'THEORY',
      sessions_per_week: 3,
      duration: 1,
      required_capability: '',
    }
  }

  function edit(s: SubjectInfo) {
    setDraft({
      id: s.id,
      batch_id: s.batch_id,
      code: s.code,
      name: s.name,
      faculty_id: s.faculty_id,
      category: (s.category as SubjectDraft['category']) ?? 'THEORY',
      sessions_per_week: s.sessions_per_week,
      duration: s.duration,
      required_capability: s.required_capability ?? '',
    })
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    if (!draft) return
    const capability = draft.required_capability.trim() || null
    const r = await ws.run('Saving', () =>
      draft.id === null
        ? api.createSubject({
            batch_id: draft.batch_id,
            code: draft.code,
            name: draft.name,
            faculty_id: draft.faculty_id,
            category: draft.category,
            sessions_per_week: draft.sessions_per_week,
            duration: draft.duration,
            required_capability: capability,
          })
        : api.patchSubject(draft.id, {
            name: draft.name,
            faculty_id: draft.faculty_id,
            sessions_per_week: draft.sessions_per_week,
            duration: draft.duration,
            required_capability: capability,
          }),
    )
    if (r) {
      setDraft(null)
      list.reload()
      void ws.refresh()
      ws.notify(SAVED_NOTE, 'success')
    }
  }

  async function remove(id: number) {
    const r = await ws.run('Removing', () => api.deleteSubject(id))
    setConfirmDelete(null)
    if (r) {
      list.reload()
      void ws.refresh()
      ws.notify('Subject removed from the base configuration.', 'success')
    }
  }

  const rows = list.data ?? []

  return (
    <>
      <PageHeader
        eyebrow="Institution"
        title="Subjects"
        description="What each division is taught, by whom, how often, and what room it needs. Labs run as contiguous multi-hour blocks in a lab with the required equipment."
        actions={
          <Button icon="book" onClick={() => setDraft(blank())} disabled={!batches.data || !faculty.data}>
            Add subject
          </Button>
        }
      />
      {list.error && <Callout tone="red">{list.error}</Callout>}
      <Card
        title={`${rows.length} subjects`}
        actions={
          <Select
            aria-label="Division"
            value={division}
            onChange={(e) => setDivision(e.target.value)}
            className="w-56 py-1.5 text-xs"
          >
            <option value="">All divisions</option>
            {(batches.data ?? []).map((b) => (
              <option key={b.id} value={b.id}>
                {b.id} — {b.year_label}, Sem {b.semester}
              </option>
            ))}
          </Select>
        }
        bodyClassName="p-0"
      >
        {!list.data ? (
          <Loading />
        ) : rows.length === 0 ? (
          <div className="p-5">
            <EmptyState title="No subjects" body="Add one to start scheduling this division." icon="book" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                <tr>
                  <th className="px-5 py-2.5 font-medium">Subject</th>
                  <th className="px-3 py-2.5 font-medium">Division</th>
                  <th className="px-3 py-2.5 font-medium">Teacher</th>
                  <th className="px-3 py-2.5 font-medium">Type</th>
                  <th className="px-3 py-2.5 text-right font-medium">Per week</th>
                  <th className="px-3 py-2.5 font-medium">Room needs</th>
                  <th className="px-5 py-2.5" />
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.id} className="border-t border-slate-100 align-top">
                    <td className="px-5 py-3">
                      <div className="font-medium text-slate-900">{s.code}</div>
                      <div className="text-[11px] text-slate-500">{s.name}</div>
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-700">{s.batch_id}</td>
                    <td className="px-3 py-3 text-xs text-slate-700">{s.faculty_name}</td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-1">
                        <Badge tone={s.category === 'LAB' ? 'indigo' : 'neutral'}>{s.category}</Badge>
                        {s.elective_key && <Badge tone="amber">elective</Badge>}
                      </div>
                    </td>
                    <td className="px-3 py-3 text-right text-xs text-slate-700 tabular-nums">
                      {s.sessions_per_week} × {s.duration}h
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-600">
                      {s.room_type === 'LAB' ? 'Lab' : 'Lecture room'}
                      {s.required_capability && ` with ${s.required_capability}`}
                      {s.headcount ? ` · ${s.headcount} seats` : ''}
                    </td>
                    <td className="px-5 py-3 text-right whitespace-nowrap">
                      <Button variant="secondary" size="sm" onClick={() => edit(s)}>
                        Edit
                      </Button>{' '}
                      {confirmDelete === s.id ? (
                        <Button variant="danger" size="sm" onClick={() => void remove(s.id)}>
                          Confirm remove
                        </Button>
                      ) : (
                        <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(s.id)}>
                          Remove
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Modal
        open={draft !== null}
        title={draft?.id === null ? 'Add a subject' : `Edit ${draft?.code ?? ''}`}
        onClose={() => setDraft(null)}
        wide
      >
        {draft && (
          <form onSubmit={save} className="grid gap-4 sm:grid-cols-2">
            {draft.id === null && (
              <>
                <Field label="Division">
                  <Select
                    value={draft.batch_id}
                    onChange={(e) => setDraft({ ...draft, batch_id: e.target.value })}
                  >
                    {(batches.data ?? []).map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.id} — {b.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Code">
                  <Input
                    required
                    value={draft.code}
                    onChange={(e) => setDraft({ ...draft, code: e.target.value.toUpperCase() })}
                  />
                </Field>
                <Field label="Type">
                  <Select
                    value={draft.category}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        category: e.target.value as SubjectDraft['category'],
                        duration: e.target.value === 'LAB' ? 2 : 1,
                      })
                    }
                  >
                    <option value="THEORY">Theory</option>
                    <option value="TUTORIAL">Tutorial</option>
                    <option value="LAB">Lab</option>
                  </Select>
                </Field>
              </>
            )}
            <Field label="Name">
              <Input
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </Field>
            <Field label="Teacher">
              <Select
                value={draft.faculty_id}
                onChange={(e) => setDraft({ ...draft, faculty_id: e.target.value })}
              >
                {(faculty.data ?? []).map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Sessions per week">
              <Input
                type="number"
                min={1}
                max={10}
                value={draft.sessions_per_week}
                onChange={(e) => setDraft({ ...draft, sessions_per_week: Number(e.target.value) })}
              />
            </Field>
            <Field label="Hours per session">
              <Input
                type="number"
                min={1}
                max={3}
                value={draft.duration}
                onChange={(e) => setDraft({ ...draft, duration: Number(e.target.value) })}
              />
            </Field>
            <Field label="Required equipment" hint="Optional, e.g. networking">
              <Input
                value={draft.required_capability}
                onChange={(e) => setDraft({ ...draft, required_capability: e.target.value })}
              />
            </Field>
            <div className="flex justify-end gap-2 sm:col-span-2">
              <Button variant="secondary" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button type="submit">{draft.id === null ? 'Add subject' : 'Save'}</Button>
            </div>
          </form>
        )}
      </Modal>
    </>
  )
}
