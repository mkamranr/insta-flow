import { apiGet, apiPost } from './client'

export interface User {
  id: string
  email: string
  is_active: boolean
  last_login_at: string | null
}

export const getMe = () => apiGet<User>('/api/auth/me')
export const login = (email: string, password: string) =>
  apiPost<User>('/api/auth/login', { email, password })
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
