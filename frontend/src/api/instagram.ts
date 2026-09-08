import { apiDelete, apiGet, apiPost } from './client'

/** Note there is no token field: the API never returns one. */
export interface InstagramAccount {
  id: string
  instagram_user_id: string
  username: string
  profile_picture_url?: string | null
  display_name: string | null
  account_type: string | null
  permissions: string[]
  status: string
  status_error: string | null
  webhook_subscribed: boolean
  subscribed_fields: string[]
  token_expires_at: string | null
  token_expired: boolean
  automation_paused: boolean
  automation_paused_until: string | null
  automation_paused_at: string | null
  /** Meta's own total, so a resync can report "30 of 30" rather than only how
   *  many it happened to fetch. */
  media_count?: number | null
  media_resync_state?: 'running' | 'done' | 'failed' | null
  media_resync_started_at?: string | null
  media_resync_finished_at?: string | null
  media_resync_count?: number | null
  media_resync_pages?: number | null
  media_resync_error?: string | null
}

export const listAccounts = () => apiGet<InstagramAccount[]>('/api/instagram/accounts')
export const startConnect = () =>
  apiGet<{ authorize_url: string; redirect_uri: string }>('/api/auth/instagram/start')
export const subscribeWebhooks = (id: string) =>
  apiPost<InstagramAccount>(`/api/instagram/${id}/subscribe`)
export const checkSubscription = (id: string) =>
  apiGet<{ subscribed: boolean; fields: string[]; error?: string }>(
    `/api/instagram/${id}/subscription`,
  )
export const refreshToken = (id: string) => apiPost<InstagramAccount>(`/api/instagram/${id}/refresh`)
export const disconnect = (id: string) => apiDelete<void>(`/api/instagram/${id}`)

/** Queues a full walk of the media edge. `POST /api/posts/sync` stays at one
 *  page — this is the manual escape hatch for an edit to an older post. */
export const resyncAllPosts = () => apiPost<{ queued: boolean }>('/api/posts/resync')
