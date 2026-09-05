import { apiGet } from './client'

/**
 * A workspace is one user, the Instagram account they connected, and the
 * `/u/<slug>/` prefix both live under.
 */
export interface Workspace {
  slug: string
  email: string
  is_own: boolean
  username: string | null
  profile_picture_url: string | null
  connected: boolean
}

/**
 * Always at least your own. Everyone's, when you hold `workspaces.view` — which
 * is what decides whether the sidebar shows a switcher or the single account it
 * always showed.
 */
export const listWorkspaces = () => apiGet<Workspace[]>('/api/workspaces')
