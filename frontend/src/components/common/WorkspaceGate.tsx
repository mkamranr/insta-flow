import { useQuery } from '@tanstack/react-query'
import { type ReactNode, useEffect } from 'react'
import { getMe } from '../../api/auth'
import { listWorkspaces } from '../../api/workspaces'
import { workspaceSlug, workspaceUrl } from '../../app/workspace'

/**
 * Makes sure the URL names a workspace this person can actually open.
 *
 * Runs inside RequireAuth, so `['me']` is already resolved and there is no
 * second round trip before the first paint.
 *
 * Two cases:
 *
 * * **No prefix at all** — a bookmark from before workspaces existed, or a bare
 *   `/`. Redirected to your own workspace so the address bar and the server
 *   agree about whose data is on screen. A full document load, because
 *   `basename` is fixed for the life of the app.
 * * **A prefix naming a workspace you cannot open** — a stale link, or somebody
 *   else's. Explained in place rather than redirected: a redirect destroys the
 *   URL the person needs in order to ask for access, the same reasoning as
 *   `RequirePermission`.
 */
export function WorkspaceGate({ children }: { children: ReactNode }) {
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe })
  const { data: workspaces, isLoading } = useQuery({
    queryKey: ['workspaces'],
    queryFn: listWorkspaces,
    retry: false,
  })

  const ownSlug = me?.slug ?? null
  const needsRedirect = !workspaceSlug && Boolean(ownSlug)

  useEffect(() => {
    if (!needsRedirect || !ownSlug) return
    window.location.replace(
      workspaceUrl(ownSlug, window.location.pathname) + window.location.search,
    )
  }, [needsRedirect, ownSlug])

  if (needsRedirect) return <div className="centered muted">Loading…</div>

  // A user still awaiting the slug backfill has no workspace URL to send them
  // to. The unprefixed layout still works, so serve it rather than blocking.
  if (!workspaceSlug) return <>{children}</>

  if (isLoading || !workspaces) return <div className="centered muted">Loading…</div>

  if (!workspaces.some((w) => w.slug === workspaceSlug)) {
    return (
      <div className="centered">
        <div className="card" style={{ maxWidth: 420 }}>
          <h2>No such workspace</h2>
          <p className="muted">
            <code>/u/{workspaceSlug}/</code> is not a workspace you can open. It may have been
            renamed, or it may belong to someone else.
          </p>
          {ownSlug && (
            <p>
              <a className="link" href={workspaceUrl(ownSlug)}>
                Go to your workspace
              </a>
            </p>
          )}
        </div>
      </div>
    )
  }

  return <>{children}</>
}
