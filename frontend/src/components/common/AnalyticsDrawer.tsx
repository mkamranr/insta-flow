import { CloseIcon } from '../icons/Icons'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { getRulePerformance, getSummary } from '../../api/analytics'

const RANGES = [7, 30, 90] as const

export function AnalyticsDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [days, setDays] = useState<number>(7)

  const { data: summary } = useQuery({
    queryKey: ['summary', days],
    queryFn: () => getSummary(days),
    enabled: open,
  })
  const { data: rules } = useQuery({
    queryKey: ['rule-performance'],
    queryFn: getRulePerformance,
    enabled: open,
  })

  if (!open) return null

  const best = rules?.filter((r) => r.dms_sent > 0).sort((a, b) =>
    b.click_through_rate - a.click_through_rate,
  )

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Analytics">
        <header className="drawer-head">
          <h2>Analytics</h2>
          <div className="range">
            {RANGES.map((r) => (
              <button
                key={r}
                className={days === r ? 'range-btn active' : 'range-btn'}
                onClick={() => setDays(r)}
              >
                {r}d
              </button>
            ))}
          </div>
          <button className="link" onClick={onClose} aria-label="Close">
            <CloseIcon />
          </button>
        </header>

        <div className="drawer-body">
          <h3>Key metrics</h3>
          <div className="drawer-grid">
            <Metric label="DMs sent" value={summary?.dms_sent ?? 0} />
            <Metric label="Link clicks" value={summary?.link_clicks ?? 0} />
            <Metric
              label="CTR"
              value={`${summary?.click_through_rate ?? 0}%`}
              hint="Clicks per DM sent"
            />
            <Metric
              label="Leads captured"
              value={summary?.leads_captured ?? 0}
              hint={
                summary?.dms_sent
                  ? `${Math.round(((summary.leads_captured ?? 0) / summary.dms_sent) * 100)}% of DMs collected email`
                  : undefined
              }
            />
          </div>

          <h3 style={{ marginTop: 26 }}>Comments</h3>
          <div className="drawer-grid">
            <Metric label="Received" value={summary?.comments ?? 0} />
            <Metric label="Matched" value={summary?.matched ?? 0} />
            <Metric label="Unmatched" value={summary?.unmatched ?? 0} />
            <Metric label="Dry runs" value={summary?.dry_runs ?? 0} hint="Recorded, not sent" />
          </div>

          <h3 style={{ marginTop: 26 }}>Top performers</h3>
          <p className="muted small" style={{ marginTop: -6 }}>
            Your best automations by click-through rate.
          </p>
          {!best?.length && <p className="muted small">Nothing has sent a DM yet.</p>}
          {best?.map((rule, i) => (
            <div className="performer" key={rule.id}>
              <span className="rank">{i + 1}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{rule.name}</div>
                <div className="muted small">
                  {rule.keywords.join(', ') || 'any word'}
                </div>
              </div>
              <span className="muted small">{rule.dms_sent}</span>
              <span className="muted small">{rule.link_clicks}</span>
              <span className="ctr-pill">{rule.click_through_rate}%</span>
            </div>
          ))}

          <p className="muted small" style={{ marginTop: 24 }}>
            No delivery or read rates are shown: Instagram does not report either, so any
            number here would be invented.
          </p>
        </div>
      </aside>
    </>
  )
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string
  value: string | number
  hint?: string
}) {
  return (
    <div className="drawer-metric">
      <div className="muted small">{label}</div>
      <div className="drawer-value">{value}</div>
      {hint && <div className="faint small">{hint}</div>}
    </div>
  )
}
