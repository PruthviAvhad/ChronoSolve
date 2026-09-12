// Scheduling: generate and optimise, browse and hand-edit timetables (locks
// and validated moves), and the disruption what-if -- the hero workflow.

import { useEffect, useState, type FormEvent } from 'react'

import {
  api,
  type DisruptionInput,
  type Explanation,
  type ImportResult,
  type MoveCheck,
  type SolveStage,
  type Source,
  type Weights,
  type WhatIf,
} from '../api'
import { CHECK_LABELS, ProofPanel, RepairPanel, StatusPill } from '../Panels'
import { Badge, Button, Callout, Card, Field, Input, PageHeader, Select } from '../ui'
import { DAY_NAMES, useWorkspace } from '../workspace'
import { RepairOutcome, TimetableExplorer, round1, useRepairActions } from './admin-common'
import { NlAssistant, parsedWhatIf } from './admin-rules'
import { useLoad } from './shared'

// ---------------------------------------------------------------------------
// Generate & optimise
// ---------------------------------------------------------------------------

const WEIGHT_FIELDS: { key: keyof Weights; label: string; help: string }[] = [
  { key: 'student_gaps', label: 'Student compactness', help: 'idle hours between a division’s classes' },
  { key: 'faculty_gaps', label: 'Faculty comfort', help: 'idle hours inside a teacher’s day' },
  { key: 'faculty_load_balance', label: 'Daily load balance', help: 'teaching hours above the daily target' },
  { key: 'subject_spread', label: 'Subject spread', help: 'the same subject twice on one day' },
  { key: 'last_slot', label: 'Avoid last period', help: 'classes in the final period' },
  { key: 'room_wastage', label: 'Room fit', help: 'empty seats, counted per 10' },
  { key: 'faculty_preferences', label: 'Teacher preferences', help: 'classes in a preferred-off period' },
]

export function GeneratePage() {
  const ws = useWorkspace()
  const state = ws.state
  const [weights, setWeights] = useState<Weights | null>(state?.weights ?? null)
  const [timeLimit, setTimeLimit] = useState(20)
  const [stages, setStages] = useState<SolveStage[]>([])
  const [result, setResult] = useState<WhatIf | null>(null)
  const [imported, setImported] = useState<ImportResult | null>(null)
  const { apply, discard } = useRepairActions(() => setStages([]))

  if (!state || !weights) return null
  const proposal = state.proposal ?? null

  async function save() {
    if (!weights) return
    const w = await ws.run('Saving weights', () => api.saveWeights(weights))
    if (w) {
      ws.notify('Weights saved. They apply to the next solve.', 'success')
      void ws.refresh()
    }
  }

  async function generate() {
    if (!weights) return
    const s = await ws.run('Generating', (p) => api.generate(weights, timeLimit, p))
    if (s) {
      setStages(s.stages)
      setResult(null)
      await ws.refresh()
      ws.notify(
        `Proposal ready as version ${s.proposal?.number ?? ''}. Review it, then publish or discard.`,
        'success',
      )
    }
  }

  async function reoptimise() {
    const r = await ws.run('Re-optimising', (p) => api.reoptimize(p))
    if (r) {
      setResult(r)
      void ws.refresh()
    }
  }

  async function importWorkbook(file: File) {
    const r = await ws.run('Importing', () => api.importWorkbook(file))
    if (!r) return
    setImported(r)
    if (r.accepted) {
      await ws.refresh()
      ws.notify('Workbook imported, solved and published as a new version.', 'success')
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Scheduling"
        title="Generate & optimise"
        description="Solve the whole institution from scratch, or re-optimise the published timetable with the fewest possible moves. Both produce a proposal; nothing is published until you say so."
      />

      {result && (
        <div className="mb-6">
          <RepairOutcome result={result} onClose={() => setResult(null)} />
        </div>
      )}

      {proposal && !result && (
        <div className="mb-6">
          <Card
            title={`Proposal waiting: version ${proposal.number}`}
            subtitle={proposal.reason || proposal.label}
            actions={<StatusPill status={proposal.solver_status} />}
          >
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-slate-600">
              <span>
                objective <b className="text-slate-900 tabular-nums">{proposal.objective ?? '—'}</b>
              </span>
              {proposal.changed_count !== null && (
                <span>
                  <b className="text-slate-900 tabular-nums">{proposal.changed_count}</b> sessions differ
                  from the published version
                </span>
              )}
              {proposal.retention_pct !== null && (
                <span>
                  <b className="text-slate-900 tabular-nums">{round1(proposal.retention_pct)}%</b> retained
                </span>
              )}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="success" icon="check" onClick={() => void apply()}>
                Publish proposal
              </Button>
              <Button variant="secondary" onClick={() => ws.navigate('admin/timetables?source=pending')}>
                Review in Timetables
              </Button>
              <Button variant="ghost" onClick={() => void discard()}>
                Discard
              </Button>
            </div>
          </Card>
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Card title="Objective weights" subtitle="How much each soft goal counts. Hard rules are never traded off.">
          <div className="space-y-4">
            {WEIGHT_FIELDS.map((f) => (
              <label key={f.key} className="block">
                <div className="mb-1 flex items-baseline justify-between text-xs">
                  <span className="font-medium text-slate-700">{f.label}</span>
                  <span className="text-slate-900 tabular-nums">{weights[f.key] ?? 0}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={20}
                  aria-label={f.label}
                  value={weights[f.key] ?? 0}
                  onChange={(e) => setWeights({ ...weights, [f.key]: Number(e.target.value) })}
                  className="w-full accent-[var(--color-accent-500)]"
                />
                <div className="text-[11px] text-slate-400">{f.help}</div>
              </label>
            ))}
            <div className="grid grid-cols-2 gap-3">
              <Field label="Daily target (hours)" hint="Load above this is penalised">
                <Input
                  type="number"
                  min={1}
                  max={8}
                  value={weights.faculty_daily_target}
                  onChange={(e) => setWeights({ ...weights, faculty_daily_target: Number(e.target.value) })}
                />
              </Field>
              <Field label="Search time limit (s)" hint="CP-SAT stops here and reports its status">
                <Input
                  type="number"
                  min={5}
                  max={120}
                  value={timeLimit}
                  onChange={(e) => setTimeLimit(Number(e.target.value))}
                />
              </Field>
            </div>
          </div>
          <div className="mt-5 flex flex-wrap gap-2">
            <Button icon="sparkles" onClick={() => void generate()} disabled={ws.busy !== null}>
              Generate timetable
            </Button>
            <Button variant="secondary" icon="refresh" onClick={() => void reoptimise()} disabled={ws.busy !== null}>
              Re-optimise published
            </Button>
            <Button variant="ghost" onClick={() => void save()} disabled={ws.busy !== null}>
              Save weights
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-slate-400">
            Generate solves from scratch and keeps the result as a proposal. Re-optimise keeps as much
            of the published week as possible.
          </p>
        </Card>

        <Card title="What the solver optimises">
          <div className="space-y-4 text-sm text-slate-600">
            <div>
              <div className="mb-1 font-medium text-slate-800">Objective (minimised)</div>
              <div className="rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-relaxed text-slate-700">
                {WEIGHT_FIELDS.map((f, i) => (
                  <div key={f.key}>
                    {i === 0 ? '  ' : '+ '}
                    {weights[f.key] ?? 0} × {f.help}
                  </div>
                ))}
              </div>
            </div>
            <div>
              <div className="mb-1 font-medium text-slate-800">Hard rules (never violated)</div>
              <div className="flex flex-wrap gap-1">
                {Object.values(CHECK_LABELS).map((label) => (
                  <Badge key={label}>{label}</Badge>
                ))}
              </div>
              <p className="mt-1 text-[11px] text-slate-400">
                Each is re-checked by an independent validator on every version.
              </p>
            </div>
            <div>
              <div className="mb-1 font-medium text-slate-800">Minimum-disruption repair</div>
              <p className="text-[13px]">
                Phase 1 minimises <span className="font-mono text-xs">(n + 1) × time moves + room moves</span>,
                so no number of room changes can outweigh one moved class; OPTIMAL proves no smaller
                repair exists. Phase 2 then improves the objective above without exceeding that move
                budget.
              </p>
            </div>
            <div>
              <div className="mb-1 font-medium text-slate-800">Room allocation</div>
              <p className="text-[13px]">
                A session may only use an active room of the right type with enough seats and the
                equipment it needs. The three tightest rooms that fit are offered to the solver, and
                empty seats count against the room-fit weight.
              </p>
            </div>
          </div>
        </Card>
      </div>

      {stages.length > 0 && (
        <div className="mt-5">
          <Card title="What the solver did" subtitle="Each stage with its measured duration">
            <ol className="space-y-2">
              {stages.map((s) => (
                <li key={s.key} className="flex gap-3 text-sm">
                  <span className="mt-0.5 text-keep-600">✓</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex justify-between gap-3">
                      <span className="font-medium text-slate-800">{s.label}</span>
                      <span className="text-xs text-slate-400 tabular-nums">{s.seconds.toFixed(2)}s</span>
                    </div>
                    {s.detail && <p className="text-xs text-slate-500">{s.detail}</p>}
                  </div>
                </li>
              ))}
            </ol>
          </Card>
        </div>
      )}

      <div className="mt-5 grid gap-5 xl:grid-cols-[380px_minmax(0,1fr)]">
        <Card
          title="Import from a spreadsheet"
          actions={
            <a
              href="/api/import/template"
              className="text-xs font-medium text-accent-600 hover:text-accent-700"
            >
              Download template
            </a>
          }
        >
          <label className="block cursor-pointer rounded-xl border border-dashed border-slate-300 px-3 py-6 text-center transition hover:border-accent-400 hover:bg-accent-50/40">
            <input
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                e.target.value = ''
                if (file) void importWorkbook(file)
              }}
            />
            <span className="text-sm font-medium text-slate-700">Choose a workbook</span>
            <span className="mt-0.5 block text-[11px] text-slate-500">Rooms · Faculty · Batches · Subjects</span>
          </label>
          {imported &&
            (imported.accepted ? (
              <div className="mt-3">
                <Callout tone="green" title="Imported and solved">
                  {Object.entries(imported.counts)
                    .map(([k, v]) => `${v} ${k}`)
                    .join(' · ')}
                </Callout>
              </div>
            ) : (
              <div className="mt-3">
                <Callout tone="red" title="Nothing was imported">
                  <ul className="space-y-1">
                    {imported.errors.slice(0, 6).map((e, i) => (
                      <li key={i}>
                        <b>
                          {e.sheet}
                          {e.row ? ` row ${e.row}` : ''}:
                        </b>{' '}
                        {e.message}
                      </li>
                    ))}
                  </ul>
                  {imported.errors.length > 6 && <p>+{imported.errors.length - 6} more</p>}
                </Callout>
              </div>
            ))}
          {imported && imported.warnings.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {imported.warnings.map((w, i) => (
                <li key={i} className="text-[11px] text-move-700">
                  {w.message}
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
            A workbook is applied only if every sheet validates; it then replaces the configuration
            and is solved and published as a new version (earlier versions are kept).
          </p>
        </Card>

        <ProofPanel
          title="Published timetable — technical proof"
          solver={state.published}
          validation={state.validation}
          quality={state.quality}
        />
      </div>
    </>
  )
}

// ---------------------------------------------------------------------------
// Timetables: browse, lock, move
// ---------------------------------------------------------------------------

function SessionActions({
  ex,
  source,
  onDone,
  onPreview,
}: {
  ex: Explanation
  source: Source
  onDone: () => void
  onPreview: (r: WhatIf) => void
}) {
  const ws = useWorkspace()
  const [target, setTarget] = useState('')
  const [check, setCheck] = useState<MoveCheck | null>(null)

  if (source !== 'published') {
    return (
      <p className="text-xs text-slate-500">
        Locks and moves apply to the published timetable. Switch to it to use them.
      </p>
    )
  }

  async function lock() {
    const r = await ws.run('Locking', () => api.lock(ex.session_id, true))
    if (r) {
      ws.notify(`Locked ${ex.subject_code} ${ex.batch_id} at ${ex.current_label}. Every later solve keeps it there.`, 'success')
      void ws.refresh()
      onDone()
    }
  }

  async function unlock() {
    const r = await ws.run('Unlocking', () => api.unlock(ex.session_id))
    if (r) {
      ws.notify('Unlocked. The solver may move this session again.', 'success')
      void ws.refresh()
      onDone()
    }
  }

  async function runCheck() {
    if (!target) return
    const c = await ws.run('Checking the move', () => api.moveCheck(ex.session_id, Number(target)))
    if (c) setCheck(c)
  }

  async function preview() {
    if (!target) return
    const r = await ws.run('Re-optimising around the move', (p) =>
      api.movePreview(ex.session_id, Number(target), null, p),
    )
    if (r) {
      onPreview(r)
      void ws.refresh()
    }
  }

  const slots = ex.options.filter((o) => o.label !== ex.current_label)

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {ex.locked ? (
          <Button variant="secondary" size="sm" icon="unlock" onClick={() => void unlock()}>
            Unlock
          </Button>
        ) : (
          <Button variant="secondary" size="sm" icon="lock" onClick={() => void lock()}>
            Lock in place
          </Button>
        )}
        <span className="text-[11px] text-slate-500">
          {ex.locked
            ? 'Locked: the solver must keep this placement.'
            : 'A lock is a hard constraint in every later solve.'}
        </span>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <Field label="Move to">
          <Select
            value={target}
            onChange={(e) => {
              setTarget(e.target.value)
              setCheck(null)
            }}
            className="min-w-[220px]"
          >
            <option value="">Choose a start time…</option>
            {slots.map((o) => (
              <option key={o.timeslot_id} value={o.timeslot_id}>
                {o.label}
                {o.feasible ? ' — free' : ' — needs other moves'}
              </option>
            ))}
          </Select>
        </Field>
        <Button variant="secondary" size="sm" disabled={!target} onClick={() => void runCheck()}>
          Check move
        </Button>
        <Button size="sm" disabled={!target || (check !== null && !check.allowed)} onClick={() => void preview()}>
          Preview move
        </Button>
      </div>
      {check &&
        (check.allowed ? (
          <Callout
            tone={check.resolvable.length ? 'amber' : 'green'}
            title={check.resolvable.length ? 'Allowed, with knock-on moves' : 'Free to move'}
          >
            {check.resolvable.length ? (
              <ul className="space-y-0.5">
                {check.resolvable.map((b) => (
                  <li key={b.message}>{b.message}</li>
                ))}
              </ul>
            ) : (
              <>
                Nothing blocks {check.target_label}
                {check.room_id ? ` in ${check.room_id}` : ''}.
              </>
            )}{' '}
            The preview locks it there and re-optimises the rest with the fewest moves.
          </Callout>
        ) : (
          <Callout tone="red" title="Not allowed">
            {check.hard.map((b) => (
              <div key={b.message}>{b.message}</div>
            ))}
          </Callout>
        ))}
    </div>
  )
}

export function TimetablesPage({ params }: { params: URLSearchParams }) {
  const ws = useWorkspace()
  const versionParam = params.get('version')
  const versionId = versionParam ? Number(versionParam) : null
  const [source, setSource] = useState<Source>(params.get('source') === 'pending' ? 'pending' : 'published')
  const [result, setResult] = useState<WhatIf | null>(null)
  const [tick, setTick] = useState(0)
  const hasPending = Boolean(ws.state?.has_pending) || Boolean(result?.feasible)

  useEffect(() => {
    if (!hasPending && source === 'pending') setSource('published')
  }, [hasPending, source])

  return (
    <>
      <PageHeader
        eyebrow="Scheduling"
        title="Timetables"
        description="Any division, teacher or room. Click a session to see why it sits there, lock it in place, or move it — a move is validated first, then the rest is re-optimised around it as a proposal."
      />
      {versionId !== null && (
        <div className="mb-5">
          <Callout tone="blue" title="Viewing a past version (read-only)">
            <button type="button" className="font-semibold underline" onClick={() => ws.navigate('admin/timetables')}>
              Back to the published timetable
            </button>
          </Callout>
        </div>
      )}
      {result && (
        <div className="mb-6">
          <RepairOutcome
            result={result}
            showGrid={false}
            onClose={() => {
              setResult(null)
              setSource('published')
              setTick((t) => t + 1)
            }}
          />
        </div>
      )}
      <TimetableExplorer
        source={versionId !== null ? 'published' : source}
        onSource={versionId !== null ? undefined : setSource}
        pendingAvailable={hasPending}
        versionId={versionId}
        reloadKey={`${tick}-${ws.state?.version?.id}-${ws.state?.proposal?.id}-${ws.state?.locked_sessions}`}
        focus={result?.changes[0]?.batch_id ?? null}
        explainFooter={
          versionId !== null
            ? undefined
            : (ex, done) => (
                <SessionActions
                  ex={ex}
                  source={source}
                  onDone={() => {
                    done()
                    setTick((t) => t + 1)
                  }}
                  onPreview={(r) => {
                    setResult(r)
                    if (r.feasible) setSource('pending')
                  }}
                />
              )
        }
      />
    </>
  )
}

// ---------------------------------------------------------------------------
// Disruptions & what-if
// ---------------------------------------------------------------------------

export function DisruptionsPage() {
  const ws = useWorkspace()
  const scenarios = useLoad(() => api.scenarios(), [])
  const entities = useLoad(() => api.entities(), [])
  const [result, setResult] = useState<WhatIf | null>(null)
  const [source, setSource] = useState<Source>('published')
  const [custom, setCustom] = useState<DisruptionInput>({
    kind: 'FACULTY_UNAVAILABLE',
    target: '',
    day: 4,
    start_time: '12:00',
    end_time: '17:00',
  })
  const hasPending = Boolean(ws.state?.has_pending) || Boolean(result?.feasible)
  const { apply, discard } = useRepairActions(() => {
    setResult(null)
    setSource('published')
  })

  const lists = entities.data
  const targets = !lists
    ? []
    : custom.kind === 'ROOM_UNAVAILABLE'
      ? lists.rooms
      : custom.kind === 'BATCH_UNAVAILABLE'
        ? lists.batches
        : lists.faculty

  useEffect(() => {
    if (!targets.some((t) => t.id === custom.target) && targets[0]) {
      setCustom((c) => ({ ...c, target: targets[0].id }))
    }
  }, [targets, custom.target])

  function absorb(r: WhatIf) {
    setResult(r)
    if (r.feasible) setSource('pending')
    void ws.refresh()
  }

  async function runScenario(key: string) {
    const r = await ws.run('Re-optimising', (p) => api.whatIfScenario(key, undefined, p))
    if (r) absorb(r)
  }

  async function runCustom(e: FormEvent) {
    e.preventDefault()
    const r = await ws.run('Re-optimising', (p) => api.whatIfCustom([custom], undefined, p))
    if (r) absorb(r)
  }

  async function reoptimise() {
    const r = await ws.run('Re-optimising', (p) => api.reoptimize(p))
    if (r) absorb(r)
  }

  const focus = result?.changes[0]?.batch_id ?? result?.directly_affected[0]?.batch_id ?? null
  const state = ws.state

  return (
    <>
      <PageHeader
        eyebrow="Scheduling"
        title="Disruptions & what-if"
        description="Simulate a change and see the smallest repair CP-SAT can prove. What-if never changes the published timetable — publishing is a separate, explicit step."
        actions={
          <Button variant="secondary" icon="refresh" onClick={() => void reoptimise()}>
            Re-optimise under today's rules
          </Button>
        }
      />

      <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-5">
          <Card title="Simulate a disruption">
            <div className="space-y-2">
              {(scenarios.data ?? []).map((s) => (
                <button
                  key={s.key}
                  type="button"
                  onClick={() => void runScenario(s.key)}
                  disabled={ws.busy !== null}
                  className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-left transition hover:border-accent-400 hover:bg-accent-50/40 disabled:opacity-50"
                >
                  <div className="flex items-start gap-1.5">
                    {s.kind !== 'availability' && (
                      <span className="mt-0.5 shrink-0 rounded bg-room-50 px-1 py-px text-[9px] font-semibold tracking-wider text-room-700 uppercase">
                        rule
                      </span>
                    )}
                    <span className="text-[13px] font-medium text-slate-800">{s.story}</span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-slate-500">
                    {s.descriptions.slice(0, 2).join(' · ')}
                    {s.descriptions.length > 2 && ` · +${s.descriptions.length - 2} more`}
                  </div>
                </button>
              ))}
            </div>
            <p className="mt-3 text-[11px] text-slate-500">What-if never changes the published timetable.</p>
          </Card>

          <Card title="Custom disruption">
            <form onSubmit={runCustom} className="space-y-3">
              <Field label="What is unavailable">
                <Select
                  value={custom.kind}
                  onChange={(e) =>
                    setCustom({ ...custom, kind: e.target.value as DisruptionInput['kind'], target: '' })
                  }
                >
                  <option value="FACULTY_UNAVAILABLE">A teacher</option>
                  <option value="ROOM_UNAVAILABLE">A room or lab</option>
                  <option value="BATCH_UNAVAILABLE">A division</option>
                </Select>
              </Field>
              <Field label="Which">
                <Select value={custom.target} onChange={(e) => setCustom({ ...custom, target: e.target.value })}>
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </Select>
              </Field>
              <div className="grid grid-cols-3 gap-2">
                <Field label="Day">
                  <Select value={custom.day} onChange={(e) => setCustom({ ...custom, day: Number(e.target.value) })}>
                    {DAY_NAMES.map((d, i) => (
                      <option key={d} value={i}>
                        {d.slice(0, 3)}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="From">
                  <Input
                    type="time"
                    value={custom.start_time}
                    onChange={(e) => setCustom({ ...custom, start_time: e.target.value })}
                  />
                </Field>
                <Field label="To">
                  <Input
                    type="time"
                    value={custom.end_time}
                    onChange={(e) => setCustom({ ...custom, end_time: e.target.value })}
                  />
                </Field>
              </div>
              <Button type="submit" className="w-full" disabled={ws.busy !== null || !custom.target}>
                Run what-if
              </Button>
            </form>
          </Card>

          <NlAssistant
            onWhatIf={(p) =>
              void ws.run('Re-optimising', (progress) => parsedWhatIf(p, progress)).then((r) => {
                if (r) absorb(r)
              })
            }
          />
        </div>

        <div className="min-w-0 space-y-5">
          {result ? (
            <RepairPanel
              result={result}
              onApply={() => void apply()}
              onDiscard={() => void discard()}
              busy={ws.busy !== null}
            />
          ) : (
            <Callout tone="blue" title="Pick a disruption to see its repair">
              The solver finds the repair that moves the fewest classes, proves it minimal when it can,
              and shows every change with the rule that forced it. You decide whether to publish.
            </Callout>
          )}

          <TimetableExplorer
            source={source}
            onSource={setSource}
            pendingAvailable={hasPending}
            focus={focus}
            reloadKey={result}
          />

          {result?.feasible && result.solver && result.validation && result.quality ? (
            <ProofPanel
              title="Repaired timetable — technical proof"
              solver={result.solver}
              validation={result.validation}
              quality={result.quality}
            />
          ) : (
            state && (
              <ProofPanel
                title="Published timetable — technical proof"
                solver={state.published}
                validation={state.validation}
                quality={state.quality}
              />
            )
          )}
        </div>
      </div>
    </>
  )
}
