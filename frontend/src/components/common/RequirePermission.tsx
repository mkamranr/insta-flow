import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { usePermissions } from '../../hooks/usePermissions'

/**
 * Gate for a route the signed-in user may not be allowed to open.
 *
 * Renders in place rather than redirecting, matching RequireAuth: a redirect
 * turns a bookmarked link into a silent detour that reads as a bug, and it
 * destroys the URL the person needs in order to ask an administrator for access.
 *
 * UX only. Every route behind this still calls an API that re-checks the
 * permission server-side and returns 403.
 */
export function RequirePermission({ perm, children }: { perm: string; children: ReactNode }) {
  const { can, ready } = usePermissions()
  if (!ready) return <p className="muted">Loading…</p>
  if (!can(perm)) return <NoAccess />
  return <>{children}</>
}

/** Also used for section-level denial inside a page you can otherwise open. */
export function NoAccess({ what = 'this page' }: { what?: string }) {
  return (
    <div className="card">
      <h2>You do not have access</h2>
      <p className="muted">
        Your role does not include permission to view {what}. Ask an administrator to grant it,
        or go back to <Link to="/">Home</Link>.
      </p>
    </div>
  )
}
