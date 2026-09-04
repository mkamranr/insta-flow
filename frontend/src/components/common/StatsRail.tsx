import { useQuery } from '@tanstack/react-query'
import { getSummary } from '../../api/analytics'

const badge = {
  width: 15,
  height: 15,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
}

/** Floating summary, pinned to the right edge. Opens the analytics drawer. */
export function StatsRail({ onOpen }: { onOpen: () => void }) {
  const { data } = useQuery({
    queryKey: ['summary'],
    queryFn: () => getSummary(7),
    refetchInterval: 30_000,
  })

  if (!data?.connected) return null

  const items = [
    {
      value: data.dms_sent ?? 0,
      label: 'DMS SENT',
      icon: (
        <svg {...badge}>
          <path d="M20 12a7.5 7.5 0 0 1-10.9 6.7L4 20l1.4-4.6A7.5 7.5 0 1 1 20 12Z" />
        </svg>
      ),
    },
    {
      value: data.link_clicks ?? 0,
      label: 'LINK CLICKS',
      // Counted from `link_clicks`, filtered on when the LINK was created, then
      // summed over its lifetime clicks. That is not the same as clicks inside
      // the window, and it is why this can read 5 while the timeline reads 0.
      title: 'All clicks ever recorded on links created in this window',
      icon: (
        <svg {...badge}>
          <path d="M10 13a4 4 0 0 0 5.7.4l2.6-2.6a4 4 0 0 0-5.7-5.7l-1.5 1.5" />
          <path d="M14 11a4 4 0 0 0-5.7-.4l-2.6 2.6a4 4 0 0 0 5.7 5.7l1.5-1.5" />
        </svg>
      ),
    },
    {
      value: `${data.click_through_rate ?? 0}%`,
      label: 'CTR',
      title: 'Link clicks divided by DMs sent. Can exceed 100%: one person may click twice',
      icon: (
        <svg {...badge}>
          <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />
        </svg>
      ),
    },
  ]

  return (
    <button className="stats-rail" onClick={onOpen} aria-label="Open analytics">
      {items.map((item) => (
        <div className="rail-item" key={item.label} title={item.title}>
          <span className="rail-badge">{item.icon}</span>
          <div>
            <div className="rail-value">{item.value}</div>
            <div className="rail-label">{item.label}</div>
          </div>
        </div>
      ))}
    </button>
  )
}
