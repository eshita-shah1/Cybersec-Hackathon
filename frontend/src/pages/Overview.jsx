import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bot, Activity, ShieldOff, ClipboardCheck, CheckCircle2, Cpu, Radio,
} from 'lucide-react'
import {
  PieChart, Pie, Cell, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { Card, CardHead, MetricCard } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import { getOverview } from '../services/analyticsService.js'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useLiveEvents } from '../hooks/useLiveEvents.js'
import { formatDecision, formatRiskLevel } from '../utils/statusFormatter.js'
import { formatRelative, roundScore } from '../utils/formatters.js'

const TONE_COLORS = {
  ALLOW: '#34d399', MONITOR: '#38bdf8', REVIEW: '#fbbf24', BLOCK: '#f87171',
  LOW: '#34d399', MEDIUM: '#38bdf8', HIGH: '#fbbf24', CRITICAL: '#f87171',
}

export default function Overview() {
  const { health, systemStatus, wsStatus } = useConnection()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const navigate = useNavigate()
  const { events: liveEvents, lastAt } = useLiveEvents({ limit: 8 })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setData(await getOverview())
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // Refresh stats shortly after a live event arrives (debounced by lastAt change).
  useEffect(() => {
    if (!lastAt) return
    const t = setTimeout(load, 800)
    return () => clearTimeout(t)
  }, [lastAt, load])

  if (health.state === 'down') {
    return (
      <ErrorState
        disconnected
        title="Unable to reach the D3COY gateway"
        message="The dashboard is disconnected. Start the backend gateway, then retry."
        onRetry={load}
      />
    )
  }
  if (loading && !data) return <Loading label="Loading dashboard…" />
  if (error && !data) return <ErrorState message={error.message} onRetry={load} />
  if (!data) return null

  const decDist = Object.entries(data.decision_distribution || {}).map(([k, v]) => ({
    name: k, value: v, color: TONE_COLORS[k] || '#6b7483',
  }))
  const riskDist = Object.entries(data.risk_level_distribution || {}).map(([k, v]) => ({
    name: k, value: v, color: TONE_COLORS[k] || '#6b7483',
  }))
  const detectors = systemStatus?.detectors || {}

  return (
    <>
      <div className="grid grid-metrics section-gap">
        <MetricCard label="Registered agents" value={data.agents?.total ?? '—'} icon={Bot}
          trend={`${data.agents?.active ?? 0} active · ${data.agents?.suspended ?? 0} suspended`} />
        <MetricCard label="Total events" value={data.events?.total ?? '—'} icon={Activity}
          trend={`${data.events?.last_hour ?? 0} in the last hour`} />
        <MetricCard label="Blocked actions" value={data.events?.blocked ?? '—'} icon={ShieldOff}
          valueClass={data.events?.blocked ? '' : ''}
          trend={`${data.events?.flagged ?? 0} flagged total`} />
        <MetricCard label="Pending reviews" value={data.pending_reviews ?? '—'} icon={ClipboardCheck}
          trend={data.pending_reviews ? 'awaiting a decision' : 'none waiting'} />
        <MetricCard label="Executed" value={data.events?.executed ?? '—'} icon={CheckCircle2}
          trend={`${data.events?.failed ?? 0} failed`} />
        <MetricCard label="Avg risk score" value={data.events?.average_risk_score ?? '—'} icon={Activity}
          valueClass="sm" trend="across all events" />
      </div>

      <div className="grid grid-2 section-gap">
        <Card>
          <CardHead title="Decision distribution" sub="Backend-recorded outcomes" />
          <div className="card-pad">
            {decDist.length === 0 ? (
              <EmptyState title="No events yet" message="Run a scenario or the live agent to populate this." />
            ) : (
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie data={decDist} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} innerRadius={55}>
                    {decDist.map((d) => <Cell key={d.name} fill={d.color} />)}
                  </Pie>
                  <Tooltip contentStyle={tooltipStyle} />
                </PieChart>
              </ResponsiveContainer>
            )}
            <div className="flex gap8 wrap mt8">
              {decDist.map((d) => (
                <Badge key={d.name} tone={formatDecision(d.name).tone}>{formatDecision(d.name).label}: {d.value}</Badge>
              ))}
            </div>
          </div>
        </Card>

        <Card>
          <CardHead title="Risk levels" sub="Classification of recorded events" />
          <div className="card-pad">
            {riskDist.length === 0 ? (
              <EmptyState title="No events yet" />
            ) : (
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={riskDist}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#232935" />
                  <XAxis dataKey="name" tick={{ fill: '#9aa4b2', fontSize: 12 }} />
                  <YAxis allowDecimals={false} tick={{ fill: '#9aa4b2', fontSize: 12 }} />
                  <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                  <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                    {riskDist.map((d) => <Cell key={d.name} fill={d.color} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <div className="grid grid-2">
        <Card>
          <CardHead title="Detector status" sub="From /api/system/status" right={<Cpu size={16} className="muted" />} />
          <div className="card-pad">
            {systemStatus ? (
              <dl className="kv">
                {Object.entries(detectors).map(([name, info]) => (
                  <div key={name} style={{ display: 'contents' }}>
                    <dt style={{ textTransform: 'capitalize' }}>{name.replace(/_/g, ' ')}</dt>
                    <dd>
                      <Badge tone={info.status === 'ready' ? 'ok' : info.status === 'warming_up' ? 'warn' : info.status === 'fallback' ? 'warn' : 'neutral'}>
                        {info.status}
                      </Badge>
                      {info.model && <span className="muted"> · {info.model}</span>}
                    </dd>
                  </div>
                ))}
                <dt>Database</dt>
                <dd><Badge tone={systemStatus.database?.status === 'ok' ? 'ok' : 'danger'}>{systemStatus.database?.status}</Badge> <span className="muted">{systemStatus.database?.backend}</span></dd>
                <dt>Executor</dt>
                <dd><Badge tone={systemStatus.executor?.health ? 'ok' : 'danger'}>{systemStatus.executor?.health ? 'reachable' : 'unreachable'}</Badge></dd>
              </dl>
            ) : (
              <div className="muted">Detector status unavailable.</div>
            )}
          </div>
        </Card>

        <Card>
          <CardHead title="Recent activity" sub={lastAt ? `live · updated ${formatRelative(lastAt)}` : 'most recent recorded events'}
            right={<span className="conn-pill"><span className={`dot ${wsStatus === 'connected' ? 'ok' : 'warn'}`} /><Radio size={12} /> {wsStatus}</span>} />
          <div className="table-wrap">
            {(() => {
              const rows = liveEvents.length > 0 ? liveEvents.map((e) => ({
                id: e.eventId, agent: e.agentId, tool: e.toolName, decision: e.decision, risk: e.riskScore, at: e.receivedAt,
              })) : (data.recent_events || []).map((e) => ({
                id: e.event_id, agent: e.agent_id, tool: e.tool_name, decision: e.decision, risk: e.risk_score, at: e.received_at,
              }))
              if (rows.length === 0) return <div className="card-pad"><EmptyState title="No events recorded" /></div>
              return (
                <table className="data">
                  <thead><tr><th>Agent</th><th>Tool</th><th>Decision</th><th>Risk</th><th>When</th></tr></thead>
                  <tbody>
                    {rows.slice(0, 8).map((r) => (
                      <tr key={r.id} className="clickable" onClick={() => navigate(`/activity?event=${r.id}`)}>
                        <td className="nowrap">{r.agent}</td>
                        <td className="mono">{r.tool}</td>
                        <td><Badge tone={formatDecision(r.decision).tone}>{formatDecision(r.decision).label}</Badge></td>
                        <td>{typeof r.risk === 'number' ? roundScore(r.risk) : '—'}</td>
                        <td className="muted nowrap">{formatRelative(r.at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            })()}
          </div>
        </Card>
      </div>
    </>
  )
}

const tooltipStyle = { background: '#141821', border: '1px solid #2e3542', borderRadius: 8, color: '#e8ecf2', fontSize: 12 }
