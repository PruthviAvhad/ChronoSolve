import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { describe, expect, it, vi } from 'vitest'

import type { WhatIf } from './api'
import { ExplainPanel, ProofPanel, RepairPanel, StatusPill } from './Panels'
import {
  calendar,
  explanation,
  infeasibleWhatIf,
  state,
  whatIf,
} from './test/fixtures'

const noop = () => {}

type RepairProps = ComponentProps<typeof RepairPanel>

function renderRepair(
  result: WhatIf = whatIf,
  extra: Partial<Omit<RepairProps, 'result'>> = {},
) {
  return render(
    <RepairPanel
      result={result}
      onApply={noop}
      onDiscard={noop}
      busy={false}
      {...extra}
    />,
  )
}

function renderProof() {
  return render(
    <ProofPanel
      title="Published timetable"
      solver={state.published}
      validation={state.validation}
      quality={state.quality}
    />,
  )
}

function renderExplain(onClose = noop) {
  return render(
    <ExplainPanel
      explanation={explanation}
      calendar={calendar}
      onClose={onClose}
    />,
  )
}

describe('RepairPanel, feasible', () => {
  it('leads with the retention headline', () => {
    renderRepair()
    expect(screen.getByText('96.8%')).toBeInTheDocument()
    expect(screen.getByText('151 of 156 sessions preserved')).toBeInTheDocument()
  })

  it('breaks the change down by kind', () => {
    renderRepair()
    // "Directly affected" is both a tile label and a section heading, so match
    // on presence rather than uniqueness.
    for (const label of [
      'Directly affected',
      'Changed',
      'Unchanged',
      'New time',
      'Room only',
      'Hard conflicts',
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
  })

  it('shows the solver status', () => {
    renderRepair()
    expect(screen.getByText('OPTIMAL')).toBeInTheDocument()
  })

  it('lists the directly affected sessions', () => {
    const { container } = renderRepair()
    // The affected sessions are a list; the same subject also appears in the
    // changes table below, so scope the query to the list itself.
    const list = container.querySelector('ul')
    expect(list).toBeTruthy()
    expect(within(list as HTMLElement).getByText('Machine Learning')).toBeInTheDocument()
    expect(
      within(list as HTMLElement).getByText(/Fri 12:00-13:00/),
    ).toBeInTheDocument()
  })

  it('gives every proposed change a reason it could not stay', () => {
    renderRepair()
    const table = screen.getByRole('table')
    expect(within(table).getByText('Fri 12:00 LH110')).toBeInTheDocument()
    expect(within(table).getByText('Thu 16:00 LH108')).toBeInTheDocument()
    expect(
      screen.getByText(
        /could not stay: Prof\. Mehta is marked unavailable at Fri 12:00/,
      ),
    ).toBeInTheDocument()
  })

  it('reports each phase with its own solver status', () => {
    renderRepair()
    // The two phases certify different things, so both statuses are shown.
    expect(screen.getByText(/phase 1 1\.42s \(optimal\)/)).toBeInTheDocument()
    expect(screen.getByText(/phase 2 3s \(optimal\)/)).toBeInTheDocument()
  })

  it('claims minimality only when phase 1 proved it', () => {
    renderRepair()
    expect(screen.getByText('FEWEST MOVES PROVEN')).toBeInTheDocument()

    cleanup()
    renderRepair({ ...whatIf, minimal_proven: false, phase1_status: 'FEASIBLE' })
    expect(screen.queryByText('FEWEST MOVES PROVEN')).toBeNull()
  })

  it('wires up apply and discard', async () => {
    const onApply = vi.fn()
    const onDiscard = vi.fn()
    renderRepair(whatIf, { onApply, onDiscard })

    await userEvent.click(screen.getByRole('button', { name: 'Apply repair' }))
    await userEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(onApply).toHaveBeenCalledOnce()
    expect(onDiscard).toHaveBeenCalledOnce()
  })

  it('locks the buttons while a solve is running', () => {
    renderRepair(whatIf, { busy: true })
    expect(screen.getByRole('button', { name: 'Apply repair' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard' })).toBeDisabled()
  })
})

describe('RepairPanel, infeasible', () => {
  it('says plainly that no repair exists', () => {
    renderRepair(infeasibleWhatIf)
    expect(screen.getByText('No feasible repair exists')).toBeInTheDocument()
    expect(
      screen.getByText(/4 rule categories cannot be satisfied/),
    ).toBeInTheDocument()
  })

  it('shows the root cause with its relaxations', () => {
    renderRepair(infeasibleWhatIf)
    expect(screen.getByText('Faculty availability')).toBeInTheDocument()
    expect(
      screen.getByText(/must teach 11h but is available for only 0/),
    ).toBeInTheDocument()
    expect(screen.getByText(/free up at least 11 more period/)).toBeInTheDocument()
  })

  it('separates proven-impossible findings from merely tight ones', () => {
    renderRepair(infeasibleWhatIf)
    expect(screen.getByText('blocking')).toBeInTheDocument()
    expect(screen.getByText('no slack')).toBeInTheDocument()
  })

  it('does not overclaim what a clean check would prove', () => {
    renderRepair(infeasibleWhatIf)
    expect(
      screen.getByText(/Passing every check would not prove one does/),
    ).toBeInTheDocument()
  })

  it('offers no apply button for a repair that does not exist', () => {
    renderRepair(infeasibleWhatIf)
    expect(screen.queryByRole('button', { name: 'Apply repair' })).toBeNull()
  })
})

describe('RepairPanel, search exhausted', () => {
  // UNKNOWN means CP-SAT ran out of time, not that it proved anything. Saying
  // "no feasible repair exists" here would claim a result never established.
  const timedOut = {
    ...infeasibleWhatIf,
    status: 'UNKNOWN',
    reason:
      'No repair was found within the 10s search budget (solver returned ' +
      'UNKNOWN). This does not prove that none exists.',
  }

  it('distinguishes running out of time from proving impossibility', () => {
    renderRepair(timedOut)
    expect(
      screen.getByText('No repair found in the time available'),
    ).toBeInTheDocument()
    expect(screen.queryByText('No feasible repair exists')).toBeNull()
  })

  it('states plainly that nothing was proved', () => {
    renderRepair(timedOut)
    expect(screen.getByText(/does not prove that none exists/)).toBeInTheDocument()
  })

  it('does not present the diagnosis as a proof of impossibility', () => {
    renderRepair(timedOut)
    expect(screen.queryByText(/4 rule categories cannot be satisfied/)).toBeNull()
    expect(
      screen.getByText('Diagnosis — the tightest constraints found'),
    ).toBeInTheDocument()
  })

  it('still refuses to offer a repair it never found', () => {
    renderRepair(timedOut)
    expect(screen.queryByRole('button', { name: 'Apply repair' })).toBeNull()
  })
})

describe('ProofPanel', () => {
  it('names the solver and its evidence', () => {
    renderProof()
    expect(screen.getByText('CP-SAT')).toBeInTheDocument()
    expect(screen.getByText('FEASIBLE')).toBeInTheDocument()
    expect(screen.getByText('20.21s')).toBeInTheDocument()
  })

  it('lists every hard-constraint family in readable form', () => {
    renderProof()
    for (const label of [
      'Faculty conflicts',
      'Room conflicts',
      'Batch conflicts',
      'Contiguous labs',
      'Protected lunch',
      'Parallel electives',
      'Max consecutive hours',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
    // 14 families, all currently zero.
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(14)
  })

  it('states it verified the schedule independently of the model', () => {
    renderProof()
    expect(
      screen.getByText(/verified independently of the solver model/i),
    ).toBeInTheDocument()
  })
})

describe('ExplainPanel', () => {
  it('says how many alternatives actually exist', () => {
    renderExplain()
    expect(screen.getByText(/1 of 40 start times/)).toBeInTheDocument()
    expect(screen.getByText(/Currently Thu 11:00 in CL3/)).toBeInTheDocument()
  })

  it('tallies what blocks the rest', () => {
    renderExplain()
    expect(screen.getByText('Faculty already teaching')).toBeInTheDocument()
    expect(screen.getByText('1 slot')).toBeInTheDocument()
  })

  it('disclaims being an unsatisfiable core', () => {
    renderExplain()
    expect(screen.getByText(/Not a solver unsatisfiable core/)).toBeInTheDocument()
  })

  it('can be dismissed', async () => {
    const onClose = vi.fn()
    renderExplain(onClose)
    await userEvent.click(screen.getByRole('button', { name: 'close' }))
    expect(onClose).toHaveBeenCalledOnce()
  })
})

describe('StatusPill', () => {
  it.each([
    ['OPTIMAL', 'keep-500'],
    ['FEASIBLE', 'accent-400'],
    ['INFEASIBLE', 'alarm-500'],
  ])('colours %s distinctly', (status, expected) => {
    const { container } = render(<StatusPill status={status} />)
    expect(container.firstElementChild?.className).toContain(expected)
  })
})
