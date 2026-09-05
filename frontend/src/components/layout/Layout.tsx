import { AccountAvatar } from '../common/AccountAvatar'
import { ThemeToggle } from '../common/ThemeToggle'
import { NewAutomationDialog } from '../rules/NewAutomationDialog'
import { RuleEditor } from '../rules/RuleEditor'
import type { RuleTrigger } from '../../api/rules'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { type ReactNode, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { getMe, logout } from '../../api/auth'
import { getSummary } from '../../api/analytics'
import { apiGetInstance } from '../../api/client'
import { listAccounts } from '../../api/instagram'
import { listWorkspaces } from '../../api/workspaces'
import { switchWorkspace, workspaceSlug } from '../../app/workspace'
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
  StoriesIcon,
  UpcomingIcon,
  SettingsIcon,
  ShieldIcon,
  TemplatesIcon,
  UsersIcon,
  KeyIcon,
} from '../icons/Icons'
import { AnalyticsDrawer } from '../common/AnalyticsDrawer'
import { DryRunBanner } from '../common/DryRunBanner'
import { StatsRail } from '../common/StatsRail'
import { usePermissions } from '../../hooks/usePermissions'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  end?: boolean
  /** Undefined means "everyone who can sign in". */
  permission?: string
}

// Home carries no permission on purpose: it is the target of the logo, of the
// "go back" link on the no-access card, and of every post-login redirect. A `/`
// that can refuse you needs a redirect matrix and can strand a narrow role on a
// blank screen, so the Dashboard gates its own sections instead.
const TOP: NavItem[] = [
  { to: '/', label: 'Home', icon: <HomeIcon />, end: true },
  {
    to: '/automations',
    label: 'Automations',
    icon: <AutomationIcon />,
    permission: 'automations.view',
  },
  {
    to: '/templates',
    label: 'Templates',
    icon: <TemplatesIcon />,
    permission: 'automations.view',
  },
]

const MY_CONTENT: NavItem[] = [
  { to: '/posts', label: 'Posts & Reels', icon: <PostsIcon />, permission: 'content.view' },
  { to: '/stories', label: 'Stories', icon: <StoriesIcon />, permission: 'content.view' },
  {
    to: '/upcoming',
    label: 'Upcoming post',
    icon: <UpcomingIcon />,
    permission: 'automations.view',
  },
]

const ANALYTICS: NavItem[] = [
  { to: '/analytics', label: 'Overview', icon: <AnalyticsIcon />, permission: 'analytics.view' },
  {
    to: '/insights',
    label: 'Audience insights',
    icon: <ContactsIcon />,
    permission: 'analytics.view',
  },
]

const BOTTOM: NavItem[] = [
  { to: '/contacts', label: 'Contacts', icon: <ContactsIcon />, permission: 'contacts.view' },
  { to: '/instagram', label: 'Instagram', icon: <InstagramIcon />, permission: 'instagram.view' },
  { to: '/settings', label: 'Settings', icon: <SettingsIcon />, permission: 'settings.view' },
]

const ADMIN: NavItem[] = [
  { to: '/users', label: 'Users', icon: <UsersIcon />, permission: 'users.view' },
  { to: '/roles', label: 'Roles', icon: <KeyIcon />, permission: 'roles.view' },
]

function NavGroup({
  label,
  icon,
  items,
}: {
  label: string
  icon: ReactNode
  items: NavItem[]
}) {
  const [open, setOpen] = useState(true)
  // A header that opens onto nothing is worse than no header, so the whole group
  // goes -- toggle included. The guard lives *inside* the component so that a
  // background ['me'] refetch cannot unmount it and reset the open/closed choice.
  if (items.length === 0) return null

  return (
    <div className="nav-group">
      <button
        className={open ? 'nav-toggle open' : 'nav-toggle'}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="nav-tile">{icon}</span>
        {label}
        <span className={open ? 'nav-chevron open' : 'nav-chevron'}>
          <ChevronDownIcon />
        </span>
      </button>
      {open && (
        <div className="nav-sub">
          {items.map(({ to, label: itemLabel, icon: itemIcon }) => (
            <NavLink key={to} to={to}>
              <span className="nav-tile">{itemIcon}</span>
              {itemLabel}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  )
}

/** Meta's documented ceiling: 750 private replies per hour, per account. */
const HOURLY_CEILING = 750

/**
 * The account this dashboard is currently showing, and — for anyone holding
 * `workspaces.view` — a way to move to someone else's.
 *
 * Sits under the logo because that is where "which account am I looking at"
 * belongs, and because the answer is already in the URL beside it.
 *
 * Renders as the plain, unclickable block it always was when there is only one
 * workspace, which is every ordinary user. A switcher that opens onto a list of
 * one is a control that teaches people it does nothing.
 */
function WorkspaceSwitcher({
  accountName,
  avatarUrl,
  status,
}: {
  accountName: string
  avatarUrl?: string | null
  status: string
}) {
  const [open, setOpen] = useState(false)
  const { data: workspaces } = useQuery({ queryKey: ['workspaces'], queryFn: listWorkspaces })

  const others = (workspaces ?? []).filter((w) => w.slug !== workspaceSlug)
  const canSwitch = others.length > 0

  const body = (
    <>
      <AccountAvatar
        className="workspace-avatar"
        url={avatarUrl}
        name={accountName}
        size={30}
      />
      <div className="workspace-id">
        <div className="workspace-name">{accountName}</div>
        <div className="small muted">{status}</div>
      </div>
      {canSwitch && (
        <span className={open ? 'nav-chevron open' : 'nav-chevron'}>
          <ChevronDownIcon />
        </span>
      )}
    </>
  )

  if (!canSwitch) return <div className="workspace">{body}</div>

  return (
    <div className="workspace-wrap">
      <button
        className="workspace as-button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {body}
      </button>

      {open && (
        <>
          {/* Closes on any outside click without a document listener to unbind. */}
          <div className="dropdown-backdrop" onClick={() => setOpen(false)} />
          <div className="workspace-menu">
            {(workspaces ?? []).map((w) => (
              <button
                key={w.slug}
                className={w.slug === workspaceSlug ? 'workspace-option current' : 'workspace-option'}
                onClick={() => (w.slug === workspaceSlug ? setOpen(false) : switchWorkspace(w.slug))}
              >
                <AccountAvatar url={w.profile_picture_url} name={w.username ?? w.email} size={24} />
                <span className="workspace-option-text">
                  <span className="workspace-option-name">
                    {w.username ? `@${w.username}` : 'No account'}
                  </span>
                  <span className="small faint">{w.email}</span>
                </span>
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

export function Layout() {
  const queryClient = useQueryClient()
  const [starting, setStarting] = useState(false)
  // The two account-wide triggers open the editor with no post.
  const [newTrigger, setNewTrigger] = useState<RuleTrigger | null>(null)
  const [analyticsOpen, setAnalyticsOpen] = useState(false)
  const { can } = usePermissions()

  // ['me'] is already resolved by RequireAuth, so this filter runs with real data
  // on the very first render -- nothing appears and then vanishes.
  const visible = (items: NavItem[]) => items.filter((item) => can(item.permission))

  const { data: me } = useQuery({ queryKey: ['me'], queryFn: getMe })
  const { data: accounts } = useQuery({ queryKey: ['instagram-accounts'], queryFn: listAccounts })
  const { data: summary } = useQuery({ queryKey: ['summary'], queryFn: () => getSummary(7) })
  const { data: ready } = useQuery({
    queryKey: ['ready'],
    queryFn: () => apiGetInstance<ReadyResponse>('/ready'),
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

        <WorkspaceSwitcher
          accountName={account ? `@${account.username}` : (me?.email ?? 'No account')}
          avatarUrl={account?.profile_picture_url}
          status={ready?.dry_run ? 'Dry run' : 'Live'}
        />

        {can('automations.create') && (
          <button
            className="cta full"
            onClick={() => setStarting(true)}
            style={{ marginBottom: 18 }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
              <PlusIcon />
              New automation
            </span>
          </button>
        )}

        <nav className="nav">
          {visible(TOP).map(({ to, label, icon, end }) => (
            <NavLink key={to} to={to} end={end}>
              <span className="nav-tile">{icon}</span>
              {label}
            </NavLink>
          ))}

          <NavGroup label="My content" icon={<PostsIcon />} items={visible(MY_CONTENT)} />
          <NavGroup label="Analytics" icon={<ActivityIcon />} items={visible(ANALYTICS)} />

          {visible(BOTTOM).map(({ to, label, icon }) => (
            <NavLink key={to} to={to}>
              <span className="nav-tile">{icon}</span>
              {label}
            </NavLink>
          ))}

          {/* Derived, not declared: the group exists exactly when a child does,
              so a role with neither permission sees the sidebar it always saw. */}
          <NavGroup label="Administration" icon={<ShieldIcon />} items={visible(ADMIN)} />
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
