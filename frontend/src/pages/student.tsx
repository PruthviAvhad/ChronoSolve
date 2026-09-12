import { useEffect, useMemo, useState } from 'react'

import { api, type Grid } from '../api'
import { Callout, Card, Field, PageHeader, Select, Spinner } from '../ui'
import { useWorkspace } from '../workspace'
import { TimetablePanel, TodayClasses, useLoad } from './shared'

/** Year -> semester -> division, then that division's published week. */
export function StudentPage() {
  const { user, state } = useWorkspace()
  const { data: batches, error } = useLoad(() => api.batches(), [])
  const [division, setDivision] = useState('')
  const [grid, setGrid] = useState<Grid | null>(null)

  // Start on the student's own division when the account names one.
  useEffect(() => {
    if (!batches?.length || division) return
    setDivision((batches.find((b) => b.id === user.batch_id) ?? batches[0]).id)
  }, [batches, division, user.batch_id])

  const all = useMemo(() => batches ?? [], [batches])
  const current = all.find((b) => b.id === division) ?? null

  const years = useMemo(() => {
    const seen = new Map<number, string>()
    for (const b of all) if (!seen.has(b.year)) seen.set(b.year, b.year_label || `Year ${b.year}`)
    return [...seen.entries()].sort((a, b) => a[0] - b[0])
  }, [all])

  const semesters = useMemo(
    () =>
      [...new Set(all.filter((b) => b.year === current?.year).map((b) => b.semester))].sort(
        (a, b) => a - b,
      ),
    [all, current?.year],
  )

  const divisions = all.filter(
    (b) => b.year === current?.year && b.semester === current?.semester,
  )

  function pickYear(year: number) {
    const first = all
      .filter((b) => b.year === year)
      .sort((a, b) => a.semester - b.semester || a.id.localeCompare(b.id))[0]
    if (first) setDivision(first.id)
  }

  function pickSemester(semester: number) {
    const first = all.find((b) => b.year === current?.year && b.semester === semester)
    if (first) setDivision(first.id)
  }

  return (
    <>
      <PageHeader
        eyebrow="Student"
        title="Class timetable"
        description="The published timetable for a division. It changes only when the coordinator publishes a new version — never while a change is being previewed."
      />

      {error && <Callout tone="red">{error}</Callout>}

      {!batches ? (
        !error && (
          <div className="flex items-center gap-2 text-sm text-slate-500">
            <Spinner /> Loading divisions…
          </div>
        )
      ) : (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-5">
            <Card title="Choose your class" bodyClassName="grid gap-3 p-5 sm:grid-cols-3">
              <Field label="Year">
                <Select
                  value={current?.year ?? ''}
                  onChange={(e) => pickYear(Number(e.target.value))}
                >
                  {years.map(([year, label]) => (
                    <option key={year} value={year}>
                      {label}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Semester">
                <Select
                  value={current?.semester ?? ''}
                  onChange={(e) => pickSemester(Number(e.target.value))}
                >
                  {semesters.map((s) => (
                    <option key={s} value={s}>
                      Semester {s}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Division">
                <Select value={division} onChange={(e) => setDivision(e.target.value)}>
                  {divisions.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.id} — {b.name}
                    </option>
                  ))}
                </Select>
              </Field>
            </Card>

            {division && (
              <TimetablePanel view="batch" id={division} onGrid={setGrid} exportLabel="this timetable" />
            )}
          </div>

          <div className="space-y-5">
            <TodayClasses grid={grid} today={state?.today} who="batch" />
            {current && (
              <Card title="Division">
                <dl className="grid grid-cols-2 gap-y-2 text-sm">
                  <dt className="text-slate-500">Programme</dt>
                  <dd className="text-right text-slate-800">{current.program || current.department}</dd>
                  <dt className="text-slate-500">Year</dt>
                  <dd className="text-right text-slate-800">{current.year_label}</dd>
                  <dt className="text-slate-500">Semester</dt>
                  <dd className="text-right text-slate-800">{current.semester}</dd>
                  <dt className="text-slate-500">Students</dt>
                  <dd className="text-right text-slate-800 tabular-nums">{current.strength}</dd>
                  <dt className="text-slate-500">Contact hours / week</dt>
                  <dd className="text-right text-slate-800 tabular-nums">{current.contact_hours}</dd>
                </dl>
                {state?.version && (
                  <p className="mt-4 text-xs text-slate-500">
                    Showing published version {state.version.number}.
                  </p>
                )}
              </Card>
            )}
          </div>
        </div>
      )}
    </>
  )
}
