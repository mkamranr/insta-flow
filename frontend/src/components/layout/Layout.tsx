import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { getMe, logout } from '../../api/auth'
import { getSummary } from '../../api/analytics'
import { apiGet } from '../../api/client'
import { listAccounts } from '../../api/instagram'
import type { ReadyResponse } from '../../types/health'
import {
  ActivityIcon,
  AutomationIcon,
  ContactsIcon,
  HomeIcon,
  InstagramIcon,
  PlusIcon,
  PostsIcon,
  SettingsIcon,
  TemplatesIcon,
} from '../icons/Icons'
import { AnalyticsDrawer } from '../common/AnalyticsDrawer'
import { DryRunBanner } from '../common/DryRunBanner'
import { StatsRail } from '../common/StatsRail'

const TOP = [
  { to: '/', label: 'Home', icon: <HomeIcon />, end: true },
  { to: '/automations', label: 'Automations', icon: <AutomationIcon /> },
  { to: '/templates', label: 'Templates', icon: <TemplatesIcon /> },
]

const MY_CONTENT = [{ to: '/posts', label: 'Posts & Reels' }]

const ANALYTICS = [
  { to: '/analytics', label: 'Overview' },
  { to: '/insights', label: 'Audience insights' },
]

const BOTTOM = [
  { to: '/contacts', label: 'Contacts', icon: <ContactsIcon /> },
  { to: '/instagram', label: 'Instagram', icon: <InstagramIcon /> },
  { to: '/settings', label: 'Settings', icon: <SettingsIcon /> },
]

/** Meta's documented ceiling: 750 private replies per hour, per account. */
const HOURLY_CEILING = 750

export function Layout() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [analyticsOpen, setAnalyticsOpen] = useState(false)
  const [contentOpen, setContentOpen] = useState(true)
  const [analyticsNavOpen, setAnalyticsNavOpen] = useState(true)

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe })
  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const { data: summary } = useQuery({ queryKey: ['summary'], queryFn: () => getSummary(7) })
  const { data: ready } = useQuery({
    queryKey: ['ready'],
    queryFn: () => apiGet<ReadyResponse>('/ready'),
  })

  const signOut = useMutation({
    mutationFn: logout,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['me'] }),
  })

  const account = accounts?.[0]
  const sent = summary?.dms_sent ?? 0
  const sentPct = Math.min((sent / HOURLY_CEILING) * 100, 100)

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <img src="/logo.png" alt="" width="22" height="22" />
          <span>SOCIAL FLOW</span>
        </div>

        <div className="workspace">
          <div className="workspace-avatar">
            {(account?.username ?? me?.email ?? '?').slice(0, 1).toUpperCase()}
          </div>
          <div>
            <div className="workspace-name">
              {account ? `@${account.username}` : 'No account'}
            </div>
            <div className="small muted">{ready?.dry_run ? 'Dry run' : 'Live'}</div>
          </div>
        </div>

        <button className="cta full" onClick={() => navigate('/posts')} style={{ marginBottom: 18 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <PlusIcon />
            New automation
          </span>
        </button>

        <nav className="nav">
          {TOP.map(({ to, label, icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              {icon}
              {label}
            </NavLink>
          ))}

          <div className="nav-group">
            <button className="nav-toggle" onClick={() => setContentOpen((v) => !v)}>
              <PostsIcon />
              My content
              <span className="nav-chevron">{contentOpen ? '▾' : '▸'}</span>
            </button>
            {contentOpen && (
              <div className="nav-sub">
                {MY_CONTENT.map(({ to, label }) => (
                  <NavLink key={to} to={to}>
                    {label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          <div className="nav-group">
            <button className="nav-toggle" onClick={() => setAnalyticsNavOpen((v) => !v)}>
              <ActivityIcon />
              Analytics
              <span className="nav-chevron">{analyticsNavOpen ? '▾' : '▸'}</span>
            </button>
            {analyticsNavOpen && (
              <div className="nav-sub">
                {ANALYTICS.map(({ to, label }) => (
                  <NavLink key={to} to={to}>
                    {label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {BOTTOM.map(({ to, label, icon }) => (
            <NavLink key={to} to={to}>
              {icon}
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-foot">
          <div className="meter">
            <div className="meter-row">
              <span className="muted">DMs sent (7d)</span>
              <span style={{ fontWeight: 600 }}>{sent}</span>
            </div>
            <div className="meter-track">
              <div className="meter-fill" style={{ width: `${sentPct}%` }} />
            </div>
            <div className="small faint" style={{ marginTop: 4 }}>
              Meta allows {HOURLY_CEILING}/hour
            </div>
          </div>

          <div className="meter">
            <div className="meter-row">
              <span className="muted">Instagram accounts</span>
              <span style={{ fontWeight: 600 }}>{accounts?.length ?? 0}</span>
            </div>
            <div className="meter-track">
              <div
                className="meter-fill dark"
                style={{ width: accounts?.length ? '100%' : '0%' }}
              />
            </div>
          </div>

          <button className="link full" onClick={() => signOut.mutate()}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="content">
        <DryRunBanner />
        <Outlet />
      </main>

      <StatsRail onOpen={() => setAnalyticsOpen(true)} />
      <AnalyticsDrawer open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
    </div>
  )
}
