import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { getMe } from '../../api/auth'
import { Login } from '../../pages/Login'

/**
 * Gate for authenticated routes. The session lives in an HttpOnly cookie, so the
 * only way to know whether we are signed in is to ask the server.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['me'],
    queryFn: getMe,
    retry: false,
  })

  if (isLoading) return <div className="centered muted">Loading…</div>
  if (isError || !data) return <Login />
  return <>{children}</>
}
