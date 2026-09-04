import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { RequireAuth } from '../components/common/RequireAuth'
import { Layout } from '../components/layout/Layout'
import { Analytics } from '../pages/Analytics'
import { Automations } from '../pages/Automations'
import { Contacts } from '../pages/Contacts'
import { Dashboard } from '../pages/Dashboard'
import { Insights } from '../pages/Insights'
import { Instagram } from '../pages/Instagram'
import { Posts } from '../pages/Posts'
import { Settings } from '../pages/Settings'
import { Stories } from '../pages/Stories'
import { Templates } from '../pages/Templates'

export function Router() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          element={
            <RequireAuth>
              <Layout />
            </RequireAuth>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="automations" element={<Automations />} />
          <Route path="templates" element={<Templates />} />
          <Route path="posts" element={<Posts />} />
          <Route path="stories" element={<Stories />} />
          <Route path="analytics" element={<Analytics />} />
          {/* The activity log became a tab; keep old links and bookmarks working. */}
          <Route
            path="activity"
            element={<Navigate to="/analytics?tab=activity" replace />}
          />
          <Route path="insights" element={<Insights />} />
          <Route path="contacts" element={<Contacts />} />
          <Route path="instagram" element={<Instagram />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
