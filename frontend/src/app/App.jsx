import { useCallback, useEffect, useState } from 'react'
import { Routes, Route, useLocation } from 'react-router-dom'
import Sidebar from '../components/layout/Sidebar.jsx'
import Topbar from '../components/layout/Topbar.jsx'
import Overview from '../pages/Overview.jsx'
import Agents from '../pages/Agents.jsx'
import ActivityMonitor from '../pages/ActivityMonitor.jsx'
import SecurityEvents from '../pages/SecurityEvents.jsx'
import Reviews from '../pages/Reviews.jsx'
import Scenarios from '../pages/Scenarios.jsx'
import LiveAgentDemo from '../pages/LiveAgentDemo.jsx'
import Settings from '../pages/Settings.jsx'
import { listPendingReviews } from '../services/reviewService.js'
import { useConnection } from './ConnectionContext.jsx'

const TITLES = {
  '/': 'Overview',
  '/agents': 'Agent Management',
  '/activity': 'Activity Monitor',
  '/events': 'Security Events',
  '/reviews': 'Human Reviews',
  '/scenarios': 'Scenarios',
  '/live-agent': 'Live Agent Demo',
  '/settings': 'Settings',
}

export default function App() {
  const location = useLocation()
  const { health, ws } = useConnection()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [pendingReviews, setPendingReviews] = useState(0)

  const refreshPending = useCallback(() => {
    listPendingReviews()
      .then((r) => setPendingReviews(r.total ?? (r.items || []).length))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (health.state === 'ok') refreshPending()
  }, [health.state, location.pathname, refreshPending])

  // Nudge the pending badge when a review-related live event arrives.
  useEffect(() => {
    return ws.subscribe((msg) => {
      if (msg.type === 'security_event' && (msg.data?.review_id || msg.data?.decision === 'REVIEW')) {
        refreshPending()
      }
    })
  }, [ws, refreshPending])

  const title = TITLES[location.pathname] || 'D3COY Security Monitor'

  return (
    <div className="app-shell">
      <Sidebar open={sidebarOpen} pendingReviews={pendingReviews} />
      <div className="main">
        <Topbar title={title} onToggleSidebar={() => setSidebarOpen((o) => !o)} />
        <div className="content">
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/activity" element={<ActivityMonitor />} />
            <Route path="/events" element={<SecurityEvents />} />
            <Route path="/reviews" element={<Reviews onCountChange={setPendingReviews} />} />
            <Route path="/scenarios" element={<Scenarios />} />
            <Route path="/live-agent" element={<LiveAgentDemo />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<Overview />} />
          </Routes>
        </div>
      </div>
    </div>
  )
}
