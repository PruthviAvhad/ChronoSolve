import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import type { User } from './api'
import App from './App'
import {
  adminUser,
  facultyUser,
  importRejected,
  infeasibleWhatIf,
  mockApi,
  refusal,
  state,
  studentUser,
  unparsedRule,
} from './test/fixtures'

/** Sign in as `user` on `route`, and wait for the shell to finish loading. */
async function boot(route: string, routes = {}, user: User = adminUser) {
  window.location.hash = `#/${route}`
  const api = mockApi({ '/api/auth/me': user, ...routes })
  render(<App />)
  await screen.findByRole('navigation', { name: 'Main' })
  await waitFor(() => expect(screen.queryByText(/Loading the published timetable/)).toBeNull())
  return api
}

function signedOut(routes = {}) {
  window.location.hash = ''
  return mockApi({ '/api/auth/me': refusal(401, 'Not signed in.'), ...routes })
}

function workbook(name = 'department.xlsx') {
  return new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04])], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
}

/** The file input is visually hidden, which userEvent.upload refuses to touch. */
function uploadWorkbook(name?: string) {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, { target: { files: [workbook(name)] } })
}

const mainNav = () => screen.getByRole('navigation', { name: 'Main' })

describe('signing in', () => {
  it('asks for a sign-in when there is no session, with one-click demo accounts', async () => {
    signedOut()
    render(<App />)
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    for (const who of ['Coordinator', 'Faculty', 'Student']) {
      expect(await screen.findByRole('button', { name: `Sign in as ${who}` })).toBeInTheDocument()
    }
  })

  it('opens the coordinator dashboard after a demo sign-in', async () => {
    const { calls } = signedOut()
    render(<App />)
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in as Coordinator' }))

    expect(await screen.findByRole('heading', { name: 'Coordinator dashboard' })).toBeInTheDocument()
    const demo = calls.find((c) => c.url === '/api/auth/demo')
    expect(demo?.body).toMatchObject({ role: 'ADMIN' })
  })

  it('reports a wrong password without signing in', async () => {
    signedOut({ '/api/auth/login': refusal(401, 'Wrong username or password.') })
    render(<App />)
    await userEvent.type(await screen.findByLabelText('Username'), 'admin')
    await userEvent.type(screen.getByLabelText('Password'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Wrong username or password.')
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull()
  })

  it('tells the user how to start the API when it cannot be reached', async () => {
    mockApi({ '/api/auth/me': new Response('nope', { status: 500 }) })
    render(<App />)
    expect(await screen.findByText('Cannot reach the API')).toBeInTheDocument()
    expect(screen.getByText(/uvicorn backend\.app\.api:app/)).toBeInTheDocument()
  })

  it('signs out back to the sign-in screen', async () => {
    const { calls } = await boot('admin/dashboard')
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }))

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/auth/logout' && c.method === 'POST')).toBe(true)
  })
})

describe('one shell, menus by role', () => {
  it('gives the coordinator every scheduling tool', async () => {
    await boot('admin/dashboard')
    for (const item of [
      'Dashboard',
      'Academic structure',
      'Faculty',
      'Rooms & labs',
      'Subjects',
      'Constraints',
      'Generate & optimise',
      'Timetables',
      'Disruptions & what-if',
      'Requests',
      'Analytics',
      'Versions & exports',
    ]) {
      expect(within(mainNav()).getByRole('link', { name: new RegExp(item) })).toBeInTheDocument()
    }
  })

  it('gives a teacher only their own timetable and requests', async () => {
    await boot('faculty/timetable', {}, facultyUser)
    const nav = mainNav()
    expect(within(nav).getByRole('link', { name: 'My timetable' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Report unavailability' })).toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'My requests' })).toBeInTheDocument()
    expect(within(nav).queryByRole('link', { name: 'Constraints' })).toBeNull()
    expect(within(nav).queryByRole('link', { name: 'Analytics' })).toBeNull()
  })

  it('gives a student only the class timetable', async () => {
    await boot('student/timetable', {}, studentUser)
    const nav = mainNav()
    expect(within(nav).getAllByRole('link')).toHaveLength(1)
    expect(within(nav).getByRole('link', { name: 'Class timetable' })).toBeInTheDocument()
  })

  it("sends a user away from another role's page", async () => {
    const { calls } = await boot('admin/constraints', {}, studentUser)
    expect(await screen.findByRole('heading', { name: 'Class timetable' })).toBeInTheDocument()
    expect(calls.some((c) => c.url.startsWith('/api/constraints'))).toBe(false)
  })
})

describe('coordinator dashboard', () => {
  it('shows the published version and what is waiting', async () => {
    await boot('admin/dashboard')
    expect(await screen.findByRole('heading', { name: 'Coordinator dashboard' })).toBeInTheDocument()
    const card = screen.getByText('Published version').parentElement as HTMLElement
    expect(within(card).getByText('v1')).toBeInTheDocument()
    expect(screen.getAllByText(/published-v1/).length).toBeGreaterThan(0)
    expect(screen.getAllByText('FEASIBLE').length).toBeGreaterThan(0)
    expect(screen.getByText('Prof. Mehta · Fri 11 Sep 2026, 09:00–17:00')).toBeInTheDocument()
  })
})

describe('disruptions and what-if', () => {
  const faculty = 'Prof. Mehta is unavailable on Friday afternoon'

  it('offers every rehearsed scenario, truncating long ones', async () => {
    await boot('admin/disruptions')
    expect(await screen.findByText(faculty)).toBeInTheDocument()
    expect(screen.getByText('Prof. Mehta is on leave for the whole week')).toBeInTheDocument()
    expect(screen.getByText(/\+3 more/)).toBeInTheDocument()
  })

  it('shows the repair once a scenario is run', async () => {
    await boot('admin/disruptions')
    await userEvent.click(await screen.findByText(faculty))
    expect(await screen.findByText('96.8%')).toBeInTheDocument()
    expect(screen.getByText('151 of 156 sessions preserved')).toBeInTheDocument()
  })

  it('never publishes on its own', async () => {
    const { calls } = await boot('admin/disruptions')
    await userEvent.click(await screen.findByText(faculty))
    await screen.findByText('96.8%')

    expect(calls.some((c) => c.url.startsWith('/api/apply'))).toBe(false)
    expect(calls.filter((c) => c.url.startsWith('/api/what-if'))).toHaveLength(1)
  })

  it('keeps the proposed-repair view reachable after a solve', async () => {
    await boot('admin/disruptions')
    expect(screen.getByRole('button', { name: 'proposed repair' })).toBeDisabled()
    await userEvent.click(await screen.findByText(faculty))
    await screen.findByText('96.8%')

    expect(screen.getByRole('button', { name: 'proposed repair' })).toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'published' }))
    expect(screen.getByRole('button', { name: 'proposed repair' })).toBeEnabled()
  })

  it('marks the proposal as pending in the header', async () => {
    let reads = 0
    await boot('admin/disruptions', {
      '/api/state': () => ({ ...state, has_pending: reads++ > 0 }),
    })
    await userEvent.click(await screen.findByText(faculty))
    await screen.findByText('96.8%')
    expect(await screen.findByText('PREVIEW PENDING')).toBeInTheDocument()
  })

  it('publishes only when the coordinator applies the repair', async () => {
    const { calls } = await boot('admin/disruptions')
    await userEvent.click(await screen.findByText(faculty))
    await screen.findByText('96.8%')

    await userEvent.click(screen.getByRole('button', { name: 'Apply repair' }))
    expect(await screen.findByText('Published as version 1.')).toBeInTheDocument()
    expect(calls.filter((c) => c.url === '/api/apply' && c.method === 'POST')).toHaveLength(1)
  })

  it('drops the proposal on discard', async () => {
    await boot('admin/disruptions')
    await userEvent.click(await screen.findByText(faculty))
    await screen.findByText('96.8%')

    await userEvent.click(screen.getByRole('button', { name: 'Discard' }))
    await waitFor(() => expect(screen.queryByText('96.8%')).toBeNull())
  })

  it('reports an impossible disruption with its diagnosis', async () => {
    await boot('admin/disruptions', { '/api/what-if': infeasibleWhatIf })
    await userEvent.click(await screen.findByText('Prof. Mehta is on leave for the whole week'))

    const heading = await screen.findByText('No feasible repair exists')
    const panel = heading.closest('section') as HTMLElement
    expect(within(panel).getByText('Faculty availability')).toBeInTheDocument()
    expect(within(panel).getByText(/must teach 11h but is available for only 0/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply repair' })).toBeNull()
  })
})

describe('natural-language entry', () => {
  const sentence = 'Prof. Mehta is unavailable after 2 PM on Friday'

  it('previews the structured rule before solving', async () => {
    const { calls } = await boot('admin/disruptions')
    await userEvent.type(screen.getByLabelText('Describe a change'), sentence)
    await userEvent.click(screen.getByRole('button', { name: 'Read as a rule' }))

    expect(await screen.findByText('Prof. Mehta unavailable on Fri, 14:00-23:59')).toBeInTheDocument()
    expect(screen.getByText(/FACULTY_UNAVAILABLE/)).toBeInTheDocument()
    // Reading a sentence must not run the solver.
    expect(calls.some((c) => c.url.startsWith('/api/what-if'))).toBe(false)
  })

  it('solves only after the rule is confirmed', async () => {
    const { calls } = await boot('admin/disruptions')
    await userEvent.type(screen.getByLabelText('Describe a change'), sentence)
    await userEvent.click(screen.getByRole('button', { name: 'Read as a rule' }))
    await screen.findByText('Prof. Mehta unavailable on Fri, 14:00-23:59')

    await userEvent.click(screen.getByRole('button', { name: 'Confirm & run what-if' }))
    await screen.findByText('96.8%')

    const solved = calls.find((c) => c.url.startsWith('/api/what-if'))
    expect(solved?.body).toMatchObject({
      disruptions: [{ kind: 'FACULTY_UNAVAILABLE', target: 'F01', day: 4 }],
    })
  })

  it('explains an unreadable sentence instead of guessing', async () => {
    await boot('admin/disruptions', { '/api/parse': unparsedRule })
    await userEvent.type(screen.getByLabelText('Describe a change'), 'something vague')
    await userEvent.click(screen.getByRole('button', { name: 'Read as a rule' }))

    expect(await screen.findByText('Not understood')).toBeInTheDocument()
    expect(screen.getByText(/No weekday found/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Confirm & run what-if' })).toBeNull()
  })

  it('can save the reading as a constraint instead of solving', async () => {
    const { calls } = await boot('admin/constraints')
    await userEvent.type(await screen.findByLabelText('Describe a change'), sentence)
    await userEvent.click(screen.getByRole('button', { name: 'Read as a rule' }))
    await screen.findByText('Prof. Mehta unavailable on Fri, 14:00-23:59')

    await userEvent.click(screen.getByRole('button', { name: 'Save as constraint' }))
    await waitFor(() => {
      const saved = calls.find((c) => c.url === '/api/constraints' && c.method === 'POST')
      expect(saved?.body).toMatchObject({
        category: 'BASE',
        kind: 'FACULTY_UNAVAILABLE',
        target_id: 'F01',
        days: [4],
        start_time: '14:00',
      })
    })
    expect(calls.some((c) => c.url.startsWith('/api/what-if'))).toBe(false)
  })

  it('states that the parser is not the scheduling engine', async () => {
    await boot('admin/disruptions')
    expect(screen.getByText(/not a language model/i)).toBeInTheDocument()
    expect(screen.getByText(/CP-SAT still produces the timetable/)).toBeInTheDocument()
  })
})

describe('timetables', () => {
  it('explains a session when its card is clicked, and closes again', async () => {
    await boot('admin/timetables')
    await userEvent.click(await screen.findByText('CS351'))

    expect(await screen.findByText(/1 of 40 start times/)).toBeInTheDocument()
    expect(screen.getByText('Why is this session here?')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'close' }))
    await waitFor(() => expect(screen.queryByText(/1 of 40 start times/)).toBeNull())
  })

  it('locks a session where it is published', async () => {
    const { calls } = await boot('admin/timetables')
    await userEvent.click(await screen.findByText('CS351'))
    await userEvent.click(await screen.findByRole('button', { name: 'Lock in place' }))

    await waitFor(() => {
      const lock = calls.find((c) => c.url === '/api/locks' && c.method === 'POST')
      expect(lock?.body).toMatchObject({ session_id: 'SE-A-CS351-1', keep_room: true })
    })
  })

  it('validates a manual move, then re-optimises around it', async () => {
    const { calls } = await boot('admin/timetables')
    await userEvent.click(await screen.findByText('CS351'))
    await userEvent.selectOptions(await screen.findByLabelText('Move to'), '0')
    await userEvent.click(screen.getByRole('button', { name: 'Check move' }))

    expect(await screen.findByText('Allowed, with knock-on moves')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Preview move' }))
    expect(await screen.findByText('96.8%')).toBeInTheDocument()

    const preview = calls.find((c) => c.url.startsWith('/api/moves/preview'))
    expect(preview?.body).toMatchObject({ session_id: 'SE-A-CS351-1', timeslot_id: 0 })
    expect(calls.some((c) => c.url.startsWith('/api/apply'))).toBe(false)
  })

  it('switches between division, faculty and room', async () => {
    const { calls } = await boot('admin/timetables')
    await screen.findByText('CS351')
    await userEvent.click(screen.getByRole('button', { name: 'Faculty' }))
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes('view=faculty') && c.url.includes('F01'))).toBe(true),
    )
  })

  it('links each export to the timetable on screen', async () => {
    await boot('admin/timetables')
    expect(await screen.findByRole('link', { name: 'Excel workbook' })).toHaveAttribute(
      'href',
      '/api/export/xlsx?source=published',
    )
    expect(screen.getByRole('link', { name: 'PDF timetable' })).toHaveAttribute(
      'href',
      '/api/export/pdf?source=published',
    )
    const ics = screen.getByRole('link', { name: 'Calendar — this view' }).getAttribute('href') ?? ''
    expect(ics).toContain('view=batch')
    expect(ics).toContain('id=SE-A')
  })

  it('shows the legend alongside the grid', async () => {
    await boot('admin/timetables')
    expect(await screen.findByText('moved to a new time')).toBeInTheDocument()
    expect(screen.getByText(/click any session to see why it sits there/)).toBeInTheDocument()
  })
})

describe('generate and optimise', () => {
  it('exposes every tunable soft objective', async () => {
    await boot('admin/generate')
    for (const label of [
      'Student compactness',
      'Faculty comfort',
      'Daily load balance',
      'Subject spread',
      'Avoid last period',
      'Room fit',
      'Teacher preferences',
    ]) {
      expect(screen.getByRole('slider', { name: label })).toBeInTheDocument()
    }
    expect(screen.getAllByRole('slider')).toHaveLength(7)
  })

  it('generates a proposal for review rather than publishing', async () => {
    const { calls } = await boot('admin/generate')
    await userEvent.click(screen.getByRole('button', { name: 'Generate timetable' }))
    await waitFor(() => {
      const generate = calls.find((c) => c.url.startsWith('/api/generate'))
      expect(generate?.body).toMatchObject({ weights: expect.any(Object), publish: false })
    })
    expect(calls.some((c) => c.url.startsWith('/api/apply'))).toBe(false)
  })

  it('offers the import template and reports what a workbook contained', async () => {
    const { calls } = await boot('admin/generate')
    expect(screen.getByRole('link', { name: 'Download template' })).toHaveAttribute(
      'href',
      '/api/import/template',
    )
    uploadWorkbook()
    const heading = await screen.findByText('Imported and solved')
    expect(within(heading.parentElement as HTMLElement).getByText(/156 sessions/)).toBeInTheDocument()
    expect(screen.getByText(/faculty teach nothing/)).toBeInTheDocument()

    const upload = calls.find((c) => c.url.startsWith('/api/import'))
    expect(upload?.body).toMatchObject({ file: { name: 'department.xlsx' } })
  })

  it('names the sheet and row when a workbook is refused', async () => {
    await boot('admin/generate', { '/api/import': importRejected })
    uploadWorkbook('broken.xlsx')
    expect(await screen.findByText('Nothing was imported')).toBeInTheDocument()
    expect(screen.getByText(/Subjects row 7/)).toBeInTheDocument()
    expect(screen.getByText(/unknown batch/)).toBeInTheDocument()
  })

  it('shows the constraint evidence for the published timetable', async () => {
    await boot('admin/generate')
    const heading = screen.getByText('Published timetable — technical proof')
    const proof = heading.closest('section') as HTMLElement
    expect(within(proof).getByText('Faculty conflicts')).toBeInTheDocument()
    expect(within(proof).getByText('CP-SAT')).toBeInTheDocument()
  })
})

describe('teacher request workflow', () => {
  it('lets a teacher get ranked options and auto-select the best', async () => {
    const { calls } = await boot('faculty/report', {}, facultyUser)
    await userEvent.click(screen.getByRole('button', { name: 'Find repair options' }))

    expect(await screen.findByText('Fewest moves')).toBeInTheDocument()
    expect(screen.getByText('Best')).toBeInTheDocument()
    expect(screen.getByText('99.4%')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Auto-select best/ }))
    expect(await screen.findByText('Sent for approval')).toBeInTheDocument()

    const created = calls.filter((c) => c.url.startsWith('/api/requests') && c.method === 'POST')
    expect(created[0].url).toBe('/api/requests/stream')
    expect(created[0].body).toMatchObject({ start_date: '2026-09-10', start_time: '09:00' })
    expect(calls.find((c) => c.url === '/api/requests/7/submit')?.body).toMatchObject({ auto: true })
    // A teacher never publishes anything.
    expect(calls.some((c) => c.url.startsWith('/api/apply') || c.url.includes('/approve'))).toBe(false)
  })

  it('lets a teacher submit a chosen option instead', async () => {
    const { calls } = await boot('faculty/report', {}, facultyUser)
    await userEvent.click(screen.getByRole('button', { name: 'Find repair options' }))
    await userEvent.click(await screen.findByRole('radio', { name: 'Option 2' }))
    await userEvent.click(screen.getByRole('button', { name: 'Submit option 2' }))

    await waitFor(() =>
      expect(calls.find((c) => c.url === '/api/requests/7/submit')?.body).toMatchObject({ rank: 2 }),
    )
  })

  it('lets the coordinator review and approve, publishing a new version', async () => {
    const { calls } = await boot('admin/requests')
    await userEvent.click(await screen.findByRole('button', { name: 'Review chosen option' }))
    expect(await screen.findByText('Review — current vs proposed')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: /Approve/ }))
    expect(await screen.findByText('Approved. Published as version 2.')).toBeInTheDocument()
    expect(calls.some((c) => c.url === '/api/requests/7/approve' && c.method === 'POST')).toBe(true)
  })

  it('lets the coordinator reject with a note', async () => {
    const { calls } = await boot('admin/requests')
    await userEvent.type(await screen.findByLabelText('Note to the teacher'), 'Exam week')
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }))

    await waitFor(() =>
      expect(calls.find((c) => c.url === '/api/requests/7/reject')?.body).toMatchObject({
        note: 'Exam week',
      }),
    )
  })
})

describe('student view', () => {
  it('picks a year, semester and division and shows its timetable', async () => {
    const { calls } = await boot('student/timetable', {}, studentUser)
    await screen.findByText('CS351')
    expect(calls.some((c) => c.url.includes('view=batch') && c.url.includes('id=SE-A'))).toBe(true)

    await userEvent.selectOptions(screen.getByLabelText('Year'), '4')
    await waitFor(() => expect(calls.some((c) => c.url.includes('id=BE-B'))).toBe(true))
  })

  it('is read-only: no explanations, proposals or edits', async () => {
    const { calls } = await boot('student/timetable', {}, studentUser)
    await userEvent.click(await screen.findByText('CS351'))
    expect(calls.some((c) => c.url.startsWith('/api/explain'))).toBe(false)
    expect(calls.some((c) => c.url.includes('source=pending'))).toBe(false)
  })
})

describe('every coordinator page renders from live data', () => {
  it.each([
    ['admin/structure', 'Academic structure'],
    ['admin/faculty', 'Faculty'],
    ['admin/rooms', 'Rooms & labs'],
    ['admin/subjects', 'Subjects'],
    ['admin/constraints', 'Constraints'],
    ['admin/analytics', 'Analytics'],
    ['admin/versions', 'Versions & exports'],
  ])('%s', async (route, title) => {
    await boot(route)
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument()
  })
})
