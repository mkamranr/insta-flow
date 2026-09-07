import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { Role } from '../../api/admin'
import { SKIP_REASONS, sendInvites, type InviteResult } from '../../api/invites'
import { CloseIcon } from '../icons/Icons'

/** Confirm, then report. Shared by the Invites page and the Contacts page.
 *
 * Deliberately not `window.confirm`, which the repo uses for single
 * destructive actions: a native confirm cannot show forty addresses, cannot
 * carry a role selector, and cannot report a per-recipient outcome. Sending
 * real email to real people is irreversible and outward-facing, so the
 * recipient list is on screen and removable before anything leaves.
 */
export function InviteDialog({
  emails,
  roles,
  onClose,
}: {
  emails: string[]
  roles?: Role[]
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [recipients, setRecipients] = useState(emails)
  const [roleId, setRoleId] = useState('')
  const [result, setResult] = useState<InviteResult | null>(null)

  const send = useMutation({
    mutationFn: () => sendInvites(recipients, roleId || undefined),
    onSuccess: (data) => setResult(data),
  })

  const finish = () => {
    queryClient.invalidateQueries({ queryKey: ['invites'] })
    queryClient.invalidateQueries({ queryKey: ['contacts'] })
    onClose()
  }

  return (
    <div className="modal-backdrop" onClick={result ? finish : onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-head">
          <h2>
            {result
              ? 'Invitations sent'
              : `Invite ${recipients.length} ${recipients.length === 1 ? 'person' : 'people'}`}
          </h2>
          <button className="link" aria-label="Close" onClick={result ? finish : onClose}>
            <CloseIcon />
          </button>
        </header>

        <div className="modal-body">
          {result ? (
            <>
              <p className="ok small">
                Sent {result.sent.length}{' '}
                {result.sent.length === 1 ? 'invitation' : 'invitations'}.
              </p>
              {result.skipped.length > 0 && (
                <>
                  <p className="muted small">
                    {result.skipped.length} skipped:
                  </p>
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Email</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.skipped.map((row) => (
                        <tr key={row.email}>
                          <td className="clamp">{row.email}</td>
                          <td className="muted small">
                            {SKIP_REASONS[row.reason] ?? row.reason}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              {result.sent.length > 0 && (
                <p className="muted small">
                  No email arriving? Check <strong>Settings → Email</strong>, which lists the
                  last few messages and why any of them failed.
                </p>
              )}
            </>
          ) : (
            <>
              <p className="muted small">Sending to:</p>
              <div className="chips">
                {recipients.map((email) => (
                  <span className="chip" key={email}>
                    {email}
                    {recipients.length > 1 && (
                      <button
                        className="chip-x"
                        aria-label={`Remove ${email}`}
                        onClick={() =>
                          setRecipients((current) => current.filter((e) => e !== email))
                        }
                      >
                        <CloseIcon />
                      </button>
                    )}
                  </span>
                ))}
              </div>

              {roles && roles.length > 0 && (
                <>
                  <label htmlFor="invite-role">Role</label>
                  <select
                    id="invite-role"
                    value={roleId}
                    onChange={(e) => setRoleId(e.target.value)}
                  >
                    <option value="">Workspace owner (recommended)</option>
                    {roles.map((role) => (
                      <option key={role.id} value={role.id}>
                        {role.name}
                      </option>
                    ))}
                  </select>
                  <p className="muted small">
                    Workspace owner runs their own Instagram account and sees nothing outside
                    it. Anything else shares this install&rsquo;s data.
                  </p>
                </>
              )}

              <div className="notice">
                Each person gets an email with a link to choose a password and set up their own
                workspace, connected to their own Instagram account.{' '}
                <strong>This cannot be unsent.</strong>
              </div>
            </>
          )}

          {send.error && (
            <p className="err small" role="alert">{(send.error as Error).message}</p>
          )}
        </div>

        <footer className="modal-foot">
          {result ? (
            <button className="cta" onClick={finish}>
              Done
            </button>
          ) : (
            <>
              <button className="link" onClick={onClose}>
                Cancel
              </button>
              <button
                className="cta"
                disabled={send.isPending || recipients.length === 0}
                onClick={() => send.mutate()}
              >
                {send.isPending
                  ? 'Sending…'
                  : `Send ${recipients.length} ${recipients.length === 1 ? 'invite' : 'invites'}`}
              </button>
            </>
          )}
        </footer>
      </div>
    </div>
  )
}
