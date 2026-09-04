import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { getMe } from '../api/auth'

/**
 * Read-only view of the signed-in user's effective permissions.
 *
 * No context and no extra request: RequireAuth resolves ['me'] before anything
 * inside the layout mounts, so this is a synchronous read of the react-query
 * cache and `ready` is already true on the first render of every gated
 * component. That is what stops the nav flickering items in and out.
 *
 * This is UX, not security. The permission list arrives as ordinary JSON and
 * anyone can edit it in devtools; every route behind it re-checks server-side.
 */
export function usePermissions() {
  const { data, isLoading } = useQuery({ queryKey: ['me'], queryFn: getMe, retry: false })
  const granted = useMemo(() => new Set(data?.permissions ?? []), [data?.permissions])

  return useMemo(
    () => ({
      /** Exact match only — the server sends the fully expanded list. */
      can: (permission?: string) => !permission || granted.has(permission),
      canAny: (...permissions: string[]) => permissions.some((p) => granted.has(p)),
      /** False only while the very first /api/auth/me is in flight. */
      ready: !isLoading && data !== undefined,
      role: data?.role ?? null,
    }),
    [granted, isLoading, data],
  )
}
