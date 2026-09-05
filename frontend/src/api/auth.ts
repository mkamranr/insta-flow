import { apiGet, apiPost } from './client'

/** Mirrors MIN_PASSWORD_LENGTH in backend/app/services/security/passwords.py. */
export const MIN_PASSWORD = 12

/** A role as it appears on a user. Never carries the permission list. */
export interface RoleSummary {
  id: string
  slug: string
  name: string
}

export interface User {
  id: string
  email: string
  /** The `/u/<slug>/` segment this user's workspace lives at. */
  slug: string | null
  is_active: boolean
  role: RoleSummary | null
  last_login_at: string | null
}

/**
 * Your own identity. `permissions` is the fully expanded list -- the server does
 * any wildcard expansion, so the client only ever does an exact set lookup.
 */
export interface Me extends User {
  permissions: string[]
}

export const getMe = () => apiGet<Me>('/api/auth/me')
export const login = (email: string, password: string) =>
  apiPost<User>('/api/auth/login', { email, password })

export const changeOwnPassword = (current_password: string, password: string) =>
  apiPost<void>('/api/auth/password', { current_password, password })
export const logout = () => apiPost<void>('/api/auth/logout')

/** Whether this deployment still needs its first admin. */
export const getSetupStatus = () => apiGet<{ required: boolean }>('/api/auth/setup')

/**
 * Check the setup code before showing the form. Grants nothing — the create
 * call re-checks it — so this is purely to fail early on a wrong code rather
 * than after the operator has chosen a password.
 */
export const verifySetupSecret = (secret: string) =>
  apiPost<{ ok: boolean }>('/api/auth/setup/verify', { secret })

export const createFirstAdmin = (input: {
  secret: string
  email: string
  password: string
  password_confirm: string
}) => apiPost<User>('/api/auth/setup', input)
