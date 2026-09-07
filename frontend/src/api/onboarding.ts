import { apiGet, apiPost, apiPut } from './client'

export type CopyKey =
  | 'webhook_url'
  | 'redirect_uri'
  | 'deauthorize_url'
  | 'privacy_url'
  | 'data_deletion_url'
  | 'verify_token'

export type EmbedKey = 'meta_app_credentials' | 'verify_token' | 'connect_instagram'
export type CheckKey = 'meta_app_saved' | 'instagram_connected' | 'webhooks_subscribed'

/** One piece of a step. The renderer switches exhaustively over `kind`, and
 *  assigns the default case to `never`, so `tsc` names any kind added on the
 *  server that the frontend does not yet draw. */
export type Block =
  | { kind: 'text' | 'note' | 'warn' | 'code'; text: string }
  | { kind: 'list'; items: string[]; ordered?: boolean }
  | { kind: 'table'; head: string[]; rows: string[][] }
  | { kind: 'links'; links: { label: string; href: string }[] }
  | { kind: 'copy'; value: CopyKey; label: string; hint?: string }
  | { kind: 'embed'; embed: EmbedKey }
  | { kind: 'check'; check: CheckKey; label: string }

export interface WizardStep {
  /** Stable forever: progress is stored under it. */
  id: string
  number: string
  title: string
  summary: string
  optional?: boolean
  body: Block[]
}

export interface WizardContent {
  intro: Block[]
  steps: WizardStep[]
  troubleshooting: Block[]
  /** Resolved server-side from `urls_for`, so the walkthrough cannot drift. */
  urls: Record<string, string>
}

export interface OnboardingProgress {
  done: string[]
  dismissed: boolean
  total: number
  connected: boolean
  meta_app_configured: boolean
  webhooks_subscribed: boolean
}

export const getWizardContent = () => apiGet<WizardContent>('/api/onboarding/steps')

export const getProgress = () => apiGet<OnboardingProgress>('/api/onboarding/progress')

export const setStepDone = (stepId: string, done: boolean) =>
  apiPut<OnboardingProgress>(`/api/onboarding/progress/${stepId}`, { done })

export const dismissSetup = () => apiPost<OnboardingProgress>('/api/onboarding/dismiss')
