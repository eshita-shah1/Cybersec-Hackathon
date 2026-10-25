import { useState } from 'react'
import { RefreshCw, KeyRound, LogOut, Server } from 'lucide-react'
import { Card, CardHead } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import AdminDialog from '../components/layout/AdminDialog.jsx'
import { useConnection } from '../app/ConnectionContext.jsx'
import { API_BASE_URL, deriveWsUrl, clearAdminKey } from '../services/apiClient.js'
import { useToast } from '../app/ToastContext.jsx'
import { formatRelative } from '../utils/formatters.js'

export default function Settings() {
  const { health, systemStatus, wsStatus, admin, adminRequired, refreshHealth } = useConnection()
  const [showAdmin, setShowAdmin] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const toast = useToast()

  const refresh = async () => {
    setRefreshing(true)
    const ok = await refreshHealth()
    setRefreshing(false)
    toast[ok ? 'success' : 'error'](ok ? 'Gateway reachable' : 'Gateway unreachable')
  }

  return (
    <div className="grid grid-2">
      <Card>
        <CardHead title="Connection" sub="Backend gateway and live feed" right={<Server size={16} className="muted" />} />
        <div className="card-pad">
          <dl className="kv">
            <dt>Gateway URL</dt><dd className="mono">{API_BASE_URL}</dd>
            <dt>WebSocket URL</dt><dd className="mono">{deriveWsUrl()}</dd>
            <dt>Gateway status</dt>
            <dd><Badge tone={health.state === 'ok' ? 'ok' : health.state === 'down' ? 'danger' : 'neutral'}>{health.state}</Badge>
              {health.lastOk && <span className="muted"> · last OK {formatRelative(health.lastOk)}</span>}</dd>
            <dt>Live events</dt><dd><Badge tone={wsStatus === 'connected' ? 'ok' : 'warn'}>{wsStatus}</Badge></dd>
            {health.app && (<><dt>Application</dt><dd>{health.app} <span className="muted">({health.env})</span></dd></>)}
          </dl>
          <button className="btn sm mt16" onClick={refresh} disabled={refreshing}>
            {refreshing ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <RefreshCw size={14} />} Refresh health
          </button>
        </div>
      </Card>

      <Card>
        <CardHead title="Administrator session" sub="Local-demo authorization" right={<KeyRound size={16} className="muted" />} />
        <div className="card-pad">
          <p className="dim mb16">
            {adminRequired
              ? <>Review decisions, agent lifecycle changes and scenario runs require the backend
                  <span className="mono"> ADMIN_API_KEY</span>. It is held in memory for this tab only,
                  sent as <span className="mono">X-API-Key</span>, and never stored or displayed.</>
              : <>This backend runs in <strong>open mode</strong> — no <span className="mono">ADMIN_API_KEY</span>
                  is configured, so all actions are available without authorization. Set
                  <span className="mono"> ADMIN_API_KEY</span> on the backend to require a key.</>}
          </p>
          <dl className="kv mb16">
            <dt>Mode</dt><dd><Badge tone={adminRequired ? 'info' : 'ok'}>{adminRequired ? 'Admin key required' : 'Open (no key required)'}</Badge></dd>
            <dt>Status</dt><dd><Badge tone={admin || !adminRequired ? 'ok' : 'neutral'}>{admin ? 'Authorized' : !adminRequired ? 'Open access' : 'Not authorized'}</Badge></dd>
            {systemStatus?.security && (
              <>
                <dt>Admin key required</dt>
                <dd>{systemStatus.security.admin_key_configured ? 'configured on backend' : 'not configured'}</dd>
                <dt>Agent auth</dt>
                <dd>{systemStatus.security.agent_auth_required ? 'required' : 'disabled'}</dd>
              </>
            )}
          </dl>
          {!adminRequired ? (
            <div className="muted" style={{ fontSize: 12 }}>No admin login needed in open mode.</div>
          ) : admin ? (
            <button className="btn sm danger" onClick={() => { clearAdminKey(); toast.info('Admin session cleared') }}>
              <LogOut size={14} /> Clear admin session
            </button>
          ) : (
            <button className="btn sm primary" onClick={() => setShowAdmin(true)}>
              <KeyRound size={14} /> Enter admin key
            </button>
          )}
        </div>
      </Card>

      {systemStatus?.risk && (
        <Card>
          <CardHead title="Risk configuration" sub="Authoritative — from the backend (read-only)" />
          <div className="card-pad">
            <dl className="kv">
              <dt>Weights</dt>
              <dd className="mono">{Object.entries(systemStatus.risk.weights || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</dd>
              <dt>Thresholds</dt>
              <dd className="mono">{Object.entries(systemStatus.risk.thresholds || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</dd>
              <dt>Fail-closed</dt>
              <dd>{String(systemStatus.risk.fail_closed_on_unavailable_detectors)}</dd>
            </dl>
            <div className="muted mt8" style={{ fontSize: 12 }}>
              The dashboard never recomputes risk; it displays the backend's values.
            </div>
          </div>
        </Card>
      )}

      {showAdmin && <AdminDialog onClose={() => setShowAdmin(false)} />}
    </div>
  )
}
