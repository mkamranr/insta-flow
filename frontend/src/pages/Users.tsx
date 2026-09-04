import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { deleteUser, listRoles, listUsers, patchUser, type User } from '../api/admin'
import { getMe } from '../api/auth'
import type { ApiError } from '../api/client'
import { NewUserDialog, ResetPasswordDialog } from '../components/admin/UserDialogs'
import { usePermissions } from '../hooks/usePermissions'

export function Users() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<User | null>(null)
  const { can } = usePermissions()

  const roleFilter = params.get('role')
  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe })
  const { data: users, isLoading } = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const { data: roles } = useQuery({ queryKey: ['roles'], queryFn: listRoles })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['users'] })
    queryClient.invalidateQueries({ queryKey: ['roles'] })
  }

  const setRole = useMutation({
    mutationFn: ({ id, role_id }: { id: string; role_id: string }) => patchUser(id, { role_id }),
    onSuccess: invalidate,
  })
  const setActive = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      patchUser(id, { is_active }),
    onSuccess: invalidate,
  })
  const remove = useMutation({ mutationFn: deleteUser, onSuccess: invalidate })

  // One error slot for several mutations, matching Instagram.tsx. The backend's
  // guardrail messages are already written for a human, so they are shown as-is.
  const failure = [setRole, setActive, remove].find((m) => m.isError)
  const shown = roleFilter ? users?.filter((u) => u.role?.slug === roleFilter) : users
  const manage = can('users.manage')

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Users</h1>
          <p className="muted">Who can sign in, and what each of them may do.</p>
        </div>
        {manage && roles && roles.length > 0 && (
          <button className="cta" onClick={() => setCreating(true)}>
            Add user
          </button>
        )}
      </div>

      {failure && (
        <div className="card">
          <p className="err" role="alert" style={{ margin: 0 }}>
            {(failure.error as ApiError).message}
          </p>
        </div>
      )}

      {roles && roles.length > 0 && (
        <div className="filters">
          <button
            className={roleFilter ? 'filter' : 'filter active'}
            onClick={() => setParams({}, { replace: true })}
          >
            Everyone
          </button>
          {roles.map((role) => (
            <button
              key={role.id}
              className={roleFilter === role.slug ? 'filter active' : 'filter'}
              onClick={() => setParams({ role: role.slug }, { replace: true })}
            >
              {role.name}
            </button>
          ))}
        </div>
      )}

      {isLoading && <p className="muted">Loading…</p>}

      {shown && shown.length > 0 && (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Person</th>
                <th>Role</th>
                <th>Status</th>
                <th>Last sign-in</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shown.map((user) => {
                const isSelf = user.id === me?.id
                const busy = setRole.isPending && setRole.variables?.id === user.id
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="auto-name">
                        <strong>{user.email}</strong>
                        {isSelf && <span className="pill">You</span>}
                      </div>
                    </td>
                    <td>
                      {manage && !isSelf ? (
                        <select
                          value={user.role?.id ?? ''}
                          aria-label={`Role for ${user.email}`}
                          // Disabled while its own request is in flight: the value
                          // still shows the old role until the refetch lands, which
                          // would otherwise read as "nothing happened". On failure
                          // the cache never changed, so it snaps back by itself.
                          disabled={busy}
                          onChange={(e) => setRole.mutate({ id: user.id, role_id: e.target.value })}
                        >
                          {roles?.map((role) => (
                            <option key={role.id} value={role.id}>
                              {role.name}
                            </option>
                          ))}
                        </select>
                      ) : (
                        (user.role?.name ?? <span className="muted small">No role</span>)
                      )}
                    </td>
                    <td>
                      <span className={user.is_active ? 'pill on' : 'pill off'}>
                        {user.is_active ? 'Active' : 'Disabled'}
                      </span>
                    </td>
                    <td className="muted small">
                      {user.last_login_at
                        ? new Date(user.last_login_at).toLocaleDateString()
                        : 'Never'}
                    </td>
                    <td className="actions-cell">
                      {manage && (
                        <>
                          <button className="secondary" onClick={() => setResetting(user)}>
                            Reset password
                          </button>
                          {/* Disabled rather than hidden: the block is a property
                              of this row, and a missing button would just make one
                              row inexplicably different from the others. */}
                          <button
                            className="secondary"
                            disabled={isSelf || setActive.isPending}
                            title={isSelf ? 'You cannot disable your own account.' : undefined}
                            onClick={() =>
                              setActive.mutate({ id: user.id, is_active: !user.is_active })
                            }
                          >
                            {user.is_active ? 'Disable' : 'Enable'}
                          </button>
                          <button
                            className="danger"
                            disabled={isSelf || remove.isPending}
                            title={isSelf ? 'You cannot delete your own account.' : undefined}
                            onClick={() => {
                              if (confirm(`Delete ${user.email}? They will be signed out.`)) {
                                remove.mutate(user.id)
                              }
                            }}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {shown && shown.length === 0 && (
        <div className="card">
          <h2>Nobody here</h2>
          <p className="muted">No user holds that role yet.</p>
        </div>
      )}

      {creating && roles && (
        <NewUserDialog roles={roles} onClose={() => setCreating(false)} />
      )}
      {resetting && (
        <ResetPasswordDialog
          user={resetting}
          isSelf={resetting.id === me?.id}
          onClose={() => setResetting(null)}
        />
      )}
    </>
  )
}
