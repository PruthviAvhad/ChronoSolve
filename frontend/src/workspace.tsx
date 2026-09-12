// Shared application context: who is signed in, the published state, and the
// one place long-running solves report their real progress.

import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import type { AppState, SolveProgress, User } from './api'

export interface Notice {
  id: number
  tone: 'success' | 'error' | 'info'
  text: string
}

export interface Workspace {
  user: User
  state: AppState | null
  refresh: () => Promise<void>
  busy: string | null
  /**
   * Run an action under a busy label. The action receives a progress callback
   * that streaming solves feed with their real stages; a failure becomes an
   * error notice and resolves null, so no page is left stuck.
   */
  run: <T>(
    label: string,
    fn: (onProgress: (p: SolveProgress) => void) => Promise<T>,
  ) => Promise<T | null>
  notify: (text: string, tone?: Notice['tone']) => void
  navigate: (to: string) => void
  signOut: () => Promise<void>
}

const WorkspaceContext = createContext<Workspace | null>(null)

export const WorkspaceProvider = WorkspaceContext.Provider

export function useWorkspace(): Workspace {
  const ws = useContext(WorkspaceContext)
  if (!ws) throw new Error('useWorkspace must be used inside the application shell')
  return ws
}

/** A tiny hash router: '#/admin/requests' -> 'admin/requests'. */
export function useHashRoute(): [string, (to: string) => void] {
  const read = () => window.location.hash.replace(/^#\/?/, '')
  const [route, setRoute] = useState(read)

  useEffect(() => {
    const onChange = () => setRoute(read())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const go = useCallback((to: string) => {
    if (read() !== to) window.location.hash = `/${to}`
    setRoute(to)
  }, [])

  return [route, go]
}

export const DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']

/** Timeslot id from a day and period, as the backend numbers them. */
export function slotId(day: number, period: number, periods = 8): number {
  return day * periods + period
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function isoDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split('-').map(Number)
  return isoDate(new Date(y, m - 1, d + days))
}

/** Monday=0 .. Sunday=6 for an ISO date. */
export function weekday(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return (new Date(y, m - 1, d).getDay() + 6) % 7
}

export const WEEKDAY_NAMES = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
]

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Thu 10 Sep 2026" -- built by hand so it reads the same in every locale. */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return `${WEEKDAY_NAMES[weekday(iso)].slice(0, 3)} ${d} ${MONTHS[m - 1]} ${y}`
}
