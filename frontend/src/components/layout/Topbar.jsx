import { useState } from 'react'
import { Menu, KeyRound, LogOut, Server, Radio, Database } from 'lucide-react'
import { useConnection } from '../../app/ConnectionContext.jsx'
import { clearAdminKey } from '../../services/apiClient.js'
import { useToast } from '../../app/ToastContext.jsx'
import AdminDialog from './AdminDialog.jsx'
import { formatRelative } from '../../utils/formatters.js'

function Pill({ icon: Icon, label, tone, title }) {
  return (
    <span className="conn-pill" title={title}>
      <span className={`dot ${tone}`} />
      <Icon size={13} /> {label}
    </span>
  )
}

export default function Topbar({ title, onToggleSidebar }) {
  const { health, wsStatus, admin, adminRequired, systemStatus } = useConnection()
  const [showAdmin, setShowAdmin] = useState(false)
  const toast = useToast()

  const gatewayTone = health.state === 'ok' ? 'ok' : health.state === 'down' ? 'danger' : 'neutral'
  const wsTone = wsStatus === 'connected' ? 'ok' : wsStatus === 'connecting' ? 'warn' : 'danger'
  const execHealth = systemStatus?.executor?.health
  const execTone = execHealth ? 'ok' : systemStatus ? 'danger' : 'neutral'

  return (
    <header className="topbar">
      <button className="btn ghost sm" onClick={onToggleSidebar} style={{ display: 'none' }} id="burger">
        <Menu size={18} />
      </button>
      <h2>{title}</h2>
      <div className="spacer" />
      <div className="conn-pills">
        <Pill
          icon={Server}
          tone={gatewayTone}
          label={health.state === 'ok' ? 'Gateway' : health.state === 'down' ? 'Gateway down' : 'Gateway…'}
          title={health.lastOk ? `Last OK ${formatRelative(health.lastOk)}` : 'Checking gateway'}
        />
        <Pill
          icon={Database}
          tone={execTone}
          label="Executor"
          title={execHealth ? 'Sandbox executor reachable via gateway' : 'Executor status unknown / unreachable'}
        />
        <Pill
          icon={Radio}
          tone={wsTone}
          label={wsStatus === 'connected' ? 'Live' : wsStatus === 'connecting' ? 'Connecting' : 'Offline'}
          title={`WebSocket ${wsStatus}`}
        />
      </div>
      {!adminRequired ? (
        <span className="conn-pill" title="No admin key is configured on the backend — all actions are open.">
          <span className="dot ok" /> Open mode
        </span>
      ) : admin ? (
        <button
          className="btn sm success"
          onClick={() => {
            clearAdminKey()
            toast.info('Admin session cleared')
          }}
          title="Clear admin session"
        >
          <LogOut size={14} /> Admin
        </button>
      ) : (
        <button className="btn sm" onClick={() => setShowAdmin(true)}>
          <KeyRound size={14} /> Admin login
        </button>
      )}
      {showAdmin && <AdminDialog onClose={() => setShowAdmin(false)} />}
    </header>
  )
}
