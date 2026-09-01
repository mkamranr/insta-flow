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
