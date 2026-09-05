import { useQuery } from '@tanstack/react-query'
import { apiGetInstance } from '../../api/client'
import type { ReadyResponse } from '../../types/health'

/**
 * §36 requires dry-run mode to be visible at all times, and §37 an explicit
 * warning once real sending is live. Both states are shown, so the operator can
 * never be unsure which one they are in.
 */
export function DryRunBanner() {
  const { data } = useQuery({
    queryKey: ['ready'],
    queryFn: () => apiGetInstance<ReadyResponse>('/ready'),
    refetchInterval: 30_000,
  })

  if (!data) return null

  return data.dry_run ? (
    <div className="card warn-card">
      <span>
        <strong>Dry run.</strong> Everything runs and is recorded, but no Instagram message is
        sent.
      </span>
    </div>
  ) : (
    <div className="card err" style={{ display: 'flex', gap: 12 }}>
      <span>
        <strong>Live.</strong> Matching comments trigger real Instagram DMs from your account.
      </span>
    </div>
  )
}
