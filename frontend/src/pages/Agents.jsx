import { useCallback, useEffect, useState } from 'react'
import { Search, Bot, PauseCircle, PlayCircle, ShieldX } from 'lucide-react'
import { Card } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import Drawer from '../components/common/Drawer.jsx'
import ConfirmDialog from '../components/common/ConfirmDialog.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import { listAgents, suspendAgent, reactivateAgent, deactivateAgent } from '../services/agentService.js'
import { listEvents } from '../services/eventService.js'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useToast } from '../app/ToastContext.jsx'
import { formatAgentStatus, formatDecision } from '../utils/statusFormatter.js'
import { formatTimestamp } from '../utils/formatters.js'

export default function Agents() {
  const { canAdmin: admin } = useConnection()
  const toast = useToast()
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [query, setQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const [action, setAction] = useState(null) // {type, agent}
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setAgents(await listAgents(statusFilter || undefined))
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  useEffect(() => {
    load()
  }, [load])

  const filtered = agents.filter((a) => {
    if (!query) return true
    const q = query.toLowerCase()
    return [a.agent_id, a.agent_name, a.role, a.assigned_task].some((v) => String(v || '').toLowerCase().includes(q))
  })

  const runAction = async (reason) => {
    setBusy(true)
    setActionError(null)
    const { type, agent } = action
    try {
      const fn = type === 'suspend' ? suspendAgent : type === 'reactivate' ? reactivateAgent : deactivateAgent
      const updated = await fn(agent.agent_id, reason)
      toast.success(`Agent ${type}ed`, `${agent.agent_name} is now ${updated.status}.`)
      setAction(null)
      setSelected(updated)
      await load()
    } catch (e) {
      setActionError(e.message)
      if (e.isAuth) toast.error('Not authorized', 'Admin authorization is required. Use Admin login.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="filters">
        <div className="field" style={{ flex: 1, minWidth: 220 }}>
          <label>Search</label>
          <div style={{ position: 'relative' }}>
            <Search size={15} style={{ position: 'absolute', left: 10, top: 10, color: 'var(--text-faint)' }} />
            <input className="input" style={{ paddingLeft: 32 }} placeholder="name, id, role or task…"
              value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
        </div>
        <div className="field">
          <label>Status</label>
          <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="suspended">Suspended</option>
            <option value="deactivated">Deactivated</option>
          </select>
        </div>
      </div>

      <Card>
        {loading ? <Loading /> : error ? <ErrorState message={error.message} onRetry={load} disconnected={error.isNetwork} />
          : filtered.length === 0 ? <EmptyState title="No agents" message="The registry returned no matching agents." icon={Bot} />
          : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Agent</th><th>Role</th><th>Assigned task</th><th>Status</th><th>Tools</th></tr></thead>
                <tbody>
                  {filtered.map((a) => (
                    <tr key={a.agent_id} className="clickable" onClick={() => setSelected(a)}>
                      <td><div>{a.agent_name}</div><div className="mono">{a.agent_id}</div></td>
                      <td className="nowrap">{a.role}</td>
                      <td className="dim">{a.assigned_task}</td>
                      <td><Badge tone={formatAgentStatus(a.status).tone}>{formatAgentStatus(a.status).label}</Badge></td>
                      <td>{(a.allowed_tools || []).length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      {selected && (
        <AgentDetail
          agent={selected}
          admin={admin}
          onClose={() => setSelected(null)}
          onAction={(type) => { setActionError(null); setAction({ type, agent: selected }) }}
        />
      )}

      {action && (
        <ConfirmDialog
          title={`${action.type[0].toUpperCase() + action.type.slice(1)} agent`}
          message={`This submits a real request to ${action.type} "${action.agent.agent_name}". ${action.type === 'deactivate' ? 'Deactivated agents cannot be reactivated.' : ''}`}
          confirmLabel={action.type[0].toUpperCase() + action.type.slice(1)}
          confirmTone={action.type === 'reactivate' ? 'success' : 'danger'}
          withReason
          busy={busy}
          error={actionError}
          onConfirm={runAction}
          onCancel={() => !busy && setAction(null)}
        />
      )}
    </>
  )
}

function AgentDetail({ agent, admin, onClose, onAction }) {
  const [recent, setRecent] = useState(null)
  useEffect(() => {
    const c = new AbortController()
    listEvents({ agent_id: agent.agent_id, limit: 5 }, c.signal)
      .then((p) => setRecent(p.items || []))
      .catch(() => setRecent([]))
    return () => c.abort()
  }, [agent.agent_id])

  const st = formatAgentStatus(agent.status)
  return (
    <Drawer title={agent.agent_name} onClose={onClose}
      right={<Badge tone={st.tone}>{st.label}</Badge>}>
      <dl className="kv mb16">
        <dt>Agent ID</dt><dd className="mono">{agent.agent_id}</dd>
        <dt>Role</dt><dd>{agent.role}</dd>
        <dt>Assigned task</dt><dd>{agent.assigned_task}</dd>
        <dt>Description</dt><dd className="dim">{agent.task_description}</dd>
        <dt>Version</dt><dd>{agent.version}</dd>
        <dt>Updated</dt><dd>{formatTimestamp(agent.updated_at)}</dd>
        {agent.status_reason && (<><dt>Status reason</dt><dd className="dim">{agent.status_reason}</dd></>)}
      </dl>

      <div className="evidence">
        <h5>Authorized tools</h5>
        <div className="flex gap8 wrap">
          {(agent.allowed_tools || []).map((t) => <Badge key={t} tone="info">{t}</Badge>)}
          {(agent.allowed_tools || []).length === 0 && <span className="muted">none</span>}
        </div>
      </div>
      <div className="evidence">
        <h5>Forbidden actions</h5>
        <div className="flex gap8 wrap">
          {(agent.forbidden_actions || []).map((t) => <Badge key={t} tone="danger">{t}</Badge>)}
          {(agent.forbidden_actions || []).length === 0 && <span className="muted">none</span>}
        </div>
      </div>
      <div className="evidence">
        <h5>Resource scope</h5>
        <div className="dim">Allowed: {(agent.allowed_resources || []).join(', ') || '—'}</div>
        <div className="dim">Restricted: {(agent.restricted_resources || []).join(', ') || '—'}</div>
        {(agent.review_required_tools || []).length > 0 && (
          <div className="dim mt8">Requires authorization: {agent.review_required_tools.join(', ')}</div>
        )}
      </div>

      <div className="evidence">
        <h5>Recent activity</h5>
        {recent === null ? <span className="muted">loading…</span>
          : recent.length === 0 ? <span className="muted">no recorded events</span>
          : recent.map((e) => (
            <div className="reason" key={e.event_id}>
              <span className="mono">{e.tool_name}</span> ·{' '}
              <Badge tone={formatDecision(e.decision).tone}>{formatDecision(e.decision).label}</Badge>
              <span className="muted"> · {formatTimestamp(e.received_at)}</span>
            </div>
          ))}
      </div>

      <div className="flex gap8 wrap mt16">
        {agent.status === 'active' && (
          <button className="btn danger" disabled={!admin} onClick={() => onAction('suspend')} title={admin ? '' : 'Admin login required'}>
            <PauseCircle size={15} /> Suspend
          </button>
        )}
        {agent.status === 'suspended' && (
          <button className="btn success" disabled={!admin} onClick={() => onAction('reactivate')}>
            <PlayCircle size={15} /> Reactivate
          </button>
        )}
        {agent.status !== 'deactivated' && (
          <button className="btn" disabled={!admin} onClick={() => onAction('deactivate')}>
            <ShieldX size={15} /> Deactivate
          </button>
        )}
      </div>
      {!admin && <div className="muted mt8" style={{ fontSize: 12 }}>Admin authorization required for lifecycle actions.</div>}
    </Drawer>
  )
}
