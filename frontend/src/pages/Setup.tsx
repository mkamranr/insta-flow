import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { createFirstAdmin, verifySetupSecret } from '../api/auth'
import { ApiError } from '../api/client'

/**
 * First-run setup, for a deployment with no shell to run `make bootstrap-admin`.
 *
 * Two steps because that is kinder — finding out the code is wrong *after*
 * choosing a password is a bad moment. The first step grants nothing: the
 * server re-checks the code, that there are still no users, and the throttle,
 * when the account is actually created.
 */

// Mirrors MIN_PASSWORD_LENGTH on the server, which is the real check.
const MIN_PASSWORD = 12

export function Setup({ onDone }: { onDone: () => void }) {
  const [secret, setSecret] = useState('')
  const [verified, setVerified] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const queryClient = useQueryClient()

  const verify = useMutation({
    mutationFn: () => verifySetupSecret(secret),
    onSuccess: () => setVerified(true),
  })

  const create = useMutation({
    mutationFn: () =>
      createFirstAdmin({ secret, email, password, password_confirm: confirm }),
    onSuccess: () => {
      // The server signs us in as part of creating the account, so the session
      // cookie is already set — just re-ask who we are.
      queryClient.invalidateQueries({ queryKey: ['me'] })
      onDone()
    },
  })

  const mismatch = confirm.length > 0 && password !== confirm
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD

  if (!verified) {
    return (
      <div className="centered">
        <form
          className="card auth-card"
          onSubmit={(event) => {
            event.preventDefault()
            verify.mutate()
          }}
        >
          <img className="auth-logo" src="/logo.png" alt="" width="56" height="56" />
          <h1>Set up Social Flow</h1>
          <p className="muted">
            No admin account exists yet. Enter the setup code to create one.
          </p>

          <label htmlFor="secret">Setup code</label>
          <input
            id="secret"
            type="password"
            autoComplete="off"
            autoFocus
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            required
          />
          <p className="muted small">
            This is <code>ADMIN_SETUP_SECRET</code> from the server&rsquo;s <code>.env</code>.
          </p>

          {verify.isError && (
            <p className="err" role="alert">
              {(verify.error as ApiError).message}
            </p>
          )}

          <button className="cta" type="submit" disabled={verify.isPending || !secret}>
            {verify.isPending ? 'Checking…' : 'Continue'}
          </button>
        </form>
      </div>
    )
  }

  return (
    <div className="centered">
      <form
        className="card auth-card"
        onSubmit={(event) => {
          event.preventDefault()
          create.mutate()
        }}
      >
        <img className="auth-logo" src="/logo.png" alt="" width="56" height="56" />
        <h1>Create your admin account</h1>
        <p className="muted">This is the account you will sign in with from now on.</p>

        <label htmlFor="setup-email">Email</label>
        <input
          id="setup-email"
          type="email"
          autoComplete="username"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />

        <label htmlFor="setup-password">Password</label>
        <input
          id="setup-password"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <p className={tooShort ? 'err small' : 'muted small'}>
          At least {MIN_PASSWORD} characters. A passphrase of a few words beats a short,
          complicated one.
        </p>

        <label htmlFor="setup-confirm">Confirm password</label>
        <input
          id="setup-confirm"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          required
        />
        {mismatch && (
          <p className="err small" role="alert">
            Passwords do not match.
          </p>
        )}

        {create.isError && (
          <p className="err" role="alert">
            {(create.error as ApiError).message}
          </p>
        )}

        <button
          className="cta"
          type="submit"
          disabled={create.isPending || mismatch || tooShort || !email || !password}
        >
          {create.isPending ? 'Creating…' : 'Create account and sign in'}
        </button>

        <button className="link" type="button" onClick={() => setVerified(false)}>
          Back
        </button>
      </form>
    </div>
  )
}
