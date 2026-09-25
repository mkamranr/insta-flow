import { useEffect, useState } from 'react'

/**
 * A post or story picture, falling back to the caller's own placeholder.
 *
 * The image comes from our own server, not Meta's CDN — their links are signed
 * and expire, and re-fetching returns a fresh URL carrying the *same* expiry, so
 * one cannot be renewed (docs/meta-api.md §2d). The server copies the bytes at
 * sync time, so in the normal case this just renders.
 *
 * The `onError` still matters, for the one case the cache cannot fix: a post or
 * story whose URL had already expired before the bytes were ever copied has
 * nothing to serve and never will. That 404s, and without a fallback it renders
 * as the browser's broken-image glyph. Same shape as `AccountAvatar`, for the
 * same underlying reason.
 *
 * `empty` is the caller's existing placeholder element rather than markup of our
 * own, so adopting this changes which element renders and never how it looks.
 */
export function MediaThumb({
  src,
  className,
  empty = null,
}: {
  src: string | null
  className?: string
  empty?: React.ReactNode
}) {
  const [broken, setBroken] = useState(false)

  // A new address deserves another attempt: `?v=` changes when the bytes do.
  useEffect(() => setBroken(false), [src])

  if (!src || broken) return <>{empty}</>

  return (
    <img className={className} src={src} alt="" loading="lazy" onError={() => setBroken(true)} />
  )
}
