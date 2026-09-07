import { apiDelete, apiGet, apiPost, apiPut } from './client'

/** The per-workspace Meta app, and the URLs it must be registered with. */
export interface MetaAppState {
  configured: boolean
  source: 'workspace' | 'instance' | 'unknown'
  app_id: string
  secret_set: boolean
  verify_token: string
  /** What to register in your own Meta app. Always workspace-prefixed. */
  urls: {
    webhook_url: string
    redirect_uri: string
    deauthorize_url: string
    privacy_url: string
    data_deletion_url: string
  }
  /** What is in force right now — differs only while on the instance-wide app. */
  active_urls: { webhook_url: string }
  workspace: { slug: string | null; is_own: boolean }
  using_instance_app: boolean
  has_own_row: boolean
  note?: string
}

/** Moved out of `pages/Settings.tsx` so the Settings page and the guided setup
 *  share one query key and one set of calls. Saving the App ID in the wizard
 *  therefore updates the Settings card, and vice versa, with no second source
 *  of truth to keep in step. */
export const getMetaApp = () => apiGet<MetaAppState>('/api/settings/meta-app')

export const saveMetaApp = (app_id: string, app_secret: string) =>
  apiPut<MetaAppState>('/api/settings/meta-app', { app_id, app_secret })

export const regenerateVerifyToken = () =>
  apiPost<MetaAppState>('/api/settings/meta-app/verify-token')

export const clearMetaApp = () => apiDelete('/api/settings/meta-app')
