import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { dismissSetup, getProgress } from '../../api/onboarding'

/**
 * "Finish setting up", on every route until an Instagram account is connected.
 *
 * Driven by `/api/onboarding/progress`, which is ungated, and NOT by
 * `summary.connected`. That distinction is load-bearing:
 * `GET /api/analytics/summary` requires `analytics.view` and
 * `/api/instagram/accounts` requires `instagram.view`, so a narrow role gets
 * 403 from both and reads as "never connected" forever -- and a freshly
 * invited person is exactly that role.
 *
 * It clears by connecting, which is the outcome the wizard exists to produce
 * and cannot be faked, or by an explicit Hide. Ticking every step deliberately
 * does not clear it: a banner that vanished while nothing worked would be the
 * worst of both.
 */
export function SetupBanner() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['onboarding', 'progress'],
    queryFn: getProgress,
    retry: false,
  })
  const hide = useMutation({
    mutationFn: dismissSetup,
    onSuccess: (updated) =>
      queryClient.setQueryData(['onboarding', 'progress'], updated),
  })

  if (!data || data.connected || data.dismissed) return null

  return (
    <div className="card warn-card">
      <div>
        <strong>Finish setting up.</strong>{' '}
        <span className="muted">
          {data.done.length} of {data.total} steps done — no Instagram account is connected
          yet, so nothing will fire.
        </span>
      </div>
      <div className="row">
        <Link to="/get-started">
          <button className="cta">Continue setup</button>
        </Link>
        <button className="link" disabled={hide.isPending} onClick={() => hide.mutate()}>
          Hide
        </button>
      </div>
    </div>
  )
}
