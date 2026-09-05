/**
 * Which workspace is on screen, read from the URL.
 *
 * The URL is the single source of truth. There is no "selected workspace" in
 * React state and none in a cookie, because the same segment is what the server
 * uses to pick a Meta app — a switcher that disagreed with the address bar would
 * mean the page and its API calls were looking at two different accounts.
 *
 * Read once at module load, deliberately. `BrowserRouter`'s `basename` cannot
 * change without remounting the whole tree, so switching workspaces is a full
 * navigation rather than a client-side route change.
 */

/** Matches `URL_PREFIX` in `backend/app/services/slugs.py`. */
export const PREFIX = '/u'

function readSlug(): string | null {
  const segments = window.location.pathname.split('/')
  return segments[1] === 'u' && segments[2] ? segments[2] : null
}

export const workspaceSlug = readSlug()

/**
 * Prepended to every route and every API call.
 *
 * Empty on the unprefixed layout, which still has to work: it is what the
 * already-live Meta app calls and what a pre-workspace bookmark points at.
 */
export const basename = workspaceSlug ? `${PREFIX}/${workspaceSlug}` : ''

/** The absolute URL of a page inside a workspace. */
export function workspaceUrl(slug: string, path = '/'): string {
  return `${PREFIX}/${slug}${path.startsWith('/') ? path : `/${path}`}`.replace(/\/$/, '') || '/'
}

/**
 * Move to another workspace, keeping the page you are on.
 *
 * A full document load, not a router navigation: `basename` is fixed for the
 * life of the app, and the API client reads it at module scope too.
 */
export function switchWorkspace(slug: string): void {
  const path = window.location.pathname.slice(basename.length) || '/'
  window.location.assign(workspaceUrl(slug, path))
}
