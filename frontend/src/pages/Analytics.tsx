import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  getAccountPerformance,
  getPostPerformance,
  getRulePerformance,
  getRulePlaces,
  getAccountPlaces,
  getHighlights,
  getTrends,
  getSummary,
  type AccountPerformance as AccountData,
  type PlaceRow,
  type PostPerformance,
  type RulePerformance,
} from '../api/analytics'
import { BarSeries } from '../components/analytics/BarSeries'
import { ActivityLog } from './Activity'

const TABS = [
  { id: 'performance', label: 'Performance' },
  { id: 'activity', label: 'Activity Log' },
  { id: 'account', label: 'Account Performance' },
] as const

const RANGES = [
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
]

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: React.ReactNode
  hint?: string
}) {
  return (
    <div className="stat">
      <div className="stat-top">
        <div className="stat-label">{label}</div>
      </div>
      <div className="stat-value">{value}</div>
      {hint && <div className="small muted">{hint}</div>}
    </div>
  )
}

const num = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString())

// -- Performance -------------------------------------------------------------

function Performance({ days }: { days: number }) {
  const { data: summary } = useQuery({
    queryKey: ['summary', days],
    queryFn: () => getSummary(days),
    refetchInterval: 30_000,
  })
  const { data: rules } = useQuery({ queryKey: ['rule-performance'], queryFn: getRulePerformance })
  const { data: posts } = useQuery({ queryKey: ['post-performance'], queryFn: getPostPerformance })

  if (summary && !summary.connected) {
    return <p className="muted">Connect an Instagram account to see performance.</p>
  }

  const withComments = (posts ?? []).filter((p) => p.comments > 0)

  return (
    <>
      <Highlights days={days} />

      <section className="section">
        <div className="section-head">
          <div>
            <h2>Key metrics</h2>
            <p className="muted small">Comments that arrived, and what the automations did.</p>
          </div>
        </div>
        <div className="stat-grid">
          <Stat label="DMs sent" value={num(summary?.dms_sent)} />
          <Stat label="Link clicks" value={num(summary?.link_clicks)} />
          <Stat
            label="Click-through rate"
            value={summary?.click_through_rate == null ? '—' : `${summary.click_through_rate}%`}
            hint="Clicks per DM sent"
          />
          <Stat label="Leads captured" value={num(summary?.leads_captured)} />
        </div>
        <div className="stat-grid" style={{ marginTop: 12 }}>
          <Stat label="Comments" value={num(summary?.comments)} />
          <Stat label="Matched a rule" value={num(summary?.matched)} />
          <Stat label="Skipped" value={num(summary?.skipped)} />
          <Stat label="Failed" value={num(summary?.failed)} />
        </div>
      </section>

      <section className="section">
        <AutomationPerformance rules={rules} posts={posts} />
      </section>

      <EngagementTrends days={days} />

      <section className="section">
        <div className="section-head">
          <div>
            <h2>Geographic distribution</h2>
            <p className="muted small">
              Where link clicks came from, across every automation.
            </p>
          </div>
        </div>
        <div className="card">
          <AccountPlaces days={days} />
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <div>
            <h2>Content performance</h2>
            <p className="muted small">
              Trigger rate is the share of a post&rsquo;s comments that actually fired an
              automation.
            </p>
          </div>
        </div>
        {withComments.length ? (
          <div className="card table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>Content</th>
                  <th className="numeric">Comments</th>
                  <th className="numeric">Triggered</th>
                  <th className="numeric">Trigger rate</th>
                </tr>
              </thead>
              <tbody>
                {withComments.map((post) => (
                  <tr key={post.id}>
                    <td>
                      <div className="media-cell">
                        {post.thumbnail_url && (
                          <img className="media-thumb" src={post.thumbnail_url} alt="" />
                        )}
                        <span className="clamp">{post.caption || post.instagram_media_id}</span>
                      </div>
                    </td>
                    <td className="numeric">{post.comments.toLocaleString()}</td>
                    <td className="numeric">{post.keyword_comments.toLocaleString()}</td>
                    <td className="numeric">{post.trigger_rate}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="card">
            <p className="muted">No comments on any post yet.</p>
          </div>
        )}
      </section>
    </>
  )
}

// -- Highlights, trends and geography ----------------------------------------

function HighlightCard({
  kicker,
  headline,
  detail,
}: {
  kicker: string
  headline: React.ReactNode
  detail?: React.ReactNode
}) {
  return (
    <div className="stat highlight">
      <div className="highlight-kicker">{kicker}</div>
      <div className="highlight-headline">{headline}</div>
      {detail && <div className="muted small">{detail}</div>}
    </div>
  )
}

function niceDay(bucket?: string | null) {
  if (!bucket) return null
  const date = new Date(`${bucket}T00:00:00Z`)
  return Number.isNaN(date.getTime())
    ? bucket
    : date.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' })
}

function Highlights({ days }: { days: number }) {
  const { data } = useQuery({
    queryKey: ['highlights', days],
    queryFn: () => getHighlights(days),
  })
  if (!data?.connected) return null

  // Every card can be blank. "Nothing yet" is a real answer, and inventing a
  // winner from an empty set would be worse than showing none.
  const none = <span className="muted">Nothing yet</span>

  return (
    <section className="section">
      <div className="section-head">
        <div>
          <h2>Highlights</h2>
          <p className="muted small">The standout result from each section.</p>
        </div>
      </div>
      <div className="stat-grid">
        <HighlightCard
          kicker="Best automation"
          headline={data.best_automation ? <mark>{data.best_automation.name}</mark> : none}
          detail={
            data.best_automation
              ? `${data.best_automation.click_through_rate}% CTR · ${data.best_automation.dms_sent} DMs · ${data.best_automation.link_clicks} clicks`
              : 'No automation has sent a DM yet.'
          }
        />
        <HighlightCard
          kicker="Top day for DMs"
          headline={data.top_dm_day ? <mark>{niceDay(data.top_dm_day.bucket)}</mark> : none}
          detail={data.top_dm_day ? `${data.top_dm_day.count} DMs sent — busiest day.` : undefined}
        />
        <HighlightCard
          kicker="Top day for clicks"
          headline={data.top_click_day ? <mark>{niceDay(data.top_click_day.bucket)}</mark> : none}
          detail={
            data.top_click_day ? `${data.top_click_day.count} link clicks — peak day.` : undefined
          }
        />
        <HighlightCard
          kicker="Top country"
          headline={data.top_country ? <mark>{data.top_country.country}</mark> : none}
          detail={
            data.top_country
              ? `${data.top_country.clicks} clicks`
              : 'Needs a MaxMind key in Settings, and a click after it.'
          }
        />
      </div>
    </section>
  )
}

function EngagementTrends({ days }: { days: number }) {
  const [bucket, setBucket] = useState<'hour' | 'day'>('hour')
  const { data } = useQuery({
    queryKey: ['trends', days, bucket],
    queryFn: () => getTrends(days, bucket),
  })

  return (
    <section className="section">
      <div className="section-head">
        <div>
          <h2>Engagement trends</h2>
          <p className="muted small">When the DMs went out, and when they were clicked.</p>
        </div>
        <div className="filters" style={{ margin: 0 }}>
          {(['hour', 'day'] as const).map((id) => (
            <button
              key={id}
              className={bucket === id ? 'filter active' : 'filter'}
              onClick={() => setBucket(id)}
            >
              {id === 'hour' ? 'Hourly' : 'Daily'}
            </button>
          ))}
        </div>
      </div>

      <div className="trend-grid">
        <div className="card">
          <h3 className="small">DMs sent</h3>
          <BarSeries
            data={data?.dms ?? []}
            byHour={bucket === 'hour'}
            emptyNote="No DMs sent in this window."
          />
        </div>
        <div className="card">
          <h3 className="small">Link clicks</h3>
          <BarSeries
            data={data?.clicks ?? []}
            byHour={bucket === 'hour'}
            emptyNote="No clicks recorded in this window. Clicks are counted per click from the day this was added."
          />
        </div>
      </div>
    </section>
  )
}

function AccountPlaces({ days }: { days: number }) {
  const [by, setBy] = useState<'country' | 'city'>('country')
  const { data } = useQuery({
    queryKey: ['account-places', days, by],
    queryFn: () => getAccountPlaces(days, by),
  })
  if (!data) return <p className="muted small">Loading…</p>

  return (
    <>
      <div className="auto-body-head">
        <div className="filters" style={{ margin: 0 }}>
          {(['country', 'city'] as const).map((id) => (
            <button
              key={id}
              className={by === id ? 'filter active' : 'filter'}
              onClick={() => setBy(id)}
            >
              {id === 'country' ? 'Country' : 'City'}
            </button>
          ))}
        </div>
        <span className="muted small">
          {data.located} of {data.total_clicks} clicks placed · {data.countries} countries
        </span>
      </div>
      <PlaceRows rows={data.rows} located={data.located} />
    </>
  )
}

// -- Automation performance --------------------------------------------------

const SORTS = [
  { id: 'newest', label: 'Newest first' },
  { id: 'dms', label: 'Most DMs sent' },
  { id: 'ctr', label: 'Best CTR' },
] as const

function AutomationCard({
  rule,
  thumbnail,
  open,
  onToggle,
}: {
  rule: RulePerformance
  thumbnail?: string | null
  open: boolean
  onToggle: () => void
}) {
  const [panel, setPanel] = useState<'links' | 'places'>('links')
  const linkCount = rule.links.length
  const top = rule.links[0]

  return (
    <div className="auto-card">
      <button className="auto-head" onClick={onToggle} aria-expanded={open}>
        {thumbnail ? (
          <img className="auto-thumb" src={thumbnail} alt="" />
        ) : (
          <div className="auto-thumb auto-thumb-empty" aria-hidden="true" />
        )}

        <div className="auto-id">
          <div className="auto-name">
            {rule.name}
            {linkCount > 0 && (
              <span className="pill">
                {linkCount} link{linkCount === 1 ? '' : 's'}
              </span>
            )}
            {!rule.enabled && <span className="pill pill-muted">Paused</span>}
          </div>
          <div className="muted small">{rule.dms_sent.toLocaleString()} DMs sent</div>
        </div>

        <div className="auto-ctr">
          <div
            className={
              rule.click_through_rate > 0 ? 'auto-ctr-value' : 'auto-ctr-value zero'
            }
          >
            {rule.click_through_rate}%
          </div>
          <div className="muted small">CTR</div>
        </div>

        <span className={open ? 'chev open' : 'chev'} aria-hidden="true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="auto-body">
          <div className="filters" style={{ margin: '12px 0 4px' }}>
            {(['links', 'places'] as const).map((id) => (
              <button
                key={id}
                className={panel === id ? 'filter active' : 'filter'}
                onClick={() => setPanel(id)}
              >
                {id === 'links' ? 'Link performance' : 'Geographic distribution'}
              </button>
            ))}
          </div>

          {panel === 'places' ? (
            <PlacesPanel ruleId={rule.id} />
          ) : (
          <>
          <div className="auto-body-head">
            <strong className="small">Link performance</strong>
            {top && (
              <span className="muted small">
                Top link: <strong>{top.title}</strong>
              </span>
            )}
          </div>

          {linkCount === 0 ? (
            <p className="muted small">
              This automation has no tracked links, so there are no clicks to report.
            </p>
          ) : (
            rule.links.map((link) => (
              <div className="link-row" key={link.target_url + link.title}>
                <div className="link-id">
                  <div className="link-title">{link.title}</div>
                  <a
                    className="link-url"
                    href={link.target_url}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {link.target_url}
                  </a>
                </div>
                <div className="link-metrics">
                  <div className="link-metric">
                    <div className="link-metric-value">{link.clicks.toLocaleString()}</div>
                    <div className="muted small">clicks</div>
                  </div>
                  <div className={link.clicks > 0 ? 'link-metric emphasis' : 'link-metric'}>
                    <div className="link-metric-value">{link.click_through_rate}%</div>
                    <div className="muted small">CTR</div>
                  </div>
                  <div className="link-metric">
                    <div className="link-metric-value">{link.unique.toLocaleString()}</div>
                    <div className="muted small">unique</div>
                  </div>
                </div>
              </div>
            ))
          )}
          </>
          )}
        </div>
      )}
    </div>
  )
}

function PlaceRows({ rows, located }: { rows: PlaceRow[]; located: number }) {
  if (rows.length === 0) {
    return (
      <p className="muted small">
        No clicks have been placed yet. Locations are resolved at click time, so only clicks
        made after the GeoLite2 database was installed carry one — add your MaxMind key under
        Settings if you have not.
      </p>
    )
  }
  const peak = rows[0]?.clicks ?? 0
  return (
    <div className="place-list">
      {rows.slice(0, 12).map((row) => {
        const name = row.city ? `${row.city} (${row.country ?? '—'})` : row.country
        const share = located ? Math.round((row.clicks / located) * 100) : 0
        return (
          <div className="place-row" key={`${row.country_code}-${row.city ?? ''}`}>
            <span className="place-name">{name ?? 'Unknown'}</span>
            <span className="place-track" aria-hidden="true">
              <span
                className="place-bar"
                style={{ width: `${peak ? (row.clicks / peak) * 100 : 0}%` }}
              />
            </span>
            <span className="place-count">{row.clicks.toLocaleString()}</span>
            <span className="muted small place-share">{share}%</span>
          </div>
        )
      })}
    </div>
  )
}

function PlacesPanel({ ruleId }: { ruleId: string }) {
  const [by, setBy] = useState<'country' | 'city'>('country')
  const { data, isLoading } = useQuery({
    queryKey: ['rule-places', ruleId, by],
    queryFn: () => getRulePlaces(ruleId, by),
  })

  if (isLoading) return <p className="muted small">Loading…</p>
  if (!data) return null

  const rows = data.rows

  return (
    <>
      <div className="auto-body-head">
        <div className="filters" style={{ margin: 0 }}>
          {(['country', 'city'] as const).map((id) => (
            <button
              key={id}
              className={by === id ? 'filter active' : 'filter'}
              onClick={() => setBy(id)}
            >
              {id === 'country' ? 'Country' : 'City'}
            </button>
          ))}
        </div>
        <span className="muted small">
          {data.located} of {data.total_clicks} clicks placed · {data.countries} countries
        </span>
      </div>

      <PlaceRows rows={rows} located={data.located} />
    </>
  )
}

function AutomationPerformance({
  rules,
  posts,
}: {
  rules?: RulePerformance[]
  posts?: PostPerformance[]
}) {
  const [sort, setSort] = useState<(typeof SORTS)[number]['id']>('newest')
  const [openId, setOpenId] = useState<string | null>(null)

  if (!rules?.length) {
    return (
      <div className="card">
        <p className="muted">No automations yet.</p>
      </div>
    )
  }

  const thumbFor = (rule: RulePerformance) =>
    posts?.find((p) => rule.instagram_media_ids.includes(p.instagram_media_id))?.thumbnail_url

  const sorted = [...rules].sort((a, b) => {
    if (sort === 'dms') return b.dms_sent - a.dms_sent
    if (sort === 'ctr') return b.click_through_rate - a.click_through_rate
    // Mongo ids start with a timestamp, so descending id is newest-first.
    return a.id < b.id ? 1 : -1
  })

  return (
    <div className="card auto-list">
      <div className="auto-list-head">
        <h2>Automation performance</h2>
        <select
          className="select"
          value={sort}
          onChange={(e) => setSort(e.target.value as (typeof SORTS)[number]['id'])}
          aria-label="Sort automations"
        >
          {SORTS.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      {sorted.map((rule) => (
        <AutomationCard
          key={rule.id}
          rule={rule}
          thumbnail={thumbFor(rule)}
          open={openId === rule.id}
          onToggle={() => setOpenId(openId === rule.id ? null : rule.id)}
        />
      ))}
    </div>
  )
}

// -- Account performance -----------------------------------------------------

/**
 * Follower count over time. One series, so no legend — the heading names it.
 *
 * The line is ink rather than the lime accent: `--accent` is ~1.3:1 on white,
 * which is fine for a CTA button and unreadable as a data mark.
 */
function FollowerSparkline({ series }: { series: { day: string; followers_count: number }[] }) {
  const W = 720
  const H = 132
  const PAD = { t: 18, r: 16, b: 26, l: 16 }

  const values = series.map((p) => p.followers_count)
  const min = Math.min(...values)
  const max = Math.max(...values)
  // A flat line would otherwise divide by zero and collapse to the top edge.
  const span = max - min || 1

  const x = (i: number) =>
    PAD.l + (i * (W - PAD.l - PAD.r)) / Math.max(series.length - 1, 1)
  const y = (v: number) => PAD.t + (1 - (v - min) / span) * (H - PAD.t - PAD.b)

  const line = series.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p.followers_count)}`).join(' ')
  const area = `${line} L ${x(series.length - 1)} ${H - PAD.b} L ${x(0)} ${H - PAD.b} Z`

  const first = series[0]
  const last = series[series.length - 1]

  return (
    <svg
      className="spark"
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      role="img"
      aria-label={`Followers from ${first.day} to ${last.day}`}
    >
      {/* Recessive baseline; no gridlines competing with a 7-point series. */}
      <line
        x1={PAD.l}
        x2={W - PAD.r}
        y1={H - PAD.b}
        y2={H - PAD.b}
        stroke="var(--border)"
        strokeWidth="1"
        vectorEffect="non-scaling-stroke"
      />
      <path d={area} fill="var(--surface-alt)" />
      <path
        d={line}
        fill="none"
        stroke="var(--dark)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {series.map((p, i) => (
        // 2px surface ring so points stay legible where the line doubles back.
        <circle
          key={p.day}
          cx={x(i)}
          cy={y(p.followers_count)}
          r="4"
          fill="var(--surface)"
          stroke="var(--dark)"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        >
          <title>{`${p.day}: ${p.followers_count.toLocaleString()} followers`}</title>
        </circle>
      ))}
      {/* Selective direct labels — endpoints only, never every point. */}
      <text x={x(0)} y={H - 8} className="spark-label" textAnchor="start">
        {first.day.slice(5)}
      </text>
      <text x={x(series.length - 1)} y={H - 8} className="spark-label" textAnchor="end">
        {last.day.slice(5)}
      </text>
    </svg>
  )
}

function NetGrowth({ value }: { value: number | null | undefined }) {
  if (value == null) return <>—</>
  // Never colour alone: the glyph and the sign carry the same meaning.
  const cls = value > 0 ? 'ok' : value < 0 ? 'err' : 'muted'
  const glyph = value > 0 ? '▲' : value < 0 ? '▼' : '—'
  return (
    <span className={cls}>
      {glyph} {value > 0 ? '+' : ''}
      {value.toLocaleString()}
    </span>
  )
}

function AccountPerformance({ days, data }: { days: number; data?: AccountData }) {
  const [showTable, setShowTable] = useState(false)

  if (data && !data.connected) {
    return <p className="muted">Connect an Instagram account to see follower data.</p>
  }
  if (!data) return <p className="muted">Loading…</p>

  const series = data.series ?? []
  const enoughToPlot = series.length >= 2

  return (
    <>
      <section className="section">
        <div className="section-head">
          <div>
            <h2>Follower growth</h2>
            <p className="muted small">
              Recorded daily by Social Flow. Instagram does not provide history, so these begin
              the day the account was connected.
            </p>
          </div>
        </div>

        <div className="stat-grid">
          <Stat label="Total followers" value={num(data.followers_count)} />
          <Stat
            label={`Net growth (${days}d)`}
            value={<NetGrowth value={data.net_growth} />}
            hint={enoughToPlot ? undefined : 'Needs two days of data'}
          />
          <Stat label="Following" value={num(data.follows_count)} />
          <Stat label="Posts" value={num(data.media_count)} />
        </div>
      </section>

      <section className="section">
        {enoughToPlot ? (
          <div className="card">
            <div className="section-head">
              <div>
                <h2>Followers over time</h2>
                <p className="muted small">
                  {series.length} daily snapshot{series.length === 1 ? '' : 's'}
                  {data.best_day && (
                    <>
                      {' '}
                      · best day {data.best_day.day.slice(5)} (
                      {data.best_day.change > 0 ? '+' : ''}
                      {data.best_day.change})
                    </>
                  )}
                </p>
              </div>
              <div className="section-actions">
                <button className="filter" onClick={() => setShowTable((v) => !v)}>
                  {showTable ? 'Show chart' : 'Show table'}
                </button>
              </div>
            </div>

            {showTable ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th className="numeric">Followers</th>
                    <th className="numeric">Change</th>
                  </tr>
                </thead>
                <tbody>
                  {series.map((point, i) => (
                    <tr key={point.day}>
                      <td>{point.day}</td>
                      <td className="numeric">{point.followers_count.toLocaleString()}</td>
                      <td className="numeric">
                        {i === 0 ? (
                          '—'
                        ) : (
                          <NetGrowth
                            value={point.followers_count - series[i - 1].followers_count}
                          />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <FollowerSparkline series={series} />
            )}
          </div>
        ) : (
          <div className="warn-card">
            <strong>Collecting since {data.collecting_since ?? 'today'}</strong>
            <p className="small">
              Growth needs at least two daily snapshots. One number cannot express a change, so
              nothing is shown rather than a misleading zero. The next snapshot is recorded just
              after midnight UTC.
            </p>
          </div>
        )}
      </section>

      <section className="section">
        <div className="card">
          <h2>What Instagram will not tell us</h2>
          <p className="muted small">
            {data.gross_growth_note ??
              'Meta reports a follower total only, with no history.'}
          </p>
          <p className="muted small">
            That is why there are no <em>new followers</em> and <em>unfollowers</em> tiles here:
            a day that gained 5 and lost 3 is indistinguishable from one that gained 2 and lost
            none. Net change is exact; the two halves are not knowable, so they are not shown.
          </p>
        </div>
      </section>
    </>
  )
}

// -- Page --------------------------------------------------------------------

export function Analytics() {
  const [params, setParams] = useSearchParams()
  const [days, setDays] = useState(7)
  const tab = params.get('tab') ?? 'performance'

  const { data: account } = useQuery({
    queryKey: ['account-performance', days],
    queryFn: () => getAccountPerformance(days),
    refetchInterval: 60_000,
  })

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Analytics</h1>
          <p className="muted">
            DMs sent, link clicks, top automations, and how the account is growing.
          </p>
        </div>
        <div className="filters" style={{ margin: 0 }}>
          {RANGES.map((range) => (
            <button
              key={range.days}
              className={days === range.days ? 'filter active' : 'filter'}
              onClick={() => setDays(range.days)}
            >
              {range.label}
            </button>
          ))}
        </div>
      </div>

      <div className="tabs" role="tablist">
        {TABS.map((item) => (
          <button
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? 'tab active' : 'tab'}
            onClick={() => setParams(item.id === 'performance' ? {} : { tab: item.id })}
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'performance' && <Performance days={days} />}
      {tab === 'activity' && <ActivityLog />}
      {tab === 'account' && <AccountPerformance days={days} data={account} />}
    </>
  )
}
