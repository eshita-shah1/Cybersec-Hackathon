import { useCallback, useEffect, useState } from 'react'
import { ShieldAlert, RefreshCw } from 'lucide-react'
import { Card, CardHead } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import Drawer from '../components/common/Drawer.jsx'
import ConfirmDialog from '../components/common/ConfirmDialog.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import EventDetail from '../components/activity/EventDetail.jsx'
import { listAlerts, reviewAlert } from '../services/reviewService.js'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useToast } from '../app/ToastContext.jsx'
import { useLiveEvents } from '../hooks/useLiveEvents.js'
import { formatSeverity, formatDecision } from '../utils/statusFormatter.js'
import { formatRelative, titleCase } from '../utils/formatters.js'

const TYPE_TONE = {
  POLICY_VIOLATION: 'danger', HIGH_RISK: 'danger', ESCALATION: 'danger', UNKNOWN_AGENT: 'danger',
  REVIEW_REQUIRED: 'warn', ANOMALY_MONITOR: 'info',
}

export default function SecurityEvents() {
  const { canAdmin: admin } = useConnection()
  const toast = useToast()
  const [alerts, setAlerts] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filters, setFilters] = useState({ status: '', severity: '', alert_type: '' })
  const [selected, setSelected] = useState(null)
  const [eventId, setEventId] = useState(null)
  const [triage, setTriage] = useState(null)
  const [busy, setBusy] = useState(false)
  const [newCount, setNewCount] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
      const page = await listAlerts({ ...clean, limit: 100 })
      setAlerts(page.items || [])
      setTotal(page.total || 0)
      setNewCount(0)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { load() }, [load])
  useLiveEvents({ limit: 1, onMessage: (m) => {
    if (m.type === 'security_alert' || m.type === 'alert_update') setNewCount((c) => c + 1)
    if (m.type === 'security_event' && m.data?.alert_ids?.length) setNewCount((c) => c + 1)
  } })

  const doTriage = async (notes) => {
    setBusy(true)
    try {
      await reviewAlert(triage.alert.alert_id, triage.status, 'analyst', notes || null)
      toast.success('Alert updated', `Marked ${triage.status}.`)
      setTriage(null)
      await load()
    } catch (e) {
      toast.error('Could not update alert', e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="filters">
        <div className="field"><label>Status</label>
          <select className="input" value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}>
            <option value="">Any</option><option value="open">Open</option><option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option><option value="false_positive">False positive</option>
          </select></div>
        <div className="field"><label>Severity</label>
          <select className="input" value={filters.severity} onChange={(e) => setFilters((f) => ({ ...f, severity: e.target.value }))}>
            <option value="">Any</option><option value="critical">Critical</option><option value="high">High</option>
            <option value="medium">Medium</option><option value="low">Low</option>
          </select></div>
        <button className="btn" onClick={load}><RefreshCw size={14} /> Refresh</button>
        {newCount > 0 && <button className="btn sm primary" onClick={load}>{newCount} new · refresh</button>}
      </div>

      <Card>
        <CardHead title="Security alerts" sub="Backend-generated from recorded events (/api/alerts)" />
        {loading ? <Loading /> : error ? <ErrorState message={error.message} onRetry={load} disconnected={error.isNetwork} />
          : alerts.length === 0 ? <EmptyState title="No alerts" message="No security alerts have been recorded." icon={ShieldAlert} />
          : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Severity</th><th>Type</th><th>Title</th><th>Agent</th><th>Status</th><th>When</th></tr></thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr key={a.alert_id} className="clickable" onClick={() => setSelected(a)}>
                      <td><Badge tone={formatSeverity(a.severity).tone}>{formatSeverity(a.severity).label}</Badge></td>
                      <td><Badge tone={TYPE_TONE[a.alert_type] || 'neutral'}>{titleCase(a.alert_type)}</Badge></td>
                      <td>{a.title}</td>
                      <td className="nowrap">{a.agent_id}</td>
                      <td><Badge tone={a.status === 'open' ? 'warn' : a.status === 'resolved' ? 'ok' : 'neutral'}>{titleCase(a.status)}</Badge></td>
                      <td className="muted nowrap">{formatRelative(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      {selected && (
        <Drawer title="Alert detail" onClose={() => setSelected(null)}
          right={<Badge tone={formatSeverity(selected.severity).tone}>{formatSeverity(selected.severity).label}</Badge>}>
          <dl className="kv mb16">
            <dt>Alert ID</dt><dd className="mono">{selected.alert_id}</dd>
            <dt>Type</dt><dd><Badge tone={TYPE_TONE[selected.alert_type] || 'neutral'}>{titleCase(selected.alert_type)}</Badge></dd>
            <dt>Title</dt><dd>{selected.title}</dd>
            <dt>Agent</dt><dd>{selected.agent_id}</dd>
            <dt>Status</dt><dd>{titleCase(selected.status)}</dd>
            <dt>Created</dt><dd>{formatRelative(selected.created_at)}</dd>
            {selected.reviewed_by && (<><dt>Reviewed by</dt><dd>{selected.reviewed_by}</dd></>)}
          </dl>
          <div className="evidence">
            <h5>Description</h5>
            <div className="reason">{selected.description}</div>
            <div className="muted mt8" style={{ fontSize: 12 }}>
              A flagged event is evidence of a deviation, not a confirmed attack.
            </div>
          </div>
          {selected.event_id && (
            <button className="btn sm mb16" onClick={() => { setEventId(selected.event_id); }}>
              View linked event evidence
            </button>
          )}
          {admin ? (
            <div className="flex gap8 wrap">
              {['acknowledged', 'resolved', 'false_positive', 'escalated'].map((s) => (
                <button key={s} className="btn sm" onClick={() => setTriage({ alert: selected, status: s })}>
                  {titleCase(s)}
                </button>
              ))}
            </div>
          ) : <div className="muted" style={{ fontSize: 12 }}>Admin authorization required to triage alerts.</div>}
        </Drawer>
      )}

      {eventId && <EventDetail eventId={eventId} onClose={() => setEventId(null)} />}

      {triage && (
        <ConfirmDialog
          title={`Mark alert: ${titleCase(triage.status)}`}
          message="This records an analyst triage decision on the alert. It does not change any enforcement outcome."
          confirmLabel="Submit"
          busy={busy}
          onConfirm={doTriage}
          onCancel={() => !busy && setTriage(null)}
        />
      )}
    </>
  )
}
