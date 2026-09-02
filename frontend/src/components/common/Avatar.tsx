/**
 * A generic person mark for example conversations.
 *
 * Deliberately not a photograph. Real portraits would mean either shipping
 * pictures of actual people or fetching from an avatar CDN, and this dashboard
 * makes no outbound requests of its own. The silhouette is drawn locally and
 * tinted from the handle, so the same name always looks the same.
 */

// Mid-dark steps only: the silhouette is white, and a pale tint would lose it.
const TINTS = [
  '#4f46e5',
  '#0e7490',
  '#b45309',
  '#7c3aed',
  '#0f766e',
  '#be123c',
  '#4d7c0f',
  '#9333ea',
]

function tintFor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return TINTS[hash % TINTS.length]
}

export function Avatar({ name, size = 26 }: { name: string; size?: number }) {
  return (
    <svg
      className="avatar"
      width={size}
      height={size}
      viewBox="0 0 40 40"
      role="img"
      aria-label={`${name} profile picture`}
    >
      <circle cx="20" cy="20" r="20" fill={tintFor(name)} />
      <circle cx="20" cy="15.4" r="6.3" fill="#fff" fillOpacity="0.93" />
      {/* Shoulders, clipped by the circle so the mark reads as a bust. */}
      <path
        d="M6.7 34.4a13.7 13.7 0 0 1 26.6 0A19.9 19.9 0 0 1 20 40a19.9 19.9 0 0 1-13.3-5.6Z"
        fill="#fff"
        fillOpacity="0.93"
      />
    </svg>
  )
}
