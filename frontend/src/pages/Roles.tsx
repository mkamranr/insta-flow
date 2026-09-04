import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { deleteRole, getCatalogue, listRoles, type Role } from '../api/admin'
import type { ApiError } from '../api/client'
import { usePermissions } from '../hooks/usePermissions'

export function Roles() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { can } = usePermissions()

  const { data: roles, isLoading } = useQuery({ queryKey: ['roles'], queryFn: listRoles })
  const { data: catalogue } = useQuery({
    queryKey: ['permissions'],
    queryFn: getCatalogue,
    // Only changes when the server is redeployed.
    staleTime: Infinity,
  })

  const remove = useMutation({
    mutationFn: deleteRole,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['roles'] }),
  })

  /** Which modules a role touches at all — what an admin actually scans for. */
  const modulesOf = (role: Role) => {
    const keys = new Set(role.permissions.map((p) => p.split('.')[0]))
    return (catalogue?.modules ?? []).filter((m) => keys.has(m.key)).map((m) => m.label)
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Roles</h1>
          <p className="muted">What each kind of teammate is allowed to do.</p>
        </div>
        {can('roles.manage') && (
          <button className="cta" onClick={() => navigate('/roles/new')}>
            New role
          </button>
        )}
      </div>

      {remove.error && (
        <div className="card">
          <p className="err" role="alert" style={{ margin: 0 }}>
            {(remove.error as ApiError).message}
          </p>
        </div>
      )}

      {isLoading && <p className="muted">Loading…</p>}

      {roles && roles.length > 0 && (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Role</th>
                <th>Covers</th>
                <th>People</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => {
                const modules = modulesOf(role)
                return (
                  <tr key={role.id}>
                    <td>
                      <div className="auto-name">
                        <strong>{role.name}</strong>
                        {role.is_system && <span className="pill">Built-in</span>}
                      </div>
                      {role.description && (
                        <div className="muted small">{role.description}</div>
                      )}
                    </td>
                    <td>
                      {modules.length === 0 ? (
                        <span className="muted small">No permissions</span>
                      ) : (
                        <div className="chips">
                          {modules.slice(0, 3).map((label) => (
                            <span key={label} className="chip">
                              {label}
                            </span>
                          ))}
                          {modules.length > 3 && (
                            <span className="chip">+{modules.length - 3}</span>
                          )}
                        </div>
                      )}
                    </td>
                    <td>
                      {can('users.view') && role.user_count > 0 ? (
                        <Link to={`/users?role=${role.slug}`}>{role.user_count}</Link>
                      ) : (
                        role.user_count
                      )}
                    </td>
                    <td className="actions-cell">
                      <button className="secondary" onClick={() => navigate(`/roles/${role.id}`)}>
                        {/* Built-in roles keep a View: the commonest question an
                            admin has is what one actually grants. */}
                        {role.is_system || !can('roles.manage') ? 'View' : 'Edit'}
                      </button>
                      {can('roles.manage') && (
                        <button
                          className="link"
                          onClick={() => navigate('/roles/new', { state: { from: role } })}
                        >
                          Duplicate
                        </button>
                      )}
                      {can('roles.manage') && !role.is_system && (
                        <button
                          className="danger"
                          disabled={remove.isPending}
                          onClick={() => {
                            const people = role.user_count === 1 ? 'person is' : 'people are'
                            const warning =
                              role.user_count > 0
                                ? `Delete ${role.name}? ${role.user_count} ${people} assigned to it.`
                                : `Delete ${role.name}?`
                            if (confirm(warning)) remove.mutate(role.id)
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
