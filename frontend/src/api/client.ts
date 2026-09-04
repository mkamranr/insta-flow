/**
 * Thin fetch wrapper. Requests are same-origin through Caddy, so the session
 * cookie travels automatically and no token is ever held in JS.
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

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
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
export const apiPost = <T,>(path: string, body?: unknown) => request<T>('POST', path, body)
export const apiPut = <T,>(path: string, body?: unknown) => request<T>('PUT', path, body)
export const apiPatch = <T,>(path: string, body?: unknown) => request<T>('PATCH', path, body)
export const apiDelete = <T,>(path: string) => request<T>('DELETE', path)

/** Multipart upload. No Content-Type header — the browser sets the boundary. */
export async function apiUpload<T>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(path, { method: 'POST', credentials: 'same-origin', body: form })
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
