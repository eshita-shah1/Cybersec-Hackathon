import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { RefreshCw, ChevronLeft, ChevronRight } from 'lucide-react'
import { Card } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import EventDetail from '../components/activity/EventDetail.jsx'
import { listEvents } from '../services/eventService.js'
import { normalizeEvent } from '../utils/eventNormalizer.js'
import { useLiveEvents } from '../hooks/useLiveEvents.js'
import { formatDecision, formatExecution, formatRiskLevel, riskScoreTone } from '../utils/statusFormatter.js'
import { formatRelative, roundScore, shortId } from '../utils/formatters.js'

const PAGE = 25

export default function ActivityMonitor() {
  const [params, setParams] = useSearchParams()
  const [rows, setRows] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [newCount, setNewCount] = useState(0)
  const [filters, setFilters] = useState({ agent_id: '', decision: '', risk_level: '', execution_status: '' })
  const selectedId = params.get('event')

  const load = useCallback(async (nextOffset = 0) => {
    setLoading(true)
    setError(null)
    try {
      const clean = Object.fromEntries(Object.entries(filters).filter(([, v]) => v))
      const page = await listEvents({ ...clean, limit: PAGE, offset: nextOffset })
      setRows((page.items || []).map(normalizeEvent))
      setTotal(page.total || 0)
      setOffset(nextOffset)
      setNewCount(0)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [filters])

  useEffect(() => { load(0) }, [load])

  // Count live events while viewing page 1 so the operator knows to refresh (no aggressive refetch).
  useLiveEvents({
    limit: 1,
    onMessage: (msg) => {
      if (msg.type === 'security_event' && offset === 0) setNewCount((c) => c + 1)
    },
  })

  const setFilter = (k, v) => setFilters((f) => ({ ...f, [k]: v }))
  const openEvent = (id) => setParams((p) => { p.set('event', id); return p }, { replace: true })
  const closeEvent = () => setParams((p) => { p.delete('event'); return p }, { replace: true })
  const selectedRow = rows.find((r) => r.eventId === selectedId)

  return (
    <>
      <div className="filters">
        <div className="field"><label>Agent ID</label>
          <input className="input" value={filters.agent_id} onChange={(e) => setFilter('agent_id', e.target.value)} placeholder="any" /></div>
        <div className="field"><label>Decision</label>
          <select className="input" value={filters.decision} onChange={(e) => setFilter('decision', e.target.value)}>
            <option value="">Any</option><option>ALLOW</option><option>MONITOR</option><option>REVIEW</option><option>BLOCK</option>
          </select></div>
        <div className="field"><label>Risk level</label>
          <select className="input" value={filters.risk_level} onChange={(e) => setFilter('risk_level', e.target.value)}>
            <option value="">Any</option><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option>
          </select></div>
        <div className="field"><label>Execution</label>
          <select className="input" value={filters.execution_status} onChange={(e) => setFilter('execution_status', e.target.value)}>
            <option value="">Any</option><option>SUCCEEDED</option><option>NOT_EXECUTED</option><option>AWAITING_REVIEW</option><option>FAILED</option>
          </select></div>
        <button className="btn" onClick={() => load(0)}><RefreshCw size={14} /> Apply</button>
      </div>

      {newCount > 0 && offset === 0 && (
        <button className="btn sm primary mb16" onClick={() => load(0)}>
          {newCount} new event{newCount > 1 ? 's' : ''} · refresh
        </button>
      )}

      <Card>
        {loading ? <Loading /> : error ? <ErrorState message={error.message} onRetry={() => load(0)} disconnected={error.isNetwork} />
          : rows.length === 0 ? <EmptyState title="No events recorded" message="No events match these filters." />
          : (
            <>
              <div className="table-wrap">
                <table className="data">
                  <thead><tr>
                    <th>Event</th><th>Agent</th><th>Tool · op</th><th>Resource</th>
                    <th>Risk</th><th>Decision</th><th>Execution</th><th>When</th>
                  </tr></thead>
                  <tbody>
                    {rows.map((e) => (
                      <tr key={e.eventId} className={`clickable ${e.eventId === selectedId ? 'selected' : ''}`} onClick={() => openEvent(e.eventId)}>
                        <td className="mono">{shortId(e.eventId, 12)}</td>
                        <td className="nowrap">{e.agentId}</td>
                        <td className="mono nowrap">{e.toolName} · {e.action}</td>
                        <td className="mono dim">{e.targetResource}</td>
                        <td>
                          {typeof e.riskScore === 'number'
                            ? <Badge tone={riskScoreTone(e.riskScore)}>{roundScore(e.riskScore)}</Badge>
                            : <span className="muted">—</span>}
                        </td>
                        <td><Badge tone={formatDecision(e.decision).tone}>{formatDecision(e.decision).label}</Badge></td>
                        <td><Badge tone={formatExecution(e.executionStatus).tone}>{formatExecution(e.executionStatus).label}</Badge></td>
                        <td className="muted nowrap">{formatRelative(e.receivedAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="flex between center card-pad">
                <span className="muted">
                  Showing {offset + 1}–{Math.min(offset + PAGE, total)} of {total} · server-side pagination
                </span>
                <div className="flex gap8">
                  <button className="btn sm" disabled={offset === 0} onClick={() => load(Math.max(0, offset - PAGE))}>
                    <ChevronLeft size={14} /> Prev
                  </button>
                  <button className="btn sm" disabled={offset + PAGE >= total} onClick={() => load(offset + PAGE)}>
                    Next <ChevronRight size={14} />
                  </button>
                </div>
              </div>
            </>
          )}
      </Card>

      {selectedId && <EventDetail eventId={selectedId} fallback={selectedRow} onClose={closeEvent} />}
    </>
  )
}
