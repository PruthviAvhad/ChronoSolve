import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

import {
  api,
  ApiError,
  type AppState,
  type DemoAccount,
  type Role,
  type SolveProgress,
  type User,
} from './api'
import { SolveProgressPanel, StatusPill } from './Panels'
import { AdminPage } from './pages/admin'
import { FacultyPage } from './pages/faculty'
import { StudentPage } from './pages/student'
import { Badge, Button, Field, Icon, Input, Spinner, cx, type IconName } from './ui'
import {
  WorkspaceProvider,
  longDate,
  useHashRoute,
  type Notice,
  type Workspace,
} from './workspace'

interface NavItem {
  route: string
  label: string
  icon: IconName
  group?: string
  badge?: (s: AppState | null) => number
}

/** Menus by role: each role sees only what it is allowed to do. */
const NAV: Record<Role, NavItem[]> = {
  ADMIN: [
    { route: 'admin/dashboard', label: 'Dashboard', icon: 'dashboard', group: 'Overview' },
    { route: 'admin/structure', label: 'Academic structure', icon: 'layers', group: 'Institution' },
    { route: 'admin/faculty', label: 'Faculty', icon: 'users', group: 'Institution' },
    { route: 'admin/rooms', label: 'Rooms & labs', icon: 'building', group: 'Institution' },
    { route: 'admin/subjects', label: 'Subjects', icon: 'book', group: 'Institution' },
    { route: 'admin/constraints', label: 'Constraints', icon: 'rules', group: 'Institution' },
    { route: 'admin/generate', label: 'Generate & optimise', icon: 'sparkles', group: 'Scheduling' },
    { route: 'admin/timetables', label: 'Timetables', icon: 'calendar', group: 'Scheduling' },
    { route: 'admin/disruptions', label: 'Disruptions & what-if', icon: 'alert', group: 'Scheduling' },
    {
      route: 'admin/requests',
      label: 'Requests',
      icon: 'inbox',
      group: 'Scheduling',
      badge: (s) => s?.pending_requests ?? 0,
    },
    { route: 'admin/analytics', label: 'Analytics', icon: 'chart', group: 'Insight' },
    { route: 'admin/versions', label: 'Versions & exports', icon: 'history', group: 'Insight' },
  ],
  FACULTY: [
    { route: 'faculty/timetable', label: 'My timetable', icon: 'calendar' },
    { route: 'faculty/report', label: 'Report unavailability', icon: 'alert' },
    { route: 'faculty/requests', label: 'My requests', icon: 'inbox' },
  ],
  STUDENT: [{ route: 'student/timetable', label: 'Class timetable', icon: 'calendar' }],
}

export const ROLE_LABEL: Record<Role, string> = {
  ADMIN: 'Coordinator',
  FACULTY: 'Faculty',
  STUDENT: 'Student',
}

type Phase =
  | { kind: 'loading' }
  | { kind: 'offline'; message: string }
  | { kind: 'signed-out' }
  | { kind: 'signed-in'; user: User }

export default function App() {
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    api.me().then(
      (user) => !cancelled && setPhase({ kind: 'signed-in', user }),
      (e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 401) setPhase({ kind: 'signed-out' })
        else setPhase({ kind: 'offline', message: e instanceof Error ? e.message : String(e) })
      },
    )
    return () => {
      cancelled = true
    }
  }, [])

  const signedOut = useCallback(() => setPhase({ kind: 'signed-out' }), [])

  if (phase.kind === 'loading') {
    return (
      <div className="flex h-full items-center justify-center gap-3 text-sm text-slate-500">
        <Spinner /> Loading ChronoSolve…
      </div>
    )
  }
  if (phase.kind === 'offline') return <Offline message={phase.message} />
  if (phase.kind === 'signed-out') {
    return <SignIn onSignedIn={(user) => setPhase({ kind: 'signed-in', user })} />
  }
  return <Shell key={phase.user.id} user={phase.user} onSignedOut={signedOut} />
}

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-500 text-white shadow-sm">
        <Icon name="calendar" className="h-4 w-4" />
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-slate-900">
          Chrono<span className="text-accent-600">Solve</span>
        </div>
        {!compact && (
          <div className="text-[10px] font-medium text-slate-400">Academic scheduling</div>
        )}
      </div>
    </div>
  )
}

function Offline({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="max-w-md rounded-2xl border border-slate-200 bg-white p-7 text-center shadow-sm">
        <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-alarm-50 text-alarm-600">
          <Icon name="alert" />
        </div>
        <p className="mb-1 font-semibold text-slate-900">Cannot reach the API</p>
        <p className="text-sm text-slate-500">{message}</p>
        <p className="mt-4 text-xs text-slate-500">Start it with:</p>
        <code className="mt-1 inline-block rounded-md bg-slate-100 px-2 py-1 text-xs text-slate-700">
          uvicorn backend.app.api:app --port 8000
        </code>
        <div className="mt-5">
          <Button variant="secondary" icon="refresh" onClick={() => window.location.reload()}>
            Try again
          </Button>
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// Sign in
// --------------------------------------------------------------------------

const DEMO_BUTTONS: { role: Role; label: string; hint: string; icon: IconName }[] = [
  {
    role: 'ADMIN',
    label: 'Coordinator',
    hint: 'Generate, repair, approve and publish',
    icon: 'dashboard',
  },
  {
    role: 'FACULTY',
    label: 'Faculty',
    hint: 'Prof. Mehta — report unavailability',
    icon: 'user',
  },
  { role: 'STUDENT', label: 'Student', hint: 'View a class timetable', icon: 'book' },
]

function SignIn({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)
  const [demo, setDemo] = useState<DemoAccount[]>([])

  useEffect(() => {
    api.demoAccounts().then(setDemo, () => setDemo([]))
  }, [])

  async function attempt(fn: () => Promise<User>) {
    setPending(true)
    setError(null)
    try {
      onSignedIn(await fn())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setPending(false)
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault()
    void attempt(() => api.login(username, password))
  }

  return (
    <div className="grid h-full lg:grid-cols-[1fr_minmax(420px,520px)]">
      <div className="relative hidden overflow-hidden bg-gradient-to-br from-accent-600 via-accent-500 to-room-500 p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/15 ring-1 ring-white/25">
            <Icon name="calendar" className="h-5 w-5" />
          </div>
          <span className="text-lg font-semibold tracking-tight">ChronoSolve</span>
        </div>
        <div className="max-w-lg">
          <h1 className="text-4xl leading-tight font-semibold tracking-tight">
            Generate once. Adapt intelligently. Disrupt minimally.
          </h1>
          <p className="mt-4 text-[15px] leading-relaxed text-white/80">
            One timetable for every year, semester and division — solved with Google OR-Tools
            CP-SAT, repaired with the fewest possible moves when something changes, and published
            only after a coordinator approves it.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-white/90">
            {[
              'Hard rules verified independently of the solver on every version',
              'Teacher requests come with ranked repair options, not guesses',
              'What-if previews never touch the published timetable',
            ].map((t) => (
              <li key={t} className="flex items-start gap-2.5">
                <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/20">
                  <Icon name="check" className="h-3 w-3" />
                </span>
                {t}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-xs text-white/60">Campusathon 2026 · PS1 Smart Timetable Generator</p>
      </div>

      <div className="flex items-center justify-center bg-white p-6 sm:p-10">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <Brand />
          </div>
          <h2 className="text-2xl font-semibold tracking-tight text-slate-900">Sign in</h2>
          <p className="mt-1 text-sm text-slate-500">Use your institution account.</p>

          <form onSubmit={submit} className="mt-6 space-y-4">
            <Field label="Username">
              <Input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder="e.g. admin"
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
              />
            </Field>
            {error && (
              <p role="alert" className="rounded-lg bg-alarm-50 px-3 py-2 text-sm text-alarm-700">
                {error}
              </p>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={pending || !username.trim() || !password}
            >
              {pending ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>

          {demo.length > 0 && (
            <div className="mt-8">
              <div className="mb-3 flex items-center gap-3 text-[11px] font-medium tracking-wide text-slate-400 uppercase">
                <span className="h-px flex-1 bg-slate-200" />
                One-click demo
                <span className="h-px flex-1 bg-slate-200" />
              </div>
              <div className="space-y-2">
                {DEMO_BUTTONS.filter((b) => demo.some((d) => d.role === b.role)).map((b) => (
                  <button
                    key={b.role}
                    type="button"
                    disabled={pending}
                    onClick={() => void attempt(() => api.demo(b.role))}
                    aria-label={`Sign in as ${b.label}`}
                    className="flex w-full items-center gap-3 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-left transition hover:border-accent-400 hover:bg-accent-50/40 disabled:opacity-50"
                  >
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-50 text-accent-600">
                      <Icon name={b.icon} className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium text-slate-800">{b.label}</span>
                      <span className="block truncate text-xs text-slate-500">{b.hint}</span>
                    </span>
                    <Icon name="arrow" className="h-4 w-4 text-slate-400" />
                  </button>
                ))}
              </div>
              <p className="mt-3 text-center text-[11px] text-slate-400">
                Demo passwords: admin123 · faculty123 · student123
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// The signed-in shell
// --------------------------------------------------------------------------

function initials(name: string): string {
  return name
    .replace(/^(Prof\.|Dr\.)\s*/, '')
    .split(/\s+/)
    .map((p) => p[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}

function Shell({ user, onSignedOut }: { user: User; onSignedOut: () => void }) {
  const [route, navigate] = useHashRoute()
  const [state, setState] = useState<AppState | null>(null)
  const [stateError, setStateError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [progress, setProgress] = useState<SolveProgress | null>(null)
  const [notices, setNotices] = useState<Notice[]>([])
  const seq = useRef(0)

  const notify = useCallback((text: string, tone: Notice['tone'] = 'info') => {
    const id = ++seq.current
    setNotices((n) => [...n, { id, tone, text }])
    window.setTimeout(
      () => setNotices((n) => n.filter((x) => x.id !== id)),
      tone === 'error' ? 9000 : 5000,
    )
  }, [])

  const signOut = useCallback(async () => {
    try {
      await api.logout()
    } catch {
      // the session is already gone; signing out locally is all that is left
    }
    window.location.hash = ''
    onSignedOut()
  }, [onSignedOut])

  const refresh = useCallback(async () => {
    try {
      setState(await api.state())
      setStateError(null)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        onSignedOut()
        return
      }
      setStateError(e instanceof Error ? e.message : String(e))
    }
  }, [onSignedOut])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Each stage event carries a fresh `done` array, so React re-renders on it.
  const onProgress = useCallback(
    (p: SolveProgress) => setProgress({ running: p.running, done: [...p.done] }),
    [],
  )

  const run = useCallback(
    async <T,>(
      label: string,
      fn: (onProgress: (p: SolveProgress) => void) => Promise<T>,
    ): Promise<T | null> => {
      setBusy(label)
      setProgress(null)
      try {
        return await fn(onProgress)
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          onSignedOut()
          return null
        }
        notify(e instanceof Error ? e.message : String(e), 'error')
        return null
      } finally {
        setBusy(null)
        setProgress(null)
      }
    },
    [notify, onProgress, onSignedOut],
  )

  const nav = NAV[user.role]
  const [path, query = ''] = route.split('?')
  const current = nav.find((n) => n.route === path)
  const params = useMemo(() => new URLSearchParams(query), [query])

  // Anything outside this role's menu -- a stale link, another role's page --
  // lands on the role's home page instead.
  useEffect(() => {
    if (!current) navigate(nav[0].route)
  }, [current, nav, navigate])

  const ws: Workspace = useMemo(
    () => ({ user, state, refresh, busy, run, notify, navigate, signOut }),
    [user, state, refresh, busy, run, notify, navigate, signOut],
  )

  const groups: [string, NavItem[]][] = []
  for (const item of nav) {
    const g = item.group ?? ''
    const last = groups[groups.length - 1]
    if (last && last[0] === g) last[1].push(item)
    else groups.push([g, [item]])
  }

  return (
    <WorkspaceProvider value={ws}>
      <div className="flex h-full">
        <aside className="hidden w-64 shrink-0 flex-col border-r border-slate-200 bg-white lg:flex">
          <div className="flex h-14 items-center border-b border-slate-100 px-5">
            <Brand />
          </div>
          <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4" aria-label="Main">
            {groups.map(([group, items]) => (
              <div key={group || 'main'}>
                {group && (
                  <div className="mb-1.5 px-2.5 text-[10px] font-semibold tracking-[0.12em] text-slate-400 uppercase">
                    {group}
                  </div>
                )}
                <ul className="space-y-0.5">
                  {items.map((item) => {
                    const active = item.route === path
                    const count = item.badge?.(state) ?? 0
                    return (
                      <li key={item.route}>
                        <a
                          href={`#/${item.route}`}
                          onClick={(e) => {
                            e.preventDefault()
                            navigate(item.route)
                          }}
                          aria-current={active ? 'page' : undefined}
                          className={cx(
                            'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition',
                            active
                              ? 'bg-accent-50 text-accent-700'
                              : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900',
                          )}
                        >
                          <Icon
                            name={item.icon}
                            className={active ? 'text-accent-600' : 'text-slate-400'}
                          />
                          <span className="flex-1 truncate">{item.label}</span>
                          {count > 0 && (
                            <span className="rounded-full bg-move-100 px-1.5 text-[10px] font-semibold text-move-700 tabular-nums">
                              {count}
                            </span>
                          )}
                        </a>
                      </li>
                    )
                  })}
                </ul>
              </div>
            ))}
          </nav>
          <div className="border-t border-slate-100 p-3">
            <div className="flex items-center gap-2.5 rounded-lg px-2 py-1.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-100 text-xs font-semibold text-slate-600">
                {initials(user.display_name)}
              </span>
              <div className="min-w-0 flex-1 leading-tight">
                <div className="truncate text-[13px] font-medium text-slate-800">
                  {user.display_name}
                </div>
                <div className="text-[11px] text-slate-500">{ROLE_LABEL[user.role]}</div>
              </div>
            </div>
          </div>
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/90 backdrop-blur">
            <div className="flex h-14 items-center gap-3 px-4 lg:px-6">
              <div className="lg:hidden">
                <Brand compact />
              </div>
              <div className="hidden min-w-0 items-center gap-2 text-xs text-slate-500 sm:flex">
                {state && (
                  <>
                    <span className="truncate font-medium text-slate-700">{state.summary.name}</span>
                    {state.today && (
                      <>
                        <span className="text-slate-300">·</span>
                        <span className="whitespace-nowrap">Today {longDate(state.today)}</span>
                      </>
                    )}
                  </>
                )}
              </div>
              <div className="ml-auto flex items-center gap-2">
                {state?.version && (
                  <Badge tone="blue" className="hidden md:inline-flex">
                    Published v{state.version.number}
                  </Badge>
                )}
                {state && <StatusPill status={state.published.status} />}
                {user.role === 'ADMIN' && state?.has_pending && (
                  <span className="rounded-full bg-move-50 px-2 py-0.5 text-[10px] font-semibold tracking-wider text-move-700 ring-1 ring-move-200 ring-inset">
                    PREVIEW PENDING
                  </span>
                )}
                <span className="mx-1 hidden h-6 w-px bg-slate-200 sm:block" />
                <span className="hidden text-xs text-slate-600 sm:inline">
                  {user.display_name}
                  <span className="ml-1.5 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                    {ROLE_LABEL[user.role]}
                  </span>
                </span>
                <Button variant="ghost" size="sm" icon="logout" onClick={() => void signOut()}>
                  Sign out
                </Button>
              </div>
            </div>
            <nav className="flex gap-1 overflow-x-auto border-t border-slate-100 px-3 py-2 lg:hidden">
              {nav.map((item) => (
                <a
                  key={item.route}
                  href={`#/${item.route}`}
                  onClick={(e) => {
                    e.preventDefault()
                    navigate(item.route)
                  }}
                  className={cx(
                    'shrink-0 rounded-md px-2.5 py-1.5 text-xs font-medium whitespace-nowrap',
                    item.route === path
                      ? 'bg-accent-50 text-accent-700'
                      : 'text-slate-600 hover:bg-slate-50',
                  )}
                >
                  {item.label}
                </a>
              ))}
            </nav>
          </header>

          <main className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-[1400px] px-4 py-6 lg:px-8">
              {stateError && (
                <div className="mb-4 rounded-lg border border-alarm-200 bg-alarm-50 px-4 py-2 text-sm text-alarm-700">
                  {stateError}
                </div>
              )}
              {!state ? (
                <div className="flex items-center gap-3 py-20 text-sm text-slate-500">
                  <Spinner /> Loading the published timetable…
                </div>
              ) : current ? (
                user.role === 'ADMIN' ? (
                  <AdminPage path={path} params={params} />
                ) : user.role === 'FACULTY' ? (
                  <FacultyPage path={path} params={params} />
                ) : (
                  <StudentPage />
                )
              ) : null}
            </div>
          </main>
        </div>

        <div className="pointer-events-none fixed top-16 right-5 z-50 flex w-[min(24rem,calc(100vw-2.5rem))] flex-col gap-2">
          {notices.map((n) => (
            <div
              key={n.id}
              role={n.tone === 'error' ? 'alert' : 'status'}
              className={cx(
                'pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-white px-4 py-3 text-sm shadow-lg',
                n.tone === 'error'
                  ? 'border-alarm-200 text-alarm-700'
                  : n.tone === 'success'
                    ? 'border-keep-200 text-keep-700'
                    : 'border-slate-200 text-slate-700',
              )}
            >
              <Icon
                name={n.tone === 'error' ? 'alert' : n.tone === 'success' ? 'check' : 'clock'}
                className="mt-0.5 h-4 w-4"
              />
              <span className="flex-1">{n.text}</span>
              <button
                type="button"
                aria-label="dismiss"
                onClick={() => setNotices((all) => all.filter((x) => x.id !== n.id))}
                className="text-slate-400 hover:text-slate-700"
              >
                <Icon name="x" className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>

        {busy && <SolveProgressPanel label={busy} progress={progress} />}
      </div>
    </WorkspaceProvider>
  )
}
