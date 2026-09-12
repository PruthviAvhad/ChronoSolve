// Constraints: permanent base rules, dated temporary overrides and locks, plus
// the natural-language assistant that proposes a structured rule for a person
// to confirm. Saving a rule never rewrites the published timetable.

import { useEffect, useState, type FormEvent } from 'react'

import {
  api,
  type Constraint,
  type ConstraintInput,
  type ParsedRule,
  type RuleChangeInput,
  type SolveProgress,
  type WhatIf,
} from '../api'
import { Badge, Button, Callout, Card, EmptyState, Field, Input, PageHeader, Select, Tabs, cx } from '../ui'
import { DAY_NAMES, formatDateTime, useWorkspace } from '../workspace'
import { LifecycleBadge, Loading, RepairOutcome, RuleStatusBadge } from './admin-common'
import { useLoad } from './shared'

/** Run the what-if a parsed sentence describes: an availability block, a
 * workload rule, or a lock at a chosen time (a move, repaired around). */
export function parsedWhatIf(
  parsed: ParsedRule,
  onProgress: (p: SolveProgress) => void,
): Promise<WhatIf> {
  if (parsed.category === 'rule' && parsed.rule_changes?.length) {
    return api.whatIfCustom(
      [],
      undefined,
      onProgress,
      parsed.rule_changes.map((r) => ({
        rule: r.rule as RuleChangeInput['rule'],
        value: r.value,
        faculty: r.faculty,
      })),
    )
  }
  if (parsed.category === 'lock' && parsed.locks?.length) {
    const lock = parsed.locks[0]
    return api.movePreview(lock.session_id, lock.timeslot_id, lock.room_id, onProgress)
  }
  return api.whatIfCustom(parsed.disruptions, undefined, onProgress)
}

const EXAMPLES = [
  'Prof. Mehta is unavailable after 2 PM on Friday',
  'Computer Lab 2 is unavailable tomorrow',
  'TE-A should not have lectures after 4 PM',
  'Maximum three consecutive lectures for faculty',
  'Lock DBMS for TE-A on Monday at 10 AM',
]

export function NlAssistant({
  onWhatIf,
  onSave,
}: {
  onWhatIf: (p: ParsedRule) => void
  onSave?: (p: ParsedRule) => void
}) {
  const ws = useWorkspace()
  const [text, setText] = useState('')
  const [parsed, setParsed] = useState<ParsedRule | null>(null)

  async function read(sentence = text) {
    if (!sentence.trim()) return
    const p = await ws.run('Reading', () => api.parse(sentence))
    if (p) setParsed(p)
  }

  const category = parsed?.category ?? 'availability'
  const structured =
    category === 'rule' ? parsed?.rule_changes : category === 'lock' ? parsed?.locks : parsed?.disruptions

  return (
    <Card title="Describe a change in words" subtitle="Proposes a structured rule for you to confirm">
      <textarea
        aria-label="Describe a change"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            void read()
          }
        }}
        rows={2}
        placeholder="Prof. Mehta is unavailable after 2 PM on Friday"
        className="w-full resize-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm outline-none placeholder:text-slate-400 focus:border-accent-400 focus:ring-2 focus:ring-accent-100"
      />
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => void read()}
          disabled={ws.busy !== null || !text.trim()}
        >
          Read as a rule
        </Button>
        {parsed && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setParsed(null)
              setText('')
            }}
          >
            Clear
          </Button>
        )}
      </div>

      {!parsed && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => {
                setText(ex)
                void read(ex)
              }}
              className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600 transition hover:border-accent-400 hover:text-accent-700"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {parsed && (
        <div className="mt-3">
          {parsed.understood ? (
            <>
              <div className="rounded-lg border border-accent-200 bg-accent-50 p-3">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  <span className="text-[10px] font-semibold tracking-wider text-accent-700 uppercase">
                    Proposed rule — confirm before solving
                  </span>
                  <Badge tone={parsed.scope === 'TEMPORARY' ? 'amber' : 'neutral'}>
                    {parsed.scope === 'TEMPORARY'
                      ? `Temporary · ${parsed.start_date}${parsed.end_date && parsed.end_date !== parsed.start_date ? ` → ${parsed.end_date}` : ''}`
                      : 'Permanent'}
                  </Badge>
                  <Badge tone="blue">{category}</Badge>
                </div>
                <p className="text-sm text-slate-800">{parsed.summary}</p>
              </div>
              <pre className="mt-2 max-h-48 overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-2 text-[10px] leading-relaxed text-slate-600">
                {JSON.stringify(structured ?? [], null, 2)}
              </pre>
              {parsed.assumptions.length > 0 && (
                <ul className="mt-2 space-y-0.5">
                  {parsed.assumptions.map((a) => (
                    <li key={a} className="text-[11px] text-move-700">
                      assumed: {a}
                    </li>
                  ))}
                </ul>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" onClick={() => onWhatIf(parsed)} disabled={ws.busy !== null}>
                  {category === 'lock' ? 'Preview lock & repair' : 'Confirm & run what-if'}
                </Button>
                {onSave && category !== 'lock' && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => onSave(parsed)}
                    disabled={ws.busy !== null}
                  >
                    Save as constraint
                  </Button>
                )}
              </div>
            </>
          ) : (
            <Callout tone="red" title="Not understood">
              <ul className="space-y-1">
                {parsed.issues.map((i) => (
                  <li key={i}>{i}</li>
                ))}
              </ul>
            </Callout>
          )}
        </div>
      )}

      <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
        A local rule-based parser, not a language model — it runs offline and costs nothing. It
        only proposes a constraint; CP-SAT still produces the timetable.
      </p>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// Adding a rule by form
// ---------------------------------------------------------------------------

type Kind = ConstraintInput['kind']
type RuleField = NonNullable<ConstraintInput['rule_field']>

interface RuleForm {
  category: 'BASE' | 'TEMPORARY'
  kind: Kind
  target_id: string
  days: number[]
  start_time: string
  end_time: string
  start_date: string
  end_date: string
  rule_field: RuleField
  rule_value: number
  reason: string
}

const KIND_LABELS: Record<Kind, string> = {
  FACULTY_UNAVAILABLE: 'Teacher unavailable',
  ROOM_UNAVAILABLE: 'Room out of use',
  BATCH_UNAVAILABLE: 'Division has no classes',
  RULE: 'Workload rule',
}

const FIELD_LABELS: Record<RuleField, string> = {
  max_consecutive: 'Max consecutive hours',
  max_daily_load: 'Max hours per day',
  max_weekly_load: 'Max hours per week',
}

function ConstraintForm({ onCreate }: { onCreate: (input: ConstraintInput) => Promise<boolean> }) {
  const ws = useWorkspace()
  const entities = useLoad(() => api.entities(), [])
  const today = ws.state?.today ?? ''
  const [f, setF] = useState<RuleForm>({
    category: 'TEMPORARY',
    kind: 'FACULTY_UNAVAILABLE',
    target_id: '',
    days: [0, 1, 2, 3, 4],
    start_time: '09:00',
    end_time: '17:00',
    start_date: today,
    end_date: today,
    rule_field: 'max_consecutive',
    rule_value: 3,
    reason: '',
  })

  const lists = entities.data
  const targets = !lists
    ? []
    : f.kind === 'ROOM_UNAVAILABLE'
      ? lists.rooms
      : f.kind === 'BATCH_UNAVAILABLE'
        ? lists.batches
        : lists.faculty

  // Keep the target valid for the kind of rule chosen.
  useEffect(() => {
    if (f.kind === 'RULE') return
    if (!targets.some((t) => t.id === f.target_id) && targets[0]) {
      setF((cur) => ({ ...cur, target_id: targets[0].id }))
    }
  }, [f.kind, f.target_id, targets])

  async function submit(e: FormEvent) {
    e.preventDefault()
    const input: ConstraintInput = {
      category: f.category,
      kind: f.kind,
      reason: f.reason,
      ...(f.kind === 'RULE'
        ? { target_id: f.target_id || null, rule_field: f.rule_field, rule_value: f.rule_value }
        : {
            target_id: f.target_id,
            start_time: f.start_time,
            end_time: f.end_time,
            days: f.category === 'BASE' ? f.days : undefined,
          }),
      ...(f.category === 'TEMPORARY' ? { start_date: f.start_date, end_date: f.end_date } : {}),
    }
    if (await onCreate(input)) setF((cur) => ({ ...cur, reason: '' }))
  }

  return (
    <Card title="Add a constraint">
      <form onSubmit={submit} className="space-y-3">
        <Tabs
          tabs={[
            { value: 'TEMPORARY', label: 'Temporary override' },
            { value: 'BASE', label: 'Permanent rule' },
          ]}
          value={f.category}
          onChange={(category) => setF({ ...f, category })}
        />
        <Field label="Type">
          <Select
            value={f.kind}
            onChange={(e) => {
              const kind = e.target.value as Kind
              setF({ ...f, kind, target_id: kind === 'RULE' ? '' : f.target_id })
            }}
          >
            {(Object.keys(KIND_LABELS) as Kind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABELS[k]}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={f.kind === 'RULE' ? 'Applies to' : 'Who or what'}>
          <Select value={f.target_id} onChange={(e) => setF({ ...f, target_id: e.target.value })}>
            {f.kind === 'RULE' && <option value="">Every teacher</option>}
            {targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
                {t.detail && f.kind !== 'FACULTY_UNAVAILABLE' && f.kind !== 'RULE' ? ` — ${t.detail}` : ''}
              </option>
            ))}
          </Select>
        </Field>

        {f.kind === 'RULE' ? (
          <div className="grid grid-cols-[1fr_90px] gap-2">
            <Field label="Rule">
              <Select
                value={f.rule_field}
                onChange={(e) => setF({ ...f, rule_field: e.target.value as RuleField })}
              >
                {(Object.keys(FIELD_LABELS) as RuleField[]).map((k) => (
                  <option key={k} value={k}>
                    {FIELD_LABELS[k]}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Value">
              <Input
                type="number"
                min={1}
                max={40}
                value={f.rule_value}
                onChange={(e) => setF({ ...f, rule_value: Number(e.target.value) })}
              />
            </Field>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            <Field label="From">
              <Input
                type="time"
                value={f.start_time}
                onChange={(e) => setF({ ...f, start_time: e.target.value })}
              />
            </Field>
            <Field label="To">
              <Input
                type="time"
                value={f.end_time}
                onChange={(e) => setF({ ...f, end_time: e.target.value })}
              />
            </Field>
          </div>
        )}

        {f.category === 'TEMPORARY' ? (
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start date">
              <Input
                type="date"
                required
                value={f.start_date}
                onChange={(e) => setF({ ...f, start_date: e.target.value })}
              />
            </Field>
            <Field label="End date">
              <Input
                type="date"
                required
                min={f.start_date}
                value={f.end_date}
                onChange={(e) => setF({ ...f, end_date: e.target.value })}
              />
            </Field>
          </div>
        ) : (
          f.kind !== 'RULE' && (
            <fieldset>
              <legend className="mb-1 text-xs font-medium text-slate-600">Every week on</legend>
              <div className="flex flex-wrap gap-1.5">
                {DAY_NAMES.map((name, d) => {
                  const on = f.days.includes(d)
                  return (
                    <button
                      key={name}
                      type="button"
                      aria-pressed={on}
                      onClick={() =>
                        setF({
                          ...f,
                          days: on ? f.days.filter((x) => x !== d) : [...f.days, d].sort(),
                        })
                      }
                      className={cx(
                        'rounded-md border px-2 py-1 text-xs font-medium transition',
                        on
                          ? 'border-accent-400 bg-accent-50 text-accent-700'
                          : 'border-slate-200 text-slate-500',
                      )}
                    >
                      {name.slice(0, 3)}
                    </button>
                  )
                })}
              </div>
            </fieldset>
          )
        )}

        <Field label="Reason">
          <Input
            value={f.reason}
            maxLength={300}
            placeholder="e.g. External examination duty"
            onChange={(e) => setF({ ...f, reason: e.target.value })}
          />
        </Field>
        <Button type="submit" className="w-full" disabled={ws.busy !== null}>
          Save constraint
        </Button>
        <p className="text-[11px] text-slate-400">
          Saving records the rule. The published timetable changes only after you re-optimise and
          publish.
        </p>
      </form>
    </Card>
  )
}

// ---------------------------------------------------------------------------
// The page
// ---------------------------------------------------------------------------

function when(r: Constraint): string {
  if (r.kind === 'LOCK') return 'every week'
  const window =
    r.kind === 'RULE'
      ? ''
      : r.start_time === '09:00' && r.end_time === '23:59'
        ? ', all day'
        : `, ${r.start_time}–${r.end_time}`
  if (r.category === 'TEMPORARY') {
    const dates = r.start_date === r.end_date ? r.start_date : `${r.start_date} → ${r.end_date}`
    return `${dates}${window}`
  }
  const days = r.days.length === 5 ? 'every weekday' : r.days.map((d) => DAY_NAMES[d]?.slice(0, 3)).join(', ')
  return `${days}${window}`
}

type Tab = 'TEMPORARY' | 'BASE' | 'LOCK'

export function ConstraintsPage() {
  const ws = useWorkspace()
  const list = useLoad(() => api.constraints(), [ws.state?.version?.id])
  const [tab, setTab] = useState<Tab>('TEMPORARY')
  const [result, setResult] = useState<WhatIf | null>(null)
  const [saved, setSaved] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null)

  const records = list.data ?? []
  const groups: Record<Tab, Constraint[]> = {
    TEMPORARY: records.filter((r) => r.category === 'TEMPORARY'),
    BASE: records.filter((r) => r.category === 'BASE' && r.kind !== 'LOCK'),
    LOCK: records.filter((r) => r.kind === 'LOCK'),
  }
  const shown = groups[tab]

  async function changed(message: string) {
    list.reload()
    await ws.refresh()
    ws.notify(message, 'success')
    setSaved(true)
  }

  async function create(input: ConstraintInput): Promise<boolean> {
    const out = await ws.run('Saving the rule', () => api.createConstraint(input))
    if (!out) return false
    setTab(out.kind === 'LOCK' ? 'LOCK' : (out.category as Tab))
    await changed(`Saved: ${out.summary}.`)
    return true
  }

  async function saveParsed(p: ParsedRule) {
    const scope = p.scope ?? 'BASE'
    const dated =
      scope === 'TEMPORARY' ? { start_date: p.start_date ?? null, end_date: p.end_date ?? null } : {}
    const reason = p.text.slice(0, 300)
    if (p.category === 'rule') {
      const rules = p.rule_changes ?? []
      const out = await ws.run('Saving the rule', async () => {
        let last: Constraint | null = null
        for (const rc of rules) {
          last = await api.createConstraint({
            category: scope,
            kind: 'RULE',
            target_id: rc.faculty,
            rule_field: rc.rule as RuleField,
            rule_value: rc.value,
            reason,
            ...dated,
          })
        }
        return last
      })
      if (out) await changed(`Saved: ${out.summary}.`)
      return
    }
    await create({
      category: scope,
      kind: p.kind as Kind,
      target_id: p.target_id,
      days: p.days,
      start_time: p.start_time,
      end_time: p.end_time,
      reason,
      ...dated,
    })
  }

  async function whatIf(p: ParsedRule) {
    const w = await ws.run('Re-optimising', (progress) => parsedWhatIf(p, progress))
    if (w) {
      setResult(w)
      void ws.refresh()
    }
  }

  async function reoptimise() {
    const w = await ws.run('Re-optimising', (p) => api.reoptimize(p))
    if (w) {
      setResult(w)
      setSaved(false)
      void ws.refresh()
    }
  }

  async function setStatus(r: Constraint, status: 'ACTIVE' | 'INACTIVE') {
    const out = await ws.run('Saving', () => api.setConstraintStatus(r.id, status))
    if (out) {
      await changed(
        status === 'ACTIVE'
          ? 'Rule active again.'
          : 'Rule switched off. Nothing reverts on its own — re-optimise, or preview a restore.',
      )
    }
  }

  async function remove(r: Constraint) {
    const out = await ws.run('Removing', () => api.deleteConstraint(r.id))
    setConfirmDelete(null)
    if (out) await changed('Rule removed.')
  }

  async function unlock(r: Constraint) {
    if (!r.session_id) return
    const sid = r.session_id
    const out = await ws.run('Unlocking', () => api.unlock(sid))
    if (out) await changed('Unlocked. The solver may move this session again.')
  }

  async function restore(r: Constraint) {
    const w = await ws.run('Previewing the restore', () => api.restorePreview(r.id))
    if (w) {
      setResult(w)
      void ws.refresh()
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Institution"
        title="Constraints"
        description="Permanent rules hold every week until edited. Temporary overrides carry dates and a reason; after their end date they stop applying, but the timetable never reverts on its own — preview the restore and publish it."
        actions={
          <Button variant="secondary" icon="refresh" onClick={() => void reoptimise()}>
            Re-optimise under today's rules
          </Button>
        }
      />

      {result && (
        <div className="mb-6">
          <RepairOutcome result={result} onClose={() => setResult(null)} />
        </div>
      )}

      {saved && !result && (
        <div className="mb-5">
          <Callout tone="blue" title="The published timetable has not changed">
            New and edited rules apply to the next solve. Re-optimise to repair the published
            timetable around them with the fewest moves, then review and publish.{' '}
            <button type="button" className="font-semibold underline" onClick={() => void reoptimise()}>
              Re-optimise now
            </button>
          </Callout>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Card
          title="Rules on record"
          actions={
            <Tabs
              tabs={[
                { value: 'TEMPORARY', label: `Temporary (${groups.TEMPORARY.length})` },
                { value: 'BASE', label: `Permanent (${groups.BASE.length})` },
                { value: 'LOCK', label: `Locks (${groups.LOCK.length})` },
              ]}
              value={tab}
              onChange={setTab}
            />
          }
          bodyClassName="p-0"
        >
          {list.error ? (
            <div className="p-5">
              <Callout tone="red">{list.error}</Callout>
            </div>
          ) : !list.data ? (
            <Loading />
          ) : shown.length === 0 ? (
            <div className="p-5">
              <EmptyState
                title={
                  tab === 'LOCK'
                    ? 'Nothing is locked'
                    : tab === 'BASE'
                      ? 'No permanent rules beyond the configuration'
                      : 'No temporary overrides'
                }
                body={
                  tab === 'LOCK'
                    ? 'Lock a session from Timetables: click it, then Lock in place.'
                    : 'Add one with the form, or describe it in words.'
                }
                icon="rules"
              />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-left text-[11px] text-slate-500">
                  <tr>
                    <th className="px-5 py-2.5 font-medium">Rule</th>
                    <th className="px-3 py-2.5 font-medium">When</th>
                    <th className="px-3 py-2.5 font-medium">State</th>
                    <th className="px-3 py-2.5 font-medium">Recorded</th>
                    <th className="px-5 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.id} className="border-t border-slate-100 align-top">
                      <td className="px-5 py-3">
                        <div className="text-slate-800">{r.summary}</div>
                        {r.reason && <div className="mt-0.5 text-[11px] text-slate-500">{r.reason}</div>}
                        {r.request_id !== null && (
                          <div className="mt-0.5 text-[11px] text-accent-600">
                            from teacher request #{r.request_id}
                          </div>
                        )}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-600">{when(r)}</td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-1">
                          <LifecycleBadge value={r.lifecycle} />
                          <RuleStatusBadge value={r.status} />
                        </div>
                      </td>
                      <td className="px-3 py-3 text-[11px] whitespace-nowrap text-slate-500">
                        {formatDateTime(r.created_at)}
                        <div>{r.created_by}</div>
                      </td>
                      <td className="px-5 py-3 text-right whitespace-nowrap">
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {r.kind === 'LOCK' ? (
                            r.status === 'ACTIVE' && (
                              <Button variant="secondary" size="sm" icon="unlock" onClick={() => void unlock(r)}>
                                Unlock
                              </Button>
                            )
                          ) : (
                            <>
                              {r.category === 'TEMPORARY' && r.lifecycle === 'EXPIRED' && (
                                <Button size="sm" onClick={() => void restore(r)}>
                                  Preview restore
                                </Button>
                              )}
                              {r.status === 'ACTIVE' ? (
                                <Button variant="secondary" size="sm" onClick={() => void setStatus(r, 'INACTIVE')}>
                                  Switch off
                                </Button>
                              ) : r.status === 'INACTIVE' ? (
                                <Button variant="secondary" size="sm" onClick={() => void setStatus(r, 'ACTIVE')}>
                                  Switch on
                                </Button>
                              ) : null}
                              {r.request_id === null &&
                                (confirmDelete === r.id ? (
                                  <Button variant="danger" size="sm" onClick={() => void remove(r)}>
                                    Confirm delete
                                  </Button>
                                ) : (
                                  <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(r.id)}>
                                    Delete
                                  </Button>
                                ))}
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <div className="space-y-5">
          <ConstraintForm onCreate={create} />
          <NlAssistant onWhatIf={(p) => void whatIf(p)} onSave={(p) => void saveParsed(p)} />
        </div>
      </div>
    </>
  )
}
