import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { getMe, getSetupStatus } from '../../api/auth'
import { Login } from '../../pages/Login'
import { Setup } from '../../pages/Setup'

/**
 * Gate for authenticated routes. The session lives in an HttpOnly cookie, so the
 * only way to know whether we are signed in is to ask the server.
 *
 * When nobody is signed in we also ask whether this deployment has any admin at
 * all. If it does not, the first screen is setup rather than a login form the
 * operator could not possibly satisfy -- which is the whole point on a host
 * with no shell to run `make bootstrap-admin`.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['me'],
    queryFn: getMe,
    retry: false,
  })

  // Only asked once we know we are signed out, so a normal session costs no
  // extra request.
  const signedOut = isError || (!isLoading && !data)
  const { data: setup, isLoading: setupLoading } = useQuery({
    queryKey: ['setup-status'],
    queryFn: getSetupStatus,
    enabled: signedOut,
    retry: false,
  })

  if (isLoading) return <div className="centered muted">Loading…</div>
  if (signedOut) {
    if (setupLoading) return <div className="centered muted">Loading…</div>
    if (setup?.required) {
      return (
        <Setup
          onDone={() => {
            queryClient.invalidateQueries({ queryKey: ['me'] })
            queryClient.invalidateQueries({ queryKey: ['setup-status'] })
          }}
        />
      )
    }
    return <Login />
  }
  return <>{children}</>
}
