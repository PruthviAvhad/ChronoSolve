import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { GridLegend, TimetableGrid } from './Grid'
import { calendar, cell, grid, gridWithChanges } from './test/fixtures'

describe('TimetableGrid', () => {
  it('renders one card per session', () => {
    render(<TimetableGrid grid={grid} />)
    for (const c of grid.cells) {
      expect(screen.getByText(c.subject_code)).toBeInTheDocument()
    }
  })

  it('marks the lunch period on every day', () => {
    render(<TimetableGrid grid={grid} />)
    expect(screen.getAllByText('lunch')).toHaveLength(calendar.days.length)
  })

  it('shows every weekday as a column header', () => {
    render(<TimetableGrid grid={grid} />)
    for (const day of calendar.days) {
      expect(screen.getByText(day)).toBeInTheDocument()
    }
  })

  it('badges a multi-hour lab with its length and spans the rows', () => {
    const { container } = render(<TimetableGrid grid={grid} />)
    expect(screen.getByText('2h')).toBeInTheDocument()

    const lab = container.querySelector('[title*="Data Structures Lab"]')
    expect(lab).toBeTruthy()
    // The 2-hour block occupies two grid rows.
    const wrapper = lab?.closest('[style*="span"]') as HTMLElement | null
    expect(wrapper?.style.gridRow).toContain('span 2')
  })

  it('places parallel electives side by side in one slot', () => {
    render(<TimetableGrid grid={grid} />)
    const cloud = screen.getByText('CS401').closest('button')
    const cyber = screen.getByText('CS402').closest('button')
    expect(cloud).toBeTruthy()
    expect(cyber).toBeTruthy()
    // Same slot container, so they share a grandparent.
    expect(cloud?.parentElement?.parentElement).toBe(
      cyber?.parentElement?.parentElement,
    )
  })

  it('shows where a moved session came from', () => {
    render(<TimetableGrid grid={gridWithChanges} />)
    expect(screen.getByText('was Fri 12:00 LH110')).toBeInTheDocument()
    expect(screen.getByText('was Thu 11:00 CL3')).toBeInTheDocument()
  })

  it('colours a time move and a room-only move differently', () => {
    const { container } = render(<TimetableGrid grid={gridWithChanges} />)
    const moved = container.querySelector(
      '[title*="Data Structures & Algorithms"]',
    )
    const roomOnly = container.querySelector('[title*="CS352"]')

    expect(moved?.className).toContain('move-500')
    expect(roomOnly?.className ?? '').not.toContain('move-500')
  })

  it('reports the clicked session to its caller', async () => {
    const onSelect = vi.fn()
    render(<TimetableGrid grid={grid} onSelect={onSelect} />)

    await userEvent.click(screen.getByText('CS301'))
    expect(onSelect).toHaveBeenCalledOnce()
    expect(onSelect.mock.calls[0][0].session_id).toBe('SE-A-CS301-1')
  })

  it('rings the selected session', () => {
    const { container } = render(
      <TimetableGrid grid={grid} selectedId="SE-A-CS301-1" />,
    )
    const selected = container.querySelector(
      '[title*="Data Structures & Algorithms"]',
    )
    expect(selected?.className).toContain('ring-2')
  })

  it('invites the user to click for an explanation', () => {
    render(<TimetableGrid grid={grid} />)
    const title = screen.getByText('CS301').closest('button')?.title ?? ''
    expect(title).toContain('why')
  })

  it('renders an empty grid without crashing', () => {
    render(<TimetableGrid grid={{ ...grid, cells: [] }} />)
    expect(screen.getAllByText('lunch')).toHaveLength(calendar.days.length)
  })

  it('does not break when a session sits in the last period', () => {
    const last = { ...grid, cells: [cell({ day: 4, period: 7 })] }
    render(<TimetableGrid grid={last} />)
    expect(screen.getByText('CS301')).toBeInTheDocument()
  })
})

describe('GridLegend', () => {
  it('explains every cell colour it uses', () => {
    render(<GridLegend />)
    for (const label of [
      'unchanged',
      'moved to a new time',
      'moved room only',
      'multi-hour lab',
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })
})
