import { apiDelete, apiGet, apiPost } from './client'
import type { InstagramAccount } from './instagram'

/**
 * A pause, and what it is holding.
 *
 * `held` and `deliverable` differ on purpose. A pause can run for up to 30 days
 * but Instagram only accepts a reply within 7 days of the comment, so part of a
 * long backlog cannot be sent whatever the operator chooses. Anything offering
 * to send must quote `deliverable`.
 *
 * `paused_until` can be in the past: nothing runs at expiry, so a lapsed pause
 * still carries its old instant. Read `paused` for the state, never the date.
 */
export interface PauseStatus {
  paused: boolean
  paused_until: string | null
  paused_at: string | null
  held: number
  deliverable: number
}

/** What to do with the comments a pause held. */
export type BacklogChoice = 'deliver' | 'ignore'

export const getPauseStatus = (accountId: string) =>
  apiGet<PauseStatus>(`/api/instagram/${accountId}/pause`)

export const pauseAccount = (accountId: string, until: string) =>
  apiPost<InstagramAccount>(`/api/instagram/${accountId}/pause`, { until })

/** Ends the pause. Deliberately does not settle the backlog -- that is a
    separate decision, and it is still owed afterwards. */
export const resumeAccount = (accountId: string) =>
  apiDelete<InstagramAccount>(`/api/instagram/${accountId}/pause`)

export const actOnBacklog = (accountId: string, action: BacklogChoice) =>
  apiPost<{ status: string }>(`/api/instagram/${accountId}/pause/backlog`, { action })
