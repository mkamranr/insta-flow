import { useEffect, useState } from 'react'

/**
 * The connected account's Instagram profile picture, falling back to its initial.
 *
 * Meta serves these from a CDN with expiring URLs, so a stored link goes stale
 * on its own schedule. The daily account job refreshes it, but between refreshes
 * a link can start 404ing — hence the `onError` fallback rather than trusting
 * that a non-empty URL means a working image.
 */
export function AccountAvatar({
  url,
  name,
  size = 30,
  className = 'account-avatar',
}: {
  url?: string | null
  name?: string | null
  size?: number
  className?: string
}) {
  const [broken, setBroken] = useState(false)

  // A fresh URL after a refresh deserves another attempt.
  useEffect(() => setBroken(false), [url])

  const initial = (name ?? '?').trim().slice(0, 1).toUpperCase() || '?'
  const style = { width: size, height: size }

  if (!url || broken) {
    return (
      <span className={`${className} account-avatar-initial`} style={style} aria-hidden="true">
        {initial}
      </span>
    )
  }

  return (
    <img
      className={className}
      style={style}
      src={url}
      alt=""
      loading="lazy"
      onError={() => setBroken(true)}
    />
  )
}
