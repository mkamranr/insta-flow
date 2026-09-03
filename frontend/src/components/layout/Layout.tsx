import { AccountAvatar } from '../common/AccountAvatar'
import { ThemeToggle } from '../common/ThemeToggle'
import { NewAutomationDialog } from '../rules/NewAutomationDialog'
import { RuleEditor } from '../rules/RuleEditor'
import type { RuleTrigger } from '../../api/rules'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { getMe, logout } from '../../api/auth'
import { getSummary } from '../../api/analytics'
import { apiGet } from '../../api/client'
import { listAccounts } from '../../api/instagram'
import type { ReadyResponse } from '../../types/health'
import {
  ActivityIcon,
  AnalyticsIcon,
  ChevronDownIcon,
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

const MY_CONTENT = [{ to: '/posts', label: 'Posts & Reels', icon: <PostsIcon /> }]

const ANALYTICS = [
  { to: '/analytics', label: 'Overview', icon: <AnalyticsIcon /> },
  { to: '/insights', label: 'Audience insights', icon: <ContactsIcon /> },
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
  const [starting, setStarting] = useState(false)
  // The two account-wide triggers open the editor with no post.
  const [newTrigger, setNewTrigger] = useState<RuleTrigger | null>(null)
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
          <AccountAvatar
            className="workspace-avatar"
            url={account?.profile_picture_url}
            name={account?.username ?? me?.email}
            size={30}
          />
          <div>
            <div className="workspace-name">
              {account ? `@${account.username}` : 'No account'}
            </div>
            <div className="small muted">{ready?.dry_run ? 'Dry run' : 'Live'}</div>
          </div>
        </div>

        <button className="cta full" onClick={() => setStarting(true)} style={{ marginBottom: 18 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
            <PlusIcon />
            New automation
          </span>
        </button>

        <nav className="nav">
          {TOP.map(({ to, label, icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <span className="nav-tile">{icon}</span>
              {label}
            </NavLink>
          ))}

          <div className="nav-group">
            <button
              className={contentOpen ? 'nav-toggle open' : 'nav-toggle'}
              onClick={() => setContentOpen((v) => !v)}
            >
              <span className="nav-tile">
                <PostsIcon />
              </span>
              My content
              <span className={contentOpen ? 'nav-chevron open' : 'nav-chevron'}>
                <ChevronDownIcon />
              </span>
            </button>
            {contentOpen && (
              <div className="nav-sub">
                {MY_CONTENT.map(({ to, label, icon }) => (
                  <NavLink key={to} to={to}>
                    <span className="nav-tile">{icon}</span>
                    {label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          <div className="nav-group">
            <button
              className={analyticsNavOpen ? 'nav-toggle open' : 'nav-toggle'}
              onClick={() => setAnalyticsNavOpen((v) => !v)}
            >
              <span className="nav-tile">
                <ActivityIcon />
              </span>
              Analytics
              <span className={analyticsNavOpen ? 'nav-chevron open' : 'nav-chevron'}>
                <ChevronDownIcon />
              </span>
            </button>
            {analyticsNavOpen && (
              <div className="nav-sub">
                {ANALYTICS.map(({ to, label, icon }) => (
                  <NavLink key={to} to={to}>
                    <span className="nav-tile">{icon}</span>
                    {label}
                  </NavLink>
                ))}
              </div>
            )}
          </div>

          {BOTTOM.map(({ to, label, icon }) => (
            <NavLink key={to} to={to}>
              <span className="nav-tile">{icon}</span>
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

          <ThemeToggle />
          <button className="link full" onClick={() => signOut.mutate()}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="content">
        <DryRunBanner />
        <Outlet />

      {starting && (
        <NewAutomationDialog
          onClose={() => setStarting(false)}
          onPick={(trigger) => {
            setStarting(false)
            setNewTrigger(trigger)
          }}
        />
      )}

      {newTrigger && (
        <RuleEditor trigger={newTrigger} onClose={() => setNewTrigger(null)} />
      )}
      </main>

      <StatsRail onOpen={() => setAnalyticsOpen(true)} />
      <AnalyticsDrawer open={analyticsOpen} onClose={() => setAnalyticsOpen(false)} />
    </div>
  )
}
