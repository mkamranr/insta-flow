import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getMe, logout } from '../api/auth'
import { ApiError } from '../api/client'
import { acceptInvite, lookupInvite, slugProblem } from '../api/invites'
import { PasswordFields, usable } from '../components/common/PasswordFields'
import { PREFIX, workspaceUrl } from '../app/workspace'

/**
 * Accepting an invitation. Declared OUTSIDE the authenticated parent route, so
 * neither `RequireAuth` nor `WorkspaceGate` runs here.
 *
 * That matters for more than auth. `WorkspaceGate` redirects a signed-in user
 * with no prefix to `/u/<their-slug>/<path>`, which would bounce somebody who
 * clicked an invite link while logged in as themselves before this page could
 * explain anything -- and `Layout` would wrap a mid-signup stranger in the full
 * sidebar and DM meters.
 */

const ERRORS: Record<string, string> = {
  invite_not_found: 'That invitation link is not valid. Check it was copied in full, or ask for a new one.',
  gone: 'That invitation is no longer usable. Ask whoever invited you to send another.',
}

export function Invite() {
  const token = new URLSearchParams(window.location.search).get('token')

  const invite = useQuery({
    queryKey: ['invite', token],
    queryFn: () => lookupInvite(token!),
    enabled: Boolean(token),
    retry: false,
  })
  const me = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [slug, setSlug] = useState<string | null>(null)
  const [suggestion, setSuggestion] = useState<string | null>(null)

  const currentSlug = slug ?? invite.data?.suggested_slug ?? ''
  const problem = slugProblem(currentSlug)

  const accept = useMutation({
    mutationFn: () => acceptInvite(token!, password, confirm, currentSlug),
    onSuccess: ({ slug: created }) => {
      // A full document load, not an in-SPA transition. `basename`,
      // `workspaceSlug` and the API client's prefix are all computed once at
      // module load, so staying in this context would leave every subsequent
      // request unprefixed -- and `WorkspaceGate` would then reload anyway.
      // Same reasoning as sign-out and switching workspace.
      window.location.assign(workspaceUrl(created, '/get-started'))
    },
    onError: (error) => {
      const suggested = (error as ApiError & { suggested_slug?: string }).suggested_slug
      if (suggested) setSuggestion(suggested)
    },
  })

  if (!token) {
    return (
      <div className="centered">
        <div className="card auth-card">
          <h1 className="auth-logo">Social Flow</h1>
          <h2>That link is incomplete</h2>
          <p className="muted">
            It is missing its invitation code. Ask whoever invited you to send it again.
          </p>
        </div>
      </div>
    )
  }

  if (invite.isLoading) return <div className="centered muted">Loading…</div>

  if (invite.isError) {
    const error = invite.error as ApiError
    return (
      <div className="centered">
        <div className="card auth-card">
          <h1 className="auth-logo">Social Flow</h1>
          <h2>This invitation cannot be used</h2>
          <p className="muted">
            {ERRORS[error.code ?? ''] ?? error.message}
          </p>
          <a className="link" href="/">
            Go to the sign-in page
          </a>
        </div>
      </div>
    )
  }

  // `client.ts` deliberately does not throw on 503, so `getMe()` during a
  // database outage resolves with an error object cast to `Me`. Test the field
  // rather than the object.
  const signedInAs =
    typeof me.data?.email === 'string' && me.data.email.length > 0 ? me.data.email : null
  const alreadyThisPerson = signedInAs === invite.data?.email

  if (alreadyThisPerson) {
    return (
      <div className="centered">
        <div className="card auth-card">
          <h1 className="auth-logo">Social Flow</h1>
          <h2>You have already accepted this invitation</h2>
          <p className="muted">You are signed in as {signedInAs}.</p>
          <a className="link" href={workspaceUrl(me.data?.slug ?? '', '/get-started')}>
            Continue your setup
          </a>
        </div>
      </div>
    )
  }

  return (
    <div className="centered">
      <form
        className="card auth-card"
        onSubmit={(e) => {
          e.preventDefault()
          accept.mutate()
        }}
      >
        <h1 className="auth-logo">Social Flow</h1>
        <h2>Set up your account</h2>

        {signedInAs && (
          <div className="notice">
            You are signed in as <strong>{signedInAs}</strong>. Accepting this invitation
            creates a <strong>separate</strong> account for {invite.data?.email}.
            <br />
            <br />
            <button
              type="button"
              className="secondary"
              onClick={async () => {
                await logout()
                window.location.reload()
              }}
            >
              Sign out and continue
            </button>{' '}
            <a className="link" href="/">
              Stay signed in as {signedInAs}
            </a>
          </div>
        )}

        <label htmlFor="invite-email">Email</label>
        {/* `readOnly`, not `disabled`. Disabled drops the field out of the tab
            order, is announced as unavailable, and often fails contrast --
            which reads as "broken" when the truth is "correct and settled".
            Read-only stays focusable and copyable. Either way the server takes
            the address from the token and ignores anything sent here. */}
        <input
          id="invite-email"
          type="email"
          value={invite.data?.email ?? ''}
          readOnly
          autoComplete="username"
        />
        <p className="muted small">
          This invitation is for this address. To use a different one, ask for a new
          invitation.
        </p>

        <PasswordFields
          idPrefix="invite"
          password={password}
          confirm={confirm}
          onPassword={setPassword}
          onConfirm={setConfirm}
        />

        <label htmlFor="invite-slug">Your workspace address</label>
        <input
          id="invite-slug"
          value={currentSlug}
          onChange={(e) => {
            setSuggestion(null)
            setSlug(e.target.value.toLowerCase())
          }}
        />
        {problem ? (
          <p className="err small" role="alert">{problem}</p>
        ) : (
          <p className="muted small">
            Your pages will live at{' '}
            <code>
              {window.location.origin}
              {PREFIX}/{currentSlug}/
            </code>
          </p>
        )}

        {accept.error && (
          <p className="err small" role="alert">{(accept.error as Error).message}</p>
        )}
        {suggestion && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setSuggestion(null)
              setSlug(suggestion)
            }}
          >
            Use {suggestion} instead
          </button>
        )}

        <button
          className="cta"
          type="submit"
          disabled={!usable(password, confirm) || Boolean(problem) || accept.isPending}
        >
          {accept.isPending ? 'Creating…' : 'Create account and sign in'}
        </button>
      </form>
    </div>
  )
}
