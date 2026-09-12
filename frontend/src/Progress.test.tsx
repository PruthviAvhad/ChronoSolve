import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import App from './App'
import { SolveProgressPanel } from './Panels'
import { mockApi, repairStages } from './test/fixtures'

describe('SolveProgressPanel', () => {
  it('names the stage that is running now', () => {
    render(
      <SolveProgressPanel
        label="Re-optimising"
        progress={{
          running: {
            key: 'minimising-disruption',
            label: 'Phase 1: minimising moves from the published plan',
          },
          done: repairStages.slice(0, 2),
        }}
      />,
    )
    expect(
      screen.getByText('Phase 1: minimising moves from the published plan'),
    ).toBeInTheDocument()
  })

  it('reports each finished stage with its measured duration', () => {
    render(
      <SolveProgressPanel
        label="Re-optimising"
        progress={{ running: null, done: repairStages }}
      />,
    )
    expect(screen.getByText('Building the CP-SAT model')).toBeInTheDocument()
    expect(screen.getByText('0.45s')).toBeInTheDocument()
    expect(screen.getByText('Measuring schedule retention')).toBeInTheDocument()
    expect(screen.getByText('4.9s elapsed')).toBeInTheDocument()
  })

  it('carries the facts each stage established, not a summary of them', () => {
    render(
      <SolveProgressPanel
        label="Re-optimising"
        progress={{ running: null, done: repairStages }}
      />,
    )
    expect(
      screen.getByText(/15576 boolean variables from 13389 candidate placements/),
    ).toBeInTheDocument()
    expect(screen.getByText(/fewest possible moves proven/)).toBeInTheDocument()
    expect(
      screen.getByText(/151 of 156 sessions unchanged = 96\.8% retention/),
    ).toBeInTheDocument()
  })

  it('shows no invented completion figure', () => {
    // CP-SAT cannot say how much search is left, so there is no progress bar
    // and no percentage of the solve. Retention is a measured property of the
    // schedule, so it is excluded here by only rendering unfinished stages.
    const { container } = render(
      <SolveProgressPanel
        label="Re-optimising"
        progress={{
          running: { key: 'solving', label: 'Searching' },
          done: repairStages.slice(0, 3),
        }}
      />,
    )
    expect(container.querySelector('progress')).toBeNull()
    expect(container.querySelector('[role="progressbar"]')).toBeNull()
    const percentages = (container.textContent ?? '').match(/\d+(\.\d+)?%/g) ?? []
    expect(percentages).toHaveLength(0)
  })

  it('waits quietly before the first stage arrives', () => {
    render(<SolveProgressPanel label="Generating" progress={null} />)
    expect(screen.getByText('Generating…')).toBeInTheDocument()
    expect(screen.queryByText(/elapsed/)).toBeNull()
  })
})

/** Sign in as the coordinator on `route` (the fixtures default to one). */
async function openAs(route: string) {
  window.location.hash = `#/${route}`
  const api = mockApi()
  render(<App />)
  await screen.findByRole('navigation', { name: 'Main' })
  await waitFor(() => expect(screen.queryByText(/Loading the published timetable/)).toBeNull())
  return api
}

describe('streamed solves', () => {
  it('reports the real stages while a what-if runs', async () => {
    await openAs('admin/disruptions')

    await userEvent.click(
      await screen.findByText('Prof. Mehta is unavailable on Friday afternoon'),
    )

    // The repair finishes fast against a stubbed fetch, so assert on the
    // outcome the stream produced rather than racing the intermediate frames.
    expect(await screen.findByText('96.8%')).toBeInTheDocument()
  })

  it('asks the streaming endpoint, and asks it only once', async () => {
    const { calls } = await openAs('admin/disruptions')

    await userEvent.click(
      await screen.findByText('Prof. Mehta is unavailable on Friday afternoon'),
    )
    await screen.findByText('96.8%')

    const solves = calls.filter((c) => c.url.startsWith('/api/what-if'))
    expect(solves).toHaveLength(1)
    expect(solves[0].url).toBe('/api/what-if/stream')
    // A preview must never publish on its own.
    expect(calls.some((c) => c.url.startsWith('/api/apply'))).toBe(false)
  })

  it('streams a teacher request, and never falls back to a second create', async () => {
    const { calls } = await openAs('admin/dashboard')
    // A request stream that yields nothing must not be retried as a plain
    // POST: the stream route already recorded the request.
    const { api } = await import('./api')
    const out = api.createRequest(
      { start_date: '2026-09-11', end_date: '2026-09-11', start_time: '09:00', end_time: '17:00' },
      () => {},
    )
    await expect(out).resolves.toMatchObject({ id: 7, status: 'DRAFT' })
    const creates = calls.filter((c) => c.url.startsWith('/api/requests') && c.method === 'POST')
    expect(creates.map((c) => c.url)).toEqual(['/api/requests/stream'])
  })

  it('streams a regeneration too', async () => {
    const { calls } = await openAs('admin/generate')

    await userEvent.click(screen.getByRole('button', { name: 'Generate timetable' }))

    await waitFor(() => {
      const generate = calls.find((c) => c.url.startsWith('/api/generate'))
      expect(generate?.url).toBe('/api/generate/stream')
      expect(generate?.body).toMatchObject({ weights: expect.any(Object) })
    })
  })
})
