import React, { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight } from 'lucide-react'
import { Badge } from '../../components/ui/Badge'
import { ScoreBadge } from '../../components/score/ScoreBadge'
import { Spinner, EmptyState, ErrorBanner } from '../../components/feedback/Spinner'
import {
  listLeads,
  claimLead,
  DEFAULT_PAGE_SIZE,
  DEFAULT_SORT_BY,
  DEFAULT_SORT_ORDER,
  MAX_PAGE_SIZE,
} from '../../api/leads'
import { can } from '../../utils/roles'

/**
 * LeadTable (FE-09)
 *
 * Server-side paginated + sortable CRM table.
 *
 * Props:
 *  - onSelect(leadId)  Called when a row is clicked (or Enter/Space on a focused row).
 *  - filters           Optional object forwarded to the API as query params (used by FE-10).
 *                      Changing it resets the table to page 1.
 *  - currentUserId     Used to render "You" in the Assigned To column.
 *  - selectedId        Optional id of the currently open lead, highlighted in the table.
 *  - refreshKey        Change this value to force a re-fetch of the current page.
 *  - initialPageSize   Defaults to 25 (max 100, per contracts/openapi.yaml).
 */

const PAGE_SIZE_OPTIONS = [10, 25, 50, MAX_PAGE_SIZE]

// key = value sent as `sort_by`
const COLUMNS = [
  { key: 'organization_name', label: 'Organization' },
  { key: 'title', label: 'Job Title' },
  { key: 'source_name', label: 'Source' },
  { key: 'experience_min', label: 'Experience Range' },
  { key: 'score', label: 'Quality Score' },
  { key: 'status', label: 'Contact Status' },
  { key: 'assigned_to', label: 'Assigned To' },
  { key: 'discovered_at', label: 'Discovered' },
]

const STATUS_VARIANTS = {
  new: 'neutral',
  needs_enrichment: 'warning',
  verified: 'success',
  draft_ready: 'info',
  approved: 'info',
  sent: 'success',
  closed: 'danger',
}

const TABLE_CSS = `
.lt-scroll {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overscroll-behavior-x: contain;
  -webkit-overflow-scrolling: touch;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-bg-surface);
}
.lt-scroll:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.lt-table {
  width: 100%;
  min-width: 1000px; /* below this width the wrapper scrolls instead of squeezing columns */
  border-collapse: collapse;
  text-align: left;
  font-size: 14px;
}
.lt-table thead { background: var(--color-bg-subtle); border-bottom: 1px solid var(--color-border); }
.lt-th { padding: 0; white-space: nowrap; }
.lt-sort-btn {
  display: flex; align-items: center; gap: 6px; width: 100%;
  padding: 12px 16px; border: none; background: none;
  font: inherit; font-weight: 600; color: var(--color-text-muted);
  text-align: left; cursor: pointer;
}
.lt-sort-btn:hover { color: var(--color-text-main); }
.lt-sort-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -2px; }
.lt-th[aria-sort="ascending"] .lt-sort-btn,
.lt-th[aria-sort="descending"] .lt-sort-btn { color: var(--color-primary); }
.lt-row { cursor: pointer; border-bottom: 1px solid var(--color-border); transition: background-color 0.1s ease; }
.lt-row:last-child { border-bottom: none; }
.lt-row:hover { background: var(--color-bg-subtle); }
.lt-row:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -2px; }
.lt-row[data-selected="true"] { background: var(--color-primary-light); }
.lt-td { padding: 12px 16px; vertical-align: middle; }
.lt-nowrap { white-space: nowrap; }
.lt-title { max-width: 280px; }
.lt-pagination {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 12px; margin-top: 12px; font-size: 13px; color: var(--color-text-muted);
}
.lt-pager { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.lt-page-btn {
  min-width: 32px; height: 32px; padding: 0 8px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid var(--color-border); border-radius: var(--radius-md);
  background: var(--color-bg-surface); color: var(--color-text-main);
  font-size: 13px; cursor: pointer;
}
.lt-page-btn:hover:not(:disabled) { border-color: var(--color-border-hover); background: var(--color-bg-subtle); }
.lt-page-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.lt-page-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.lt-page-btn[aria-current="page"] {
  background: var(--color-primary); border-color: var(--color-primary); color: #fff; font-weight: 600;
}
.lt-size-select {
  height: 32px; padding: 0 8px; border: 1px solid var(--color-border);
  border-radius: var(--radius-md); background: var(--color-bg-surface);
  color: var(--color-text-main); font-size: 13px;
}
.lt-claim-btn {
  padding: 3px 8px;
  font-size: 12px;
  font-weight: 600;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-primary);
  background-color: transparent;
  color: var(--color-primary);
  cursor: pointer;
  transition: all 0.15s ease;
}
.lt-claim-btn:hover:not(:disabled) {
  background-color: var(--color-primary);
  color: #ffffff;
}
.lt-claim-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
@media (max-width: 1023px) {
  .lt-pagination { justify-content: center; }
}
`

/* ----------------------------------------------------------------------------
 * Helpers
 * ------------------------------------------------------------------------- */

function formatExperience(min, max) {
  const hasMin = min !== null && min !== undefined
  const hasMax = max !== null && max !== undefined
  if (!hasMin && !hasMax) return '—'
  if (hasMax && max === 0) return 'Fresher'
  if (hasMin && hasMax && min === max) return `${min} yr${min === 1 ? '' : 's'}`
  if (hasMax) return `${min ?? 0}–${max} yrs`
  return `${min}+ yrs`
}

function formatStatus(status) {
  return (status || 'unknown').replace(/_/g, ' ')
}

function formatDate(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

function getPageItems(page, totalPages) {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, i) => i + 1)
  const items = [1]
  const start = Math.max(2, page - 1)
  const end = Math.min(totalPages - 1, page + 1)
  if (start > 2) items.push('gap-left')
  for (let p = start; p <= end; p += 1) items.push(p)
  if (end < totalPages - 1) items.push('gap-right')
  items.push(totalPages)
  return items
}

function SortIcon({ active, order }) {
  if (!active) return <ArrowUpDown size={14} aria-hidden="true" style={{ opacity: 0.5 }} />
  return order === 'asc' ? <ArrowUp size={14} aria-hidden="true" /> : <ArrowDown size={14} aria-hidden="true" />
}

/* ----------------------------------------------------------------------------
 * Component
 * ------------------------------------------------------------------------- */

export function LeadTable({
  onSelect,
  filters,
  currentUser,
  currentUserId: propCurrentUserId,
  userRole: propUserRole,
  selectedId,
  refreshKey = 0,
  initialPageSize = DEFAULT_PAGE_SIZE,
  emptyTitle = 'No leads match your criteria',
  emptyMessage = 'Try adjusting your search terms or filters.',
  onClaimSuccess,
}) {
  const currentUserId = currentUser?.id || propCurrentUserId
  const userRole = currentUser?.role || propUserRole
  const canClaim = Boolean(userRole) && can(userRole, 'claim_leads')

  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(Math.min(initialPageSize, MAX_PAGE_SIZE))
  const [sortBy, setSortBy] = useState(DEFAULT_SORT_BY)
  const [sortOrder, setSortOrder] = useState(DEFAULT_SORT_ORDER)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [claimError, setClaimError] = useState(null)
  const [claimingLeadIds, setClaimingLeadIds] = useState(() => new Set())

  // Reset to page 1 whenever the filters change (adjusting state during render
  // avoids a wasted fetch for the old page).
  const filtersKey = JSON.stringify(filters || {})
  const [prevFiltersKey, setPrevFiltersKey] = useState(filtersKey)
  if (filtersKey !== prevFiltersKey) {
    setPrevFiltersKey(filtersKey)
    setPage(1)
  }

  const load = useCallback(
    async (isCancelled) => {
      setLoading(true)
      setError(null)
      try {
        const result = await listLeads({
          page,
          pageSize,
          sortBy,
          sortOrder,
          filters: JSON.parse(filtersKey),
        })
        if (isCancelled()) return
        setData(result)
        // Server may clamp the page (e.g. fewer results after a filter change).
        if (result.page !== page) setPage(result.page)
      } catch (err) {
        if (isCancelled()) return
        setError(err?.message || 'Failed to load leads')
      } finally {
        if (!isCancelled()) setLoading(false)
      }
    },
    [page, pageSize, sortBy, sortOrder, filtersKey]
  )

  // `refreshKey` is intentionally part of the deps so the parent can force a reload.
  useEffect(() => {
    let cancelled = false
    load(() => cancelled)
    return () => {
      cancelled = true // ignore out-of-order responses from stale requests
    }
  }, [load, refreshKey])

  async function handleClaim(e, lead) {
    if (e) {
      e.preventDefault()
      e.stopPropagation()
    }
    if (!canClaim || !currentUserId || !lead?.id) return
    if (claimingLeadIds.has(lead.id)) return

    setClaimError(null)
    setClaimingLeadIds((prev) => new Set(prev).add(lead.id))

    // 1. Optimistic update: mark as assigned to current user immediately
    const prevAssignedTo = lead.assigned_to
    setData((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        items: prev.items.map((item) =>
          item.id === lead.id ? { ...item, assigned_to: currentUserId } : item
        ),
      }
    })

    try {
      // 2. Send claim request to backend (self-claims send empty body per API contract)
      const updated = await claimLead(lead.id)

      // 3. Retain updated state if successful
      if (updated && updated.assigned_to) {
        setData((prev) => {
          if (!prev) return prev
          return {
            ...prev,
            items: prev.items.map((item) =>
              item.id === lead.id
                ? {
                    ...item,
                    assigned_to: updated.assigned_to,
                    status: updated.status || item.status,
                  }
                : item
            ),
          }
        })
      }
      onClaimSuccess?.(lead.id, updated)
    } catch (err) {
      // 4. Restore previous state if unsuccessful
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          items: prev.items.map((item) =>
            item.id === lead.id ? { ...item, assigned_to: prevAssignedTo } : item
          ),
        }
      })

      // 5 & 6. Display clear error banner, handling 409 Conflict specifically
      const message =
        err?.status === 409
          ? 'Lead has already been claimed by another team member.'
          : err?.message || 'Failed to claim lead. Please try again.'
      setClaimError(message)
    } finally {
      setClaimingLeadIds((prev) => {
        const next = new Set(prev)
        next.delete(lead.id)
        return next
      })
    }
  }

  function handleSort(columnKey) {
    if (columnKey === sortBy) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(columnKey)
      setSortOrder('asc')
    }
    setPage(1)
  }

  function handlePageSizeChange(e) {
    setPageSize(Number(e.target.value))
    setPage(1)
  }

  function handleRowKeyDown(e, leadId, lead) {
    if (e.target !== e.currentTarget) return
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onSelect?.(leadId, lead)
    }
  }

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = data?.total_pages ?? 1
  // Derive the range from the response (not local state) so the label always
  // matches the rows on screen while the next page is loading.
  const shownPage = data?.page ?? page
  const shownSize = data?.page_size ?? pageSize
  const firstShown = total === 0 ? 0 : (shownPage - 1) * shownSize + 1
  const lastShown = Math.min(shownPage * shownSize, total)

  /* ---- first load / error / empty states ---- */

  if (!data && loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '48px' }}>
        <Spinner size={32} />
      </div>
    )
  }

  if (!data && error) {
    return <ErrorBanner message={error} onRetry={() => load(() => false)} />
  }

  return (
    <div>
      <style>{TABLE_CSS}</style>

      {error && <ErrorBanner message={error} onRetry={() => load(() => false)} />}
      {claimError && (
        <div style={{ marginBottom: '12px' }}>
          <ErrorBanner message={claimError} />
        </div>
      )}

      {total === 0 ? (
        <EmptyState
          title={emptyTitle}
          message={emptyMessage}
        />
      ) : (
        <div
          className="lt-scroll"
          role="region"
          aria-label="Leads table (scrollable)"
          tabIndex={0}
        >
          <table className="lt-table" aria-busy={loading}>
            <thead>
              <tr>
                {COLUMNS.map((col) => {
                  const active = sortBy === col.key
                  const ariaSort = active ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'
                  return (
                    <th key={col.key} className="lt-th" scope="col" aria-sort={ariaSort}>
                      <button
                        type="button"
                        className="lt-sort-btn"
                        onClick={() => handleSort(col.key)}
                        title={`Sort by ${col.label}`}
                      >
                        {col.label}
                        <SortIcon active={active} order={sortOrder} />
                      </button>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody style={{ opacity: loading ? 0.55 : 1, transition: 'opacity 0.15s ease' }}>
              {items.map((lead) => {
                const isMine = currentUserId && lead.assigned_to === currentUserId
                const isClaiming = claimingLeadIds.has(lead.id)
                return (
                  <tr
                    key={lead.id}
                    className="lt-row"
                    tabIndex={0}
                    data-selected={selectedId === lead.id}
                    onClick={() => onSelect?.(lead.id, lead)}
                    onKeyDown={(e) => handleRowKeyDown(e, lead.id, lead)}
                  >
                    <td className="lt-td" style={{ fontWeight: 600 }}>
                      {lead.organization_name}
                    </td>
                    <td className="lt-td lt-title">
                      <div style={{ fontWeight: 500 }}>{lead.title}</div>
                      {lead.location && (
                        <div style={{ fontSize: '12px', color: 'var(--color-text-muted)' }}>
                          📍 {lead.location}
                        </div>
                      )}
                    </td>
                    <td className="lt-td lt-nowrap">
                      <Badge variant="info">{lead.source_name}</Badge>
                    </td>
                    <td className="lt-td lt-nowrap" style={{ fontSize: '13px' }}>
                      {formatExperience(lead.experience_min, lead.experience_max)}
                    </td>
                    <td className="lt-td lt-nowrap">
                      <ScoreBadge score={lead.score} band={lead.score_band} />
                    </td>
                    <td className="lt-td lt-nowrap">
                      <Badge variant={STATUS_VARIANTS[lead.status] || 'neutral'}>
                        <span style={{ textTransform: 'capitalize' }}>{formatStatus(lead.status)}</span>
                      </Badge>
                    </td>
                    <td className="lt-td lt-nowrap" style={{ fontSize: '13px' }}>
                      {isMine ? (
                        <Badge variant="success">You</Badge>
                      ) : lead.assigned_to ? (
                        <span style={{ color: 'var(--color-text-muted)' }} title={lead.assigned_to}>
                          {lead.assigned_to.length > 10 ? `${lead.assigned_to.slice(0, 8)}…` : lead.assigned_to}
                        </span>
                      ) : (
                        <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                          <span style={{ color: 'var(--color-text-light)' }}>Unassigned</span>
                          {canClaim && (
                            <button
                              type="button"
                              className="lt-claim-btn"
                              disabled={isClaiming}
                              onClick={(e) => handleClaim(e, lead)}
                              title="Claim this lead"
                              aria-label={`Claim lead ${lead.organization_name}`}
                            >
                              {isClaiming ? 'Claiming…' : 'Claim'}
                            </button>
                          )}
                        </div>
                      )}
                    </td>
                    <td
                      className="lt-td lt-nowrap"
                      style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}
                      title={lead.discovered_at}
                    >
                      {formatDate(lead.discovered_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div className="lt-pagination">
          <div aria-live="polite">
            Showing {firstShown}–{lastShown} of {total} leads
          </div>

          <nav className="lt-pager" aria-label="Pagination">
            <button
              type="button"
              className="lt-page-btn"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1 || loading}
              aria-label="Previous page"
            >
              <ChevronLeft size={16} aria-hidden="true" />
            </button>

            {getPageItems(page, totalPages).map((item) =>
              typeof item === 'string' ? (
                <span key={item} aria-hidden="true" style={{ padding: '0 4px' }}>
                  …
                </span>
              ) : (
                <button
                  key={item}
                  type="button"
                  className="lt-page-btn"
                  onClick={() => setPage(item)}
                  disabled={loading}
                  aria-label={`Page ${item}`}
                  aria-current={item === page ? 'page' : undefined}
                >
                  {item}
                </button>
              )
            )}

            <button
              type="button"
              className="lt-page-btn"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || loading}
              aria-label="Next page"
            >
              <ChevronRight size={16} aria-hidden="true" />
            </button>
          </nav>

          <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            Rows per page
            <select className="lt-size-select" value={pageSize} onChange={handlePageSizeChange}>
              {PAGE_SIZE_OPTIONS.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
    </div>
  )
}
