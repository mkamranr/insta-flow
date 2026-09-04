import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import {
  createRole,
  getCatalogue,
  getRole,
  patchRole,
  type Role,
  type RoleInput,
} from '../api/admin'
import type { ApiError } from '../api/client'
import { PermissionGrid } from '../components/admin/PermissionGrid'
import { usePermissions } from '../hooks/usePermissions'

const EMPTY: RoleInput = { name: '', description: '', permissions: [] }

export function RoleEditor() {
  const { id } = useParams()
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const isNew = id === undefined
  const seed = (location.state as { from?: Role } | null)?.from

  const { data: catalogue } = useQuery({
    queryKey: ['permissions'],
    queryFn: getCatalogue,
    staleTime: Infinity,
  })
  const { data: role, isLoading } = useQuery({
    queryKey: ['role', id],
    queryFn: () => getRole(id as string),
    enabled: !isNew,
  })

  const [draft, setDraft] = useState<RoleInput | null>(null)
  const [saved, setSaved] = useState(false)

  // The server is the source of truth until the operator types.
  const server: RoleInput = role
    ? { name: role.name, description: role.description, permissions: role.permissions }
    : seed
      ? {
          name: `${seed.name} (copy)`,
          description: seed.description,
          permissions: seed.permissions,
        }
      : EMPTY

  const current = draft ?? server
  // Locked because of what the role *is*, not who is looking at it — a built-in
  // role's permissions are re-synced on every boot, so an edit here would be
  // silently undone at the next restart.
  const locked = (role?.is_system ?? false) || !can('roles.manage')

  const dirty =
    draft !== null &&
    (draft.name !== server.name ||
      draft.description !== server.description ||
      draft.permissions.length !== server.permissions.length ||
      draft.permissions.some((p) => !server.permissions.includes(p)))

  const edit = (patch: Partial<RoleInput>) => {
    setSaved(false)
    setDraft({ ...current, ...patch })
  }

  const save = useMutation({
    mutationFn: () => (isNew ? createRole(current) : patchRole(id as string, current)),
    onSuccess: (result) => {
      setDraft(null)
      setSaved(true)
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      queryClient.invalidateQueries({ queryKey: ['role', result.id] })
      // Editing the role you are signed in under changes your own navigation.
      queryClient.invalidateQueries({ queryKey: ['me'] })
      if (isNew) navigate(`/roles/${result.id}`, { replace: true })
    },
  })

  if (isLoading || !catalogue) return <p className="muted">Loading…</p>

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{isNew ? 'New role' : (role?.name ?? 'Role')}</h1>
          <p className="muted">Choose what this role can see and change.</p>
        </div>
        <button className="secondary" onClick={() => navigate('/roles')}>
          Back to roles
        </button>
      </div>

      {role?.is_system && (
        <div className="card">
          <h2>This is a built-in role</h2>
          <p className="muted">
            Built-in roles are kept up to date automatically, so they cannot be edited — a
            change here would be undone the next time the app restarts. Duplicate it to get an
            editable copy with the same permissions.
          </p>
          {can('roles.manage') && (
            <button
              className="cta"
              onClick={() => navigate('/roles/new', { state: { from: role } })}
            >
              Duplicate this role
            </button>
          )}
        </div>
      )}

      <div className="card">
        <label htmlFor="role-name">Name</label>
        <input
          id="role-name"
          maxLength={60}
          value={current.name}
          disabled={locked}
          placeholder="Social manager"
          onChange={(e) => edit({ name: e.target.value })}
        />

        <label htmlFor="role-desc">Description</label>
        <input
          id="role-desc"
          maxLength={200}
          value={current.description}
          disabled={locked}
          placeholder="What this role is for, in one line."
          onChange={(e) => edit({ description: e.target.value })}
        />
        <p className="muted small">Shown on the roles list, so make it about the job.</p>
      </div>

      <div className="card">
        <h2>Permissions</h2>
        <PermissionGrid
          catalogue={catalogue}
          value={current.permissions}
          disabled={locked}
          onChange={(permissions) => edit({ permissions })}
        />

        {!locked && (
          <div className="card-foot">
            {save.error && (
              <p className="err small" role="alert">
                {(save.error as ApiError).message}
              </p>
            )}
            {saved && !save.error && !dirty && <p className="ok small">Saved.</p>}
            <button className="link" onClick={() => navigate('/roles')}>
              Cancel
            </button>
            <button
              className="cta"
              disabled={!dirty || !current.name.trim() || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? 'Saving…' : isNew ? 'Create role' : 'Save changes'}
            </button>
          </div>
        )}
      </div>
    </>
  )
}
