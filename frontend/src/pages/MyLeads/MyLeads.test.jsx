/* @vitest-environment jsdom */

import React from 'react'
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import MyLeadsPage from './index'

vi.mock('../LeadPipeline/LeadTable', () => ({
  LeadTable: ({ filters, currentUserId, onSelect, emptyTitle, emptyMessage, refreshKey }) => (
    <div data-testid="mock-lead-table">
      <div data-testid="table-assigned-to">{filters?.assigned_to}</div>
      <div data-testid="table-user-id">{currentUserId}</div>
      <div data-testid="table-empty-title">{emptyTitle}</div>
      <div data-testid="table-empty-message">{emptyMessage}</div>
      <div data-testid="table-refresh-key">{refreshKey}</div>
      <button
        type="button"
        data-testid="select-lead-btn"
        onClick={() =>
          onSelect('lead-101', {
            id: 'lead-101',
            title: 'Fullstack React Engineer',
            organization_name: 'Acme Corp',
            source_name: 'Internshala',
          })
        }
      >
        Select Lead
      </button>
    </div>
  ),
}))

vi.mock('../LeadPipeline/LeadDetailDrawer', () => ({
  default: ({ lead, isOpen, onClose }) =>
    isOpen && lead ? (
      <div data-testid="mock-detail-drawer">
        <h3>{lead.title}</h3>
        <p>{lead.organization_name}</p>
        <button type="button" onClick={onClose} aria-label="Close drawer">
          Close
        </button>
      </div>
    ) : null,
}))

describe('FE-14: My Leads Page', () => {
  const mockUser = {
    id: 'user-researcher-1',
    email: 'researcher@example.com',
    full_name: 'Jane Doe',
    role: 'researcher',
  }

  afterEach(() => {
    cleanup()
  })

  it('renders the My Leads heading and subtitle', () => {
    render(<MyLeadsPage currentUser={mockUser} />)

    expect(screen.getByRole('heading', { name: 'My Assigned Leads' })).toBeTruthy()
    expect(
      screen.getByText('Review and manage fresher opportunities claimed by or assigned to you.')
    ).toBeTruthy()
  })

  it('passes assigned_to filter matching currentUser.id to LeadTable', () => {
    render(<MyLeadsPage currentUser={mockUser} />)

    expect(screen.getByTestId('table-assigned-to').textContent).toBe('user-researcher-1')
    expect(screen.getByTestId('table-user-id').textContent).toBe('user-researcher-1')
  })

  it('configures custom empty state title and message for My Leads', () => {
    render(<MyLeadsPage currentUser={mockUser} />)

    expect(screen.getByTestId('table-empty-title').textContent).toBe('No leads assigned to you')
    expect(screen.getByTestId('table-empty-message').textContent).toContain('You have not claimed any leads yet')
  })

  it('opens and closes the LeadDetailDrawer when a lead is selected', () => {
    render(<MyLeadsPage currentUser={mockUser} />)

    expect(screen.queryByTestId('mock-detail-drawer')).toBeNull()

    fireEvent.click(screen.getByTestId('select-lead-btn'))

    expect(screen.getByTestId('mock-detail-drawer')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Fullstack React Engineer' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Close drawer' }))

    expect(screen.queryByTestId('mock-detail-drawer')).toBeNull()
  })

  it('increments refresh key when clicking the Refresh button', () => {
    render(<MyLeadsPage currentUser={mockUser} />)

    expect(screen.getByTestId('table-refresh-key').textContent).toBe('0')

    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    expect(screen.getByTestId('table-refresh-key').textContent).toBe('1')
  })
})
