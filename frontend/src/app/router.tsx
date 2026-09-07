import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { basename } from './workspace'
import { RequireAuth } from '../components/common/RequireAuth'
import { WorkspaceGate } from '../components/common/WorkspaceGate'
import { NoAccess, RequirePermission } from '../components/common/RequirePermission'
import { Layout } from '../components/layout/Layout'
import { Analytics } from '../pages/Analytics'
import { CloneAutomation } from '../pages/CloneAutomation'
import { Automations } from '../pages/Automations'
import { AutoDmPause } from '../pages/AutoDmPause'
import { Contacts } from '../pages/Contacts'
import { Dashboard } from '../pages/Dashboard'
import { Insights } from '../pages/Insights'
import { Invite } from '../pages/Invite'
import { Invites } from '../pages/Invites'
import { GetStarted } from '../pages/GetStarted'
import { Instagram } from '../pages/Instagram'
import { Posts } from '../pages/Posts'
import { Settings } from '../pages/Settings'
import { Stories } from '../pages/Stories'
import { Upcoming } from '../pages/Upcoming'
import { Templates } from '../pages/Templates'
import { RoleEditor } from '../pages/RoleEditor'
import { Roles } from '../pages/Roles'
import { Users } from '../pages/Users'

/**
 * `basename` is the `/u/<slug>` prefix, or '' on the unprefixed layout that
 * pre-workspace bookmarks and the already-live Meta app still use. Vite emits
 * absolute asset paths, so serving the same build at any depth needs no
 * build-time change -- only this and the SPA fallback nginx already does.
 */
export function Router() {
  return (
    <BrowserRouter basename={basename}>
      <Routes>
        {/* Public, and declared outside the authenticated parent on purpose.
            The page has a real "signed in as somebody else" state, so it can
            never sit behind an auth gate -- and putting the branch inside
            `RequireAuth` would make an auth gate read the URL while leaving the
            catch-all below still claiming `/invite` in the route table.
            React Router scores a static segment above a splat, so this wins. */}
        <Route path="invite" element={<Invite />} />
        <Route
          element={
            <RequireAuth>
              <WorkspaceGate>
                <Layout />
              </WorkspaceGate>
            </RequireAuth>
          }
        >
          <Route index element={<Dashboard />} />
          {/* Ungated, like `index`: this is where a freshly invited person
              lands the moment they choose a password, so it must never refuse
              them. Steps needing `instagram.manage` degrade in place. */}
          <Route path="get-started" element={<GetStarted />} />
          <Route
            path="invites"
            element={
              <RequirePermission perm="users.view">
                <Invites />
              </RequirePermission>
            }
          />
          <Route
            path="automations"
            element={
              <RequirePermission perm="automations.view">
                <Automations />
              </RequirePermission>
            }
          />
          <Route
            path="templates"
            element={
              <RequirePermission perm="automations.view">
                <Templates />
              </RequirePermission>
            }
          />
          <Route
            path="posts"
            element={
              <RequirePermission perm="content.view">
                <Posts />
              </RequirePermission>
            }
          />
          <Route
            path="stories"
            element={
              <RequirePermission perm="content.view">
                <Stories />
              </RequirePermission>
            }
          />
          <Route
            path="upcoming"
            element={
              <RequirePermission perm="automations.view">
                <Upcoming />
              </RequirePermission>
            }
          />
          <Route
            path="analytics"
            element={
              <RequirePermission perm="analytics.view">
                <Analytics />
              </RequirePermission>
            }
          />
          {/* The activity log became a tab; keep old links and bookmarks working. */}
          <Route
            path="activity"
            element={<Navigate to="/analytics?tab=activity" replace />}
          />
          <Route
            path="insights"
            element={
              <RequirePermission perm="analytics.view">
                <Insights />
              </RequirePermission>
            }
          />
          <Route
            path="auto-dm-pause"
            element={
              <RequirePermission perm="instagram.manage">
                <AutoDmPause />
              </RequirePermission>
            }
          />
          <Route
            path="contacts"
            element={
              <RequirePermission perm="contacts.view">
                <Contacts />
              </RequirePermission>
            }
          />
          <Route
            path="instagram"
            element={
              <RequirePermission perm="instagram.view">
                <Instagram />
              </RequirePermission>
            }
          />
          <Route
            path="settings"
            element={
              <RequirePermission perm="settings.view">
                <Settings />
              </RequirePermission>
            }
          />
          <Route
            path="users"
            element={
              <RequirePermission perm="users.view">
                <Users />
              </RequirePermission>
            }
          />
          <Route
            path="roles"
            element={
              <RequirePermission perm="roles.view">
                <Roles />
              </RequirePermission>
            }
          />
          <Route
            path="roles/new"
            element={
              <RequirePermission perm="roles.manage">
                <RoleEditor />
              </RequirePermission>
            }
          />
          <Route
            path="roles/:id"
            element={
              <RequirePermission perm="roles.view">
                <RoleEditor />
              </RequirePermission>
            }
          />
          <Route
            path="posts/:postId/clone"
            element={
              <RequirePermission perm="automations.create">
                <CloneAutomation />
              </RequirePermission>
            }
          />
          {/* No catch-all existed before. A role-gated app produces more wrong
              URLs than an open one, because people share links to pages that
              others cannot reach. */}
          <Route path="*" element={<NoAccess what="that page - it does not exist" />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
