import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { listRoles } from '../api/admin'
import {
  listInvites,
  resendInvite,
  revokeInvite,
  type Invite,
} from '../api/invites'
import { CopyField } from '../components/common/CopyField'
import { InviteDialog } from '../components/admin/InviteDialog'
import { usePermissions } from '../hooks/usePermissions'

const FILTERS = [
  { key: '', label: 'All' },
  { key: 'pending', label: 'Pending' },
  { key: 'accepted', label: 'Accepted' },
  { key: 'expired', label: 'Expired' },
  { key: 'revoked', label: 'Revoked' },
]

/** `expired` is derived from the clock server-side, never stored, so it can
 *  appear here without a sweeper having run. */
function statusPill(invite: Invite) {
  if (invite.status === 'accepted') return <span className="pill on">Accepted</span>
  if (invite.status === 'expired') return <span className="pill paused">Expired</span>
  if (invite.status === 'revoked') return <span className="pill off">Revoked</span>
  return <span className="pill">Pending</span>
}

const when = (value: string | null) =>
  value ? new Date(value).toLocaleDateString() : '—'

export function Invites() {
  const queryClient = useQueryClient()
  const { can } = usePermissions()
  const [params, setParams] = useSearchParams()
  const filter = params.get('status') ?? ''

  const { data: invites } = useQuery({
    queryKey: ['invites', filter],
    queryFn: () => listInvites(filter || undefined),
  })
  // `listRoles` needs `roles.view`, which `users.manage` does not imply, so the
  // role picker simply does not appear without it and the server defaults.
  const { data: roles } = useQuery({
    queryKey: ['roles'],
    queryFn: listRoles,
    enabled: can('roles.view'),
  })

  const [email, setEmail] = useState('')
  const [inviting, setInviting] = useState<string[] | null>(null)
  const [link, setLink] = useState<string | null>(null)

  const resend = useMutation({
    mutationFn: resendInvite,
    onSuccess: (invite) => {
      setLink(invite.url ?? null)
      queryClient.invalidateQueries({ queryKey: ['invites'] })
    },
  })
  const revoke = useMutation({
    mutationFn: revokeInvite,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['invites'] }),
  })
  const failure = [resend, revoke].find((m) => m.isError)?.error

  const manage = can('users.manage')

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Invites</h1>
          <p className="muted">
            Invite someone to run their own Instagram account on this install. They choose
            their own password and connect their own Meta app — nothing is shared with your
            workspace. <Link to="/users">People with accounts →</Link>
          </p>
        </div>
      </div>

      {manage && (
        <div className="card">
          <label htmlFor="invite-email">Email address</label>
          <div className="row">
            <input
              id="invite-email"
              type="email"
              value={email}
              placeholder="someone@example.com"
              onChange={(e) => setEmail(e.target.value)}
            />
            <button
              disabled={!email.includes('@')}
              onClick={() => {
                setLink(null)
                setInviting([email.trim().toLowerCase()])
              }}
            >
              Invite
            </button>
          </div>
          <p className="muted small">
            Sending needs <strong>Settings → Email</strong> configured. Without it the
            invitation is still created and you can copy its link below and pass it on
            yourself.
          </p>
          {link && (
            <CopyField
              label="Activation link"
              value={link}
              hint="Single use, and it expires. Anyone holding it can create the account."
            />
          )}
        </div>
      )}

      <div className="filters">
        {FILTERS.map((option) => (
          <button
            key={option.key}
            className={filter === option.key ? 'filter active' : 'filter'}
            onClick={() =>
              setParams(option.key ? { status: option.key } : {}, { replace: true })
            }
          >
            {option.label}
          </button>
        ))}
      </div>

      {failure && <p className="err small" role="alert">{(failure as Error).message}</p>}

      {invites && invites.length === 0 ? (
        <div className="card empty-card">
          <h2>Nobody invited yet</h2>
          <p className="muted">
            An invitation lets someone sign up with the address you send it to, and nothing
            else — the link expires and works once.
          </p>
        </div>
      ) : (
        <div className="card table-card">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Invited by</th>
                <th>Sent</th>
                <th>Expires</th>
                <th>Status</th>
                {manage && <th />}
              </tr>
            </thead>
            <tbody>
              {invites?.map((invite) => (
                <tr key={invite.id}>
                  <td className="clamp">{invite.email}</td>
                  <td className="muted small">{invite.role?.name ?? '—'}</td>
                  <td className="muted small">
                    {invite.invited_by_email ?? '—'}
                    {!invite.invited_by_active && (
                      <span className="pill pill-muted" title="This admin is deactivated. Their invites still work — revoke them if that is not wanted.">
                        inactive
                      </span>
                    )}
                  </td>
                  <td className="muted small">{when(invite.last_sent_at)}</td>
                  <td className="muted small">{when(invite.expires_at)}</td>
                  <td>
                    {statusPill(invite)}
                    {invite.last_send_status === 'failed' && (
                      <div className="err small">Email not sent</div>
                    )}
                  </td>
                  {manage && (
                    <td className="actions-cell">
                      {invite.status !== 'accepted' && invite.status !== 'revoked' && (
                        <>
                          <button
                            className="secondary"
                            disabled={resend.isPending}
                            onClick={() => resend.mutate(invite.id)}
                          >
                            Resend
                          </button>
                          <button
                            className="danger"
                            disabled={revoke.isPending}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `Revoke the invite for ${invite.email}? Their link stops working.`,
                                )
                              ) {
                                revoke.mutate(invite.id)
                              }
                            }}
                          >
                            Revoke
                          </button>
                        </>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {inviting && (
        <InviteDialog
          emails={inviting}
          roles={roles}
          onClose={() => {
            setInviting(null)
            setEmail('')
          }}
        />
      )}
    </>
  )
}
