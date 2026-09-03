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
