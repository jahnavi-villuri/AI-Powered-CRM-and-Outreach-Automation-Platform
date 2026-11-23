/* @vitest-environment jsdom */

import React from 'react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react'
import { LeadTable } from './LeadTable'
import * as leadsApi from '../../api/leads'

vi.mock('../../api/leads', () => {
  return {
    DEFAULT_PAGE_SIZE: 25,
    MAX_PAGE_SIZE: 100,
    DEFAULT_SORT_BY: 'discovered_at',
    DEFAULT_SORT_ORDER: 'desc',
    listLeads: vi.fn(),
    claimLead: vi.fn(),
  }
})

describe('FE-14: LeadTable Claim Action & Optimistic UI', () => {
  const researcherUser = {
    id: 'user-researcher-1',
    role: 'researcher',
    email: 'researcher@example.com',
  }

  const developerUser = {
    id: 'user-dev-1',
    role: 'developer',
    email: 'developer@example.com',
  }

  const sampleLeadUnassigned = {
    id: 'lead-unassigned',
    organization_name: 'Tech Innovators',
    title: 'Junior Software Engineer',
    opportunity_type: 'job',
    source_name: 'Internshala',
    source_url: 'https://example.com/job/1',
    experience_min: 0,
    experience_max: 1,
    status: 'verified',
    score: 85,
    score_band: 'high',
    assigned_to: null,
    discovered_at: '2026-10-16T08:30:00Z',
  }

  const sampleLeadClaimedByOther = {
    id: 'lead-claimed-other',
    organization_name: 'Alpha Systems',
    title: 'Associate Backend Engineer',
    opportunity_type: 'job',
    source_name: 'Naukri Freshers',
    source_url: 'https://example.com/job/2',
    experience_min: 0,
    experience_max: 2,
    status: 'verified',
    score: 90,
    score_band: 'high',
    assigned_to: 'other-user-99',
    discovered_at: '2026-10-15T14:20:00Z',
  }

  const sampleLeadClaimedByMe = {
    id: 'lead-claimed-me',
    organization_name: 'Beta Cloud',
    title: 'Cloud DevOps Intern',
    opportunity_type: 'internship',
    source_name: 'Internshala',
    source_url: 'https://example.com/job/3',
    experience_min: 0,
    experience_max: 0,
    status: 'draft_ready',
    score: 88,
    score_band: 'high',
    assigned_to: 'user-researcher-1',
    discovered_at: '2026-10-14T09:30:00Z',
  }

  const mockListResponse = {
    items: [sampleLeadUnassigned, sampleLeadClaimedByOther, sampleLeadClaimedByMe],
    total: 3,
    page: 1,
    page_size: 25,
    total_pages: 1,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    leadsApi.listLeads.mockResolvedValue(mockListResponse)
  })

  afterEach(() => {
    cleanup()
  })

  it('renders Claim button for unassigned leads when user has researcher role', async () => {
    render(<LeadTable currentUser={researcherUser} />)

    await waitFor(() => {
      expect(screen.getByText('Tech Innovators')).toBeTruthy()
    })

    const claimButtons = screen.getAllByRole('button', { name: /Claim lead Tech Innovators/i })
    expect(claimButtons.length).toBe(1)
    expect(claimButtons[0].textContent).toBe('Claim')
  })

  it('does NOT render Claim button for already assigned leads', async () => {
    render(<LeadTable currentUser={researcherUser} />)

    await waitFor(() => {
      expect(screen.getByText('Alpha Systems')).toBeTruthy()
      expect(screen.getByText('Beta Cloud')).toBeTruthy()
    })

    // Lead claimed by me displays "You" badge
    expect(screen.getByText('You')).toBeTruthy()

    // Lead claimed by other displays their user id
    expect(screen.getByText('other-us…')).toBeTruthy()

    // Only 1 Claim button exists across all 3 rows (for the unassigned lead)
    const allClaimButtons = screen.queryAllByRole('button', { name: /Claim lead/i })
    expect(allClaimButtons.length).toBe(1)
  })

  it('does NOT render Claim button when user lacks claim_leads permission (developer role)', async () => {
    render(<LeadTable currentUser={developerUser} />)

    await waitFor(() => {
      expect(screen.getByText('Tech Innovators')).toBeTruthy()
    })

    const allClaimButtons = screen.queryAllByRole('button', { name: /Claim lead/i })
    expect(allClaimButtons.length).toBe(0)
    expect(screen.getByText('Unassigned')).toBeTruthy()
  })

  it('optimistically updates UI to "You" immediately upon clicking Claim and retains on success', async () => {
    let resolveClaim
    leadsApi.claimLead.mockReturnValue(
      new Promise((resolve) => {
        resolveClaim = resolve
      })
    )

    const onClaimSuccess = vi.fn()
    const onSelect = vi.fn()

    render(
      <LeadTable
        currentUser={researcherUser}
        onClaimSuccess={onClaimSuccess}
        onSelect={onSelect}
      />
    )

    await waitFor(() => {
      expect(screen.getByText('Tech Innovators')).toBeTruthy()
    })

    const claimBtn = screen.getByRole('button', { name: /Claim lead Tech Innovators/i })
    fireEvent.click(claimBtn)

    // 1. Optimistic update: instantly renders "You"
    const badges = screen.getAllByText('You')
    expect(badges.length).toBe(2) // 1 from sampleLeadClaimedByMe, 1 from optimistic update

    // e.stopPropagation ensures row onSelect was NOT triggered
    expect(onSelect).not.toHaveBeenCalled()

    // 2. Resolve API claim
    resolveClaim({
      id: 'lead-unassigned',
      assigned_to: researcherUser.id,
      status: 'verified',
    })

    await waitFor(() => {
      expect(onClaimSuccess).toHaveBeenCalledWith(
        'lead-unassigned',
        expect.objectContaining({ assigned_to: researcherUser.id })
      )
    })

    // State is retained
    expect(screen.getAllByText('You').length).toBe(2)
  })

  it('reverts optimistic update and displays ErrorBanner when claim fails (HTTP 500)', async () => {
    leadsApi.claimLead.mockRejectedValue({
      status: 500,
      code: 'INTERNAL_ERROR',
      message: 'Database connection failed',
    })

    render(<LeadTable currentUser={researcherUser} />)

    await waitFor(() => {
      expect(screen.getByText('Tech Innovators')).toBeTruthy()
    })

    const claimBtn = screen.getByRole('button', { name: /Claim lead Tech Innovators/i })
    fireEvent.click(claimBtn)

    // Reverts state on error
    await waitFor(() => {
      expect(screen.getByText('Database connection failed')).toBeTruthy()
    })

    // Restores "Unassigned" text and Claim button
    expect(screen.getByRole('button', { name: /Claim lead Tech Innovators/i })).toBeTruthy()
    expect(screen.getAllByText('You').length).toBe(1) // Only sampleLeadClaimedByMe remains
  })

  it('reverts optimistic update and displays conflict error banner when HTTP 409 Conflict occurs', async () => {
    leadsApi.claimLead.mockRejectedValue({
      status: 409,
      code: 'CONFLICT',
      message: 'Lead has already been claimed by another team member.',
    })

    render(<LeadTable currentUser={researcherUser} />)

    await waitFor(() => {
      expect(screen.getByText('Tech Innovators')).toBeTruthy()
    })

    const claimBtn = screen.getByRole('button', { name: /Claim lead Tech Innovators/i })
    fireEvent.click(claimBtn)

    // Displays 409 Conflict error message
    await waitFor(() => {
      expect(
        screen.getByText('Lead has already been claimed by another team member.')
      ).toBeTruthy()
    })

    // Restored previous state
    expect(screen.getByRole('button', { name: /Claim lead Tech Innovators/i })).toBeTruthy()
    expect(screen.getAllByText('You').length).toBe(1)
  })
})
