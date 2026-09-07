import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { createUser, resetUserPassword, type Role, type User } from '../../api/admin'
import type { ApiError } from '../../api/client'
import { PasswordFields, usable } from '../common/PasswordFields'

export function NewUserDialog({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [roleId, setRoleId] = useState(roles[0]?.id ?? '')

  const save = useMutation({
    mutationFn: () => createUser({ email, password, role_id: roleId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] })
      // user_count on the roles list moves with every assignment.
      queryClient.invalidateQueries({ queryKey: ['roles'] })
      onClose()
    },
  })

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Add user</h2>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          {/* A real form, so Enter submits -- this is a credential form, and
              Login and Setup both behave that way. */}
          <form
            id="new-user"
            onSubmit={(e) => {
              e.preventDefault()
              if (usable(password, confirm) && email && roleId) save.mutate()
            }}
          >
            <label htmlFor="user-email">Email</label>
            <input
              id="user-email"
              type="email"
              autoComplete="off"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />

            <label htmlFor="user-role">Role</label>
            <select
              id="user-role"
              value={roleId}
              onChange={(e) => setRoleId(e.target.value)}
            >
              {roles.map((role) => (
                <option key={role.id} value={role.id}>
                  {role.name}
                </option>
              ))}
            </select>
            <p className="muted small">
              Nothing is emailed. To have Social Flow send an activation link instead, so they
              choose their own password, use <strong>Invites</strong>. This form stays for an
              install with no mail configured — pass the password on yourself, and they can
              change it once they are in.
            </p>

            <PasswordFields
              password={password}
              confirm={confirm}
              onPassword={setPassword}
              onConfirm={setConfirm}
            />
          </form>
          {save.error && (
            <p className="err small" role="alert">
              {(save.error as ApiError).message}
            </p>
          )}
        </div>
        <div className="modal-foot">
          <button className="link" onClick={onClose}>
            Cancel
          </button>
          <button
            className="cta"
            type="submit"
            form="new-user"
            disabled={!email || !roleId || !usable(password, confirm) || save.isPending}
          >
            {save.isPending ? 'Adding…' : 'Add user'}
          </button>
        </div>
      </div>
    </div>
  )
}

export function ResetPasswordDialog({
  user,
  isSelf,
  onClose,
}: {
  user: User
  isSelf: boolean
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')

  const save = useMutation({
    mutationFn: () => resetUserPassword(user.id, password),
    onSuccess: () => {
      // A reset revokes every session the old password opened -- including this
      // one, when the target is yourself. Invalidating ['me'] lets RequireAuth
      // land on the login screen cleanly instead of a half-broken layout.
      if (isSelf) queryClient.invalidateQueries({ queryKey: ['me'] })
      onClose()
    },
  })

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Reset password</h2>
          <button className="link" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="modal-body">
          <p className="muted">
            Set a new password for <strong>{user.email}</strong>. They will be signed out
            everywhere.
          </p>
          {isSelf && (
            <p className="err small">
              This is your own account — you will be signed out and will need to sign in again
              with the new password.
            </p>
          )}
          <form
            id="reset-password"
            onSubmit={(e) => {
              e.preventDefault()
              if (usable(password, confirm)) save.mutate()
            }}
          >
            <PasswordFields
              password={password}
              confirm={confirm}
              onPassword={setPassword}
              onConfirm={setConfirm}
            />
          </form>
          {save.error && (
            <p className="err small" role="alert">
              {(save.error as ApiError).message}
            </p>
          )}
        </div>
        <div className="modal-foot">
          <button className="link" onClick={onClose}>
            Cancel
          </button>
          <button
            className="cta"
            type="submit"
            form="reset-password"
            disabled={!usable(password, confirm) || save.isPending}
          >
            {save.isPending ? 'Saving…' : 'Reset password'}
          </button>
        </div>
      </div>
    </div>
  )
}
