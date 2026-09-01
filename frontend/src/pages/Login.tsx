import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { login } from '../api/auth'
import { ApiError } from '../api/client'

export function Login() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const queryClient = useQueryClient()

  const signIn = useMutation({
    mutationFn: () => login(email, password),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['me'] }),
  })

  return (
    <div className="centered">
      <form
        className="card auth-card"
        onSubmit={(event) => {
          event.preventDefault()
          signIn.mutate()
        }}
      >
        <h1>InstaFlow</h1>
        <p className="muted">Sign in to continue.</p>

        <label htmlFor="email">Email</label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />

        <label htmlFor="password">Password</label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        {signIn.isError && (
          <p className="err" role="alert">
            {(signIn.error as ApiError).message}
          </p>
        )}

        <button className="cta" type="submit" disabled={signIn.isPending}>
          {signIn.isPending ? 'Signing in…' : 'Sign in'}
        </button>

        <p className="muted small">
          No account yet? Run <code>make bootstrap-admin</code> in the project directory.
        </p>
      </form>
    </div>
  )
}
