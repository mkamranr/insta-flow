import { useState } from 'react'
import type { Bucket } from '../../api/analytics'

/**
 * Counts over time.
 *
 * A bar, not a line: these are discrete counts per bucket, and a line would
 * imply a value between them. One series, so there is no legend and no
 * categorical palette to get wrong — the heading names it.
 *
 * The bars are ink rather than the lime accent. `--accent` is roughly 1.3:1 on
 * white, which is fine for a CTA and unreadable as a data mark.
 */

function label(bucket: string, byHour: boolean): string {
  const date = new Date(byHour ? `${bucket}:00Z` : `${bucket}T00:00:00Z`)
  if (Number.isNaN(date.getTime())) return bucket
  return byHour
    ? date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit' })
    : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function BarSeries({
  data,
  byHour,
  emptyNote,
}: {
  data: Bucket[]
  byHour: boolean
  emptyNote: string
}) {
  const [hovered, setHovered] = useState<Bucket | null>(null)

  if (data.length === 0) return <p className="muted small">{emptyNote}</p>

  const peak = Math.max(...data.map((point) => point.count), 1)
  const total = data.reduce((sum, point) => sum + point.count, 0)
  const best = data.reduce((top, point) => (point.count > top.count ? point : top), data[0])

  return (
    <div className="bars">
      <p className="muted small bars-caption">
        {total.toLocaleString()} total · peak {best.count} at {label(best.bucket, byHour)}
        {hovered && (
          <span className="bars-readout">
            {label(hovered.bucket, byHour)}: <strong>{hovered.count}</strong>
          </span>
        )}
      </p>

      <div className="bars-plot" onMouseLeave={() => setHovered(null)}>
        {data.map((point) => (
          <button
            key={point.bucket}
            type="button"
            className={hovered?.bucket === point.bucket ? 'bar hovered' : 'bar'}
            style={{ height: `${Math.max((point.count / peak) * 100, 2)}%` }}
            onMouseEnter={() => setHovered(point)}
            onFocus={() => setHovered(point)}
            aria-label={`${label(point.bucket, byHour)}: ${point.count}`}
          />
        ))}
      </div>

      <div className="bars-axis">
        <span>{label(data[0].bucket, byHour)}</span>
        <span>{label(data[data.length - 1].bucket, byHour)}</span>
      </div>
    </div>
  )
}
