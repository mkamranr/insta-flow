import type { RoleSummary } from './auth'
import { apiGet, apiPost } from './client'

export interface Invite {
  id: string
  email: string
  status: 'pending' | 'expired' | 'accepted' | 'revoked'
  expires_at: string
  created_at: string
  last_sent_at: string | null
  sent_count: number
  accepted_at: string | null
  role: RoleSummary | null
  invited_by_email: string | null
  invited_by_active: boolean
  last_send_status: string | null
  last_send_error: string | null
  /** Only ever present in the response that mints the link, so an install with
   *  no mail configured is still usable. Never read back from the database. */
  url: string | null
}

export interface InviteResult {
  sent: Invite[]
  skipped: { email: string; reason: string }[]
}

export interface InviteLookup {
  email: string
  expires_at: string
  suggested_slug: string
}

export const SKIP_REASONS: Record<string, string> = {
  already_registered: 'Already has an account',
  already_invited: 'Already invited and the link is still live',
  invalid: 'Not a usable email address',
  send_failed: 'Invite created, but the email could not be queued',
}

export const listInvites = (status?: string) =>
  apiGet<{ invites: Invite[] }>(
    status ? `/api/invites?invite_status=${encodeURIComponent(status)}` : '/api/invites',
  ).then((r) => r.invites)

export const sendInvites = (emails: string[], role_id?: string) =>
  apiPost<InviteResult>('/api/invites', role_id ? { emails, role_id } : { emails })

export const resendInvite = (id: string) => apiPost<Invite>(`/api/invites/${id}/resend`)

export const revokeInvite = (id: string) => apiPost<Invite>(`/api/invites/${id}/revoke`)

/** Unauthenticated. The token goes in the body, never the URL: a reverse proxy
 *  logs request paths and query strings, and this one mints an account. */
export const lookupInvite = (token: string) =>
  apiPost<InviteLookup>('/api/invite/lookup', { token })

export const acceptInvite = (
  token: string,
  password: string,
  password_confirm: string,
  slug: string,
) => apiPost<{ slug: string }>('/api/invite/accept', { token, password, password_confirm, slug })

// Mirrored from `services/slugs.py`. Kept pure and exported for the reason
// `PermissionGrid.togglePermission` is: this repo has no frontend test runner
// yet, so the riskiest logic stays in a function that can be covered the day
// it grows one.
export const SLUG_MAX = 32
const SLUG_PATTERN = /^[a-z0-9][a-z0-9-]{1,31}$/
const RESERVED = new Set([
  'u', 'api', 'webhooks', 'privacy', 'data-deletion', 'health', 'ready', 'r',
  'files', 'invite', 'login', 'logout', 'setup', 'admin', 'assets', 'static',
  'well-known',
])

export function slugProblem(slug: string): string | null {
  if (!slug) return 'Choose a web address.'
  if (!SLUG_PATTERN.test(slug) || slug.endsWith('-') || slug.includes('--')) {
    return '2 to 32 characters: lowercase letters, numbers and single hyphens, starting and ending with a letter or number.'
  }
  if (RESERVED.has(slug)) return `“${slug}” is reserved. Pick another.`
  return null
}
