import { basename } from '../app/workspace'

/**
 * Thin fetch wrapper. Requests are same-origin through Caddy, so the session
 * cookie travels automatically and no token is ever held in JS.
 *
 * Every request is prefixed with the active workspace, so `/api/rules` on
 * `/u/userxyz/automations` actually fetches `/u/userxyz/api/rules`. One rule,
 * applied here, rather than a per-call decision about whether an endpoint is
 * workspace-scoped: the server ignores the segment where it is irrelevant
 * (signing in is signing in, whichever workspace you are looking at), and
 * getting the decision wrong for a single call would silently show one
 * workspace's data inside another's page.
 */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message)
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  { workspaceScoped = true }: { workspaceScoped?: boolean } = {},
): Promise<T> {
  const response = await fetch((workspaceScoped ? basename : '') + path, {
    method,
    credentials: 'same-origin',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null

  if (!response.ok && response.status !== 503) {
    // The API returns { error: { code, message } } for every handled failure.
    const error = payload?.error
    throw new ApiError(error?.message ?? `Request failed (${response.status})`, response.status, error?.code)
  }
  return payload as T
}

export const apiGet = <T,>(path: string) => request<T>('GET', path)

/**
 * An endpoint that belongs to the instance rather than to a workspace: `/health`
 * and `/ready`, which live outside the routers `main.py` mounts twice.
 *
 * Prefixing one of these would be worse than a 404 — Caddy routes only
 * `/u/<slug>/{api,webhooks,privacy,data-deletion}` to the API, so anything else
 * under the prefix falls through to the SPA and comes back as `index.html`,
 * which fails as a JSON parse error rather than as a missing endpoint.
 */
export const apiGetInstance = <T,>(path: string) =>
  request<T>('GET', path, undefined, { workspaceScoped: false })
export const apiPost = <T,>(path: string, body?: unknown) => request<T>('POST', path, body)
export const apiPut = <T,>(path: string, body?: unknown) => request<T>('PUT', path, body)
export const apiPatch = <T,>(path: string, body?: unknown) => request<T>('PATCH', path, body)
export const apiDelete = <T,>(path: string) => request<T>('DELETE', path)

/** Multipart upload. No Content-Type header — the browser sets the boundary. */
export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(basename + path, {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  })
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    const error = payload?.error
    throw new ApiError(
      error?.message ?? `Upload failed (${response.status})`,
      response.status,
      error?.code,
    )
  }
  return payload as T
}
