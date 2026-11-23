import React, { useState, useMemo } from 'react'
import { LeadTable } from '../LeadPipeline/LeadTable'
import LeadDetailDrawer from '../LeadPipeline/LeadDetailDrawer'
import { Button } from '../../components/ui/Button'
import { EmptyState } from '../../components/feedback/Spinner'

/**
 * MyLeadsPage (FE-14)
 *
 * Displays fresher opportunities claimed by or assigned to the currently authenticated user.
 * Reuses the existing LeadTable and LeadDetailDrawer components.
 */
export default function MyLeadsPage({ currentUser }) {
  const [selectedLead, setSelectedLead] = useState(null)
  const [refreshKey, setRefreshKey] = useState(0)

  const tableFilters = useMemo(() => {
    return {
      assigned_to: currentUser?.id,
    }
  }, [currentUser?.id])

  const handleLeadSelect = (leadId, leadObj) => {
    setSelectedLead(leadObj || leadId)
  }

  return (
    <div
      style={{
        padding: '24px',
        display: 'flex',
        flexDirection: 'column',
        gap: '20px',
        minWidth: 0,
        maxWidth: '100%',
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '12px',
        }}
      >
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 700 }}>
            My Assigned Leads
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>
            Review and manage fresher opportunities claimed by or assigned to you.
          </p>
        </div>

        {currentUser?.id && (
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <Button variant="secondary" onClick={() => setRefreshKey((k) => k + 1)}>
              Refresh
            </Button>
          </div>
        )}
      </div>

      {!currentUser?.id ? (
        <EmptyState
          title="User information unavailable"
          message="Unable to load assigned leads because your user session information is unavailable. Please log in again."
        />
      ) : (
        <LeadTable
          filters={tableFilters}
          currentUser={currentUser}
          currentUserId={currentUser.id}
          selectedId={typeof selectedLead === 'object' ? selectedLead?.id : selectedLead}
          refreshKey={refreshKey}
          onSelect={handleLeadSelect}
          emptyTitle="No leads assigned to you"
          emptyMessage="You have not claimed any leads yet. Visit the Lead Pipeline to discover and claim opportunities."
        />
      )}

      <LeadDetailDrawer
        lead={selectedLead}
        isOpen={!!selectedLead}
        onClose={() => setSelectedLead(null)}
      />
    </div>
  )
}
