import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Bot, Activity, ShieldAlert, ClipboardCheck, FlaskConical, Terminal, Settings,
} from 'lucide-react'
import { useConnection } from '../../app/ConnectionContext.jsx'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/activity', label: 'Activity Monitor', icon: Activity },
  { to: '/events', label: 'Security Events', icon: ShieldAlert },
  { to: '/reviews', label: 'Human Reviews', icon: ClipboardCheck, badge: 'pendingReviews' },
  { to: '/scenarios', label: 'Scenarios', icon: FlaskConical },
  { to: '/live-agent', label: 'Live Agent Demo', icon: Terminal },
  { to: '/settings', label: 'Settings', icon: Settings },
]

export default function Sidebar({ open, pendingReviews }) {
  const { systemStatus } = useConnection()
  const counts = { pendingReviews }
  return (
    <aside className={`sidebar ${open ? 'open' : ''}`}>
      <div className="sidebar-brand">
        <div className="logo">D3</div>
        <div>
          <h1>D3COY</h1>
          <span>Security Monitor</span>
        </div>
      </div>
      <nav className="nav">
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
          >
            <item.icon size={17} />
            {item.label}
            {item.badge && counts[item.badge] > 0 && (
              <span className="badge-count">{counts[item.badge]}</span>
            )}
          </NavLink>
        ))}
      </nav>
      <div className="sidebar-footer">
        {systemStatus?.app ? `${systemStatus.app} · ` : ''}prototype
        <div className="muted">AI-agent drift detection &amp; prevention</div>
      </div>
    </aside>
  )
}
