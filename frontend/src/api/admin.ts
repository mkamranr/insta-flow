import type { RoleSummary, User } from './auth'
import { apiDelete, apiGet, apiPatch, apiPost } from './client'

/** One cell of the permission grid. `permission` is always `module.action`. */
export interface CatalogueAction {
  key: string
  label: string
  permission: string
}

export interface CatalogueModule {
  key: string
  label: string
  description: string
  /** Only the actions this module actually supports — the grid is sparse. */
  actions: CatalogueAction[]
}

export interface Catalogue {
  actions: { key: string; label: string }[]
  modules: CatalogueModule[]
}

export interface Role {
  id: string
  slug: string
  name: string
  description: string
  permissions: string[]
  is_system: boolean
  user_count: number
}

export interface RoleInput {
  name: string
  description: string
  permissions: string[]
}

/**
 * The catalogue comes from the server so the grid is never hardcoded here. A
 * module that gains an action ships without a frontend change.
 */
export const getCatalogue = () => apiGet<Catalogue>('/api/permissions')

export const listRoles = () => apiGet<Role[]>('/api/roles')
export const getRole = (id: string) => apiGet<Role>(`/api/roles/${id}`)
export const createRole = (input: RoleInput) => apiPost<Role>('/api/roles', input)
export const patchRole = (id: string, input: Partial<RoleInput>) =>
  apiPatch<Role>(`/api/roles/${id}`, input)
export const deleteRole = (id: string) => apiDelete<void>(`/api/roles/${id}`)

export const listUsers = () => apiGet<User[]>('/api/users')
export const createUser = (input: { email: string; password: string; role_id: string }) =>
  apiPost<User>('/api/users', input)
export const patchUser = (id: string, input: { role_id?: string; is_active?: boolean }) =>
  apiPatch<User>(`/api/users/${id}`, input)
export const resetUserPassword = (id: string, password: string) =>
  apiPost<void>(`/api/users/${id}/password`, { password })
export const deleteUser = (id: string) => apiDelete<void>(`/api/users/${id}`)

export type { RoleSummary, User }
