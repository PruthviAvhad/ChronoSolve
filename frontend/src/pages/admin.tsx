// Coordinator (ADMIN) pages: the router and the dashboard. Every number shown
// is fetched from the API, which computes it from stored data or solver output.

import { api } from '../api'
import { StatusPill } from '../Panels'
import { Button, Callout, Card, Metric, PageHeader } from '../ui'
import { longDate, useWorkspace } from '../workspace'
import { LifecycleBadge, Loading, MiniStat, VersionTable, round1 } from './admin-common'
import { ConstraintsPage } from './admin-rules'
import { AnalyticsPage, RequestsPage, VersionsPage } from './admin-review'
import { DisruptionsPage, GeneratePage, TimetablesPage } from './admin-schedule'
import { FacultyAdmin, RoomsAdmin, StructurePage, SubjectsAdmin } from './admin-setup'
import { RequestStatusBadge, useLoad } from './shared'

export function AdminPage({ path, params }: { path: string; params: URLSearchParams }) {
  switch (path) {
    case 'admin/structure':
      return <StructurePage />
    case 'admin/faculty':
      return <FacultyAdmin />
    case 'admin/rooms':
      return <RoomsAdmin />
    case 'admin/subjects':
      return <SubjectsAdmin />
    case 'admin/constraints':
      return <ConstraintsPage />
    case 'admin/generate':
      return <GeneratePage />
    case 'admin/timetables':
      return <TimetablesPage params={params} />
    case 'admin/disruptions':
      return <DisruptionsPage />
    case 'admin/requests':
      return <RequestsPage focus={params.get('id')} />
    case 'admin/analytics':
      return <AnalyticsPage />
    case 'admin/versions':
      return <VersionsPage />
    default:
      return <Dashboard />
  }
}

function Dashboard() {
  const ws = useWorkspace()
  const { data, error } = useLoad(
    () => api.dashboard(),
    [ws.state?.version?.id, ws.state?.proposal?.id, ws.state?.pending_requests],
  )

  if (error) return <Callout tone="red">{error}</Callout>
  if (!data) return <Loading label="Loading the dashboard…" />
  const q = data.quality

  return (
    <>
      <PageHeader
        eyebrow={`Today · ${longDate(data.today)}`}
        title="Coordinator dashboard"
        description="The published timetable, what is waiting for you, and what is in force today. Every figure is recomputed from the database when this page loads."
        actions={
          <>
            <Button
              variant="secondary"
              icon="alert"
              onClick={() => ws.navigate('admin/disruptions')}
            >
              Simulate a disruption
            </Button>
            <Button icon="sparkles" onClick={() => ws.navigate('admin/generate')}>
              Generate &amp; optimise
            </Button>
          </>
        }
      />

      {data.proposal && (
        <div className="mb-5">
          <Callout tone="amber" title={`A proposal is waiting: version ${data.proposal.number}`}>
            {data.proposal.reason || data.proposal.label}. Nothing is live until you publish it —{' '}
            <button
              type="button"
              className="font-semibold underline"
              onClick={() => ws.navigate('admin/timetables?source=pending')}
            >
              review it
            </button>
            .
          </Callout>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-6">
        <Metric
          label="Published version"
          value={`v${data.version.number}`}
          hint={data.version.label}
          tone="info"
        />
        <Metric
          label="Solver status"
          value={<StatusPill status={data.solver.status} />}
          hint={`objective ${data.solver.objective ?? '—'} · bound ${data.solver.best_bound ?? '—'}`}
        />
        <Metric
          label="Hard-rule violations"
          value={data.violations}
          hint={`across ${data.families} independently verified rule families`}
          tone={data.violations ? 'bad' : 'good'}
        />
        <Metric
          label="Requests awaiting you"
          value={data.pending_count}
          tone={data.pending_count ? 'warn' : 'neutral'}
        />
        <Metric label="Overrides in force or upcoming" value={data.overrides.length} />
        <Metric label="Locked sessions" value={data.locked} />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-2">
        <Card
          title="Teacher requests awaiting approval"
          actions={
            <Button variant="ghost" size="sm" onClick={() => ws.navigate('admin/requests')}>
              Open queue
            </Button>
          }
        >
          {data.pending_requests.length === 0 ? (
            <p className="text-sm text-slate-500">Nothing is waiting for a decision.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.pending_requests.map((r) => {
                const chosen = r.options.find((o) => o.rank === r.selected_rank)
                return (
                  <li key={r.id} className="flex items-center gap-3 py-2.5">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-slate-800">
                        {r.faculty_name} · {r.window_label}
                      </div>
                      <div className="truncate text-xs text-slate-500">
                        {chosen
                          ? `option ${chosen.rank}: ${chosen.changed} moved, ${chosen.retention_pct.toFixed(1)}% retained`
                          : 'no option chosen'}
                        {r.reason ? ` · ${r.reason}` : ''}
                      </div>
                    </div>
                    <RequestStatusBadge status={r.status} />
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={() => ws.navigate(`admin/requests?id=${r.id}`)}
                    >
                      Review
                    </Button>
                  </li>
                )
              })}
            </ul>
          )}
        </Card>

        <Card
          title="Temporary overrides"
          actions={
            <Button variant="ghost" size="sm" onClick={() => ws.navigate('admin/constraints')}>
              Manage
            </Button>
          }
        >
          {data.overrides.length === 0 ? (
            <p className="text-sm text-slate-500">No temporary override is in force or upcoming.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.overrides.map((o) => (
                <li key={o.id} className="flex items-start justify-between gap-3 py-2.5">
                  <span className="text-sm text-slate-700">{o.summary}</span>
                  <LifecycleBadge value={o.lifecycle} />
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Schedule quality" subtitle="Measured on the published timetable">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <MiniStat label="Student idle hours" value={q.student_idle_hours} />
            <MiniStat label="Faculty idle hours" value={q.faculty_idle_hours} />
            <MiniStat label="Room use" value={`${round1(q.room_utilisation_pct)}%`} />
            <MiniStat label="Seat efficiency" value={`${round1(q.seat_efficiency_pct ?? 0)}%`} />
            <MiniStat label="Last-period classes" value={q.last_slot_sessions} />
            <MiniStat label="Load spread" value={`${q.faculty_load_spread}h`} />
          </div>
        </Card>

        <Card
          title="Institution"
          actions={
            <Button variant="ghost" size="sm" onClick={() => ws.navigate('admin/structure')}>
              Structure
            </Button>
          }
        >
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <MiniStat label="Years" value={data.structure.years} />
            <MiniStat label="Divisions" value={data.structure.divisions} />
            <MiniStat label="Faculty" value={data.structure.faculty} />
            <MiniStat label="Lecture rooms" value={data.structure.rooms} />
            <MiniStat label="Labs" value={data.structure.labs} />
            <MiniStat label="Sessions / week" value={data.structure.sessions} />
          </div>
        </Card>
      </div>

      <div className="mt-5">
        <Card
          title="Recent versions"
          actions={
            <Button variant="ghost" size="sm" onClick={() => ws.navigate('admin/versions')}>
              All versions
            </Button>
          }
        >
          <VersionTable versions={data.versions} />
        </Card>
      </div>
    </>
  )
}
