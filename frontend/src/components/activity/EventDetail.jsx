import { useEffect, useState } from 'react'
import Drawer from '../common/Drawer.jsx'
import Badge from '../common/Badge.jsx'
import { Loading, ErrorState } from '../common/States.jsx'
import { getEvent } from '../../services/eventService.js'
import { normalizeEvent } from '../../utils/eventNormalizer.js'
import {
  formatDecision, formatExecution, formatRiskLevel, formatRequestStatus, riskScoreTone,
} from '../../utils/statusFormatter.js'
import { formatTimestamp, roundScore } from '../../utils/formatters.js'

function Evidence({ title, children }) {
  return (
    <div className="evidence">
      <h5>{title}</h5>
      {children}
    </div>
  )
}

function SignalRow({ label, result }) {
  if (!result) return (
    <div className="reason dim">{label}: <span className="muted">not available</span></div>
  )
  const score = typeof result.score === 'number' ? ` · score ${roundScore(result.score)}` : ''
  const status = result.status ? ` (${result.status})` : ''
  return (
    <div className="reason">
      <strong>{label}</strong>{status}{score}
      {result.explanation && <div className="dim">{result.explanation}</div>}
    </div>
  )
}

export default function EventDetail({ eventId, fallback, onClose }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    getEvent(eventId, controller.signal)
      .then((d) => active && (setDetail(normalizeEvent(d)), setLoading(false)))
      .catch((e) => {
        if (!active || e.code === 'aborted') return
        setError(e)
        setLoading(false)
      })
    return () => {
      active = false
      controller.abort()
    }
  }, [eventId])

  // Use freshest data we have: full detail if loaded, else the row we were given.
  const ev = detail || fallback
  const dec = formatDecision(ev?.decision)
  const exe = formatExecution(ev?.executionStatus)

  return (
    <Drawer title="Event detail" onClose={onClose}>
      {loading && !fallback ? (
        <Loading label="Loading event evidence…" />
      ) : error && !ev ? (
        <ErrorState message={error.message} />
      ) : !ev ? (
        <ErrorState message="Event not found." />
      ) : (
        <>
          <div className="flex gap8 wrap mb16">
            <Badge tone={dec.tone}>Decision: {dec.label}</Badge>
            <Badge tone={exe.tone}>Execution: {exe.label}</Badge>
            {ev.riskLevel && <Badge tone={formatRiskLevel(ev.riskLevel).tone}>{formatRiskLevel(ev.riskLevel).label} risk</Badge>}
            {typeof ev.riskScore === 'number' && (
              <Badge tone={riskScoreTone(ev.riskScore)}>Score {roundScore(ev.riskScore)}</Badge>
            )}
            {ev.duplicate && <Badge tone="neutral">Duplicate submission</Badge>}
          </div>

          <dl className="kv mb16">
            <dt>Event ID</dt><dd className="mono">{ev.eventId}</dd>
            <dt>Agent</dt><dd>{ev.agentId}</dd>
            {ev.taskId && (<><dt>Task</dt><dd>{ev.taskId}</dd></>)}
            <dt>Received</dt><dd>{formatTimestamp(ev.receivedAt)}</dd>
            <dt>Tool / operation</dt><dd className="mono">{ev.toolName} · {ev.action}</dd>
            <dt>Resource</dt><dd className="mono">{ev.targetResource}</dd>
            {ev.sensitivity && (<><dt>Sensitivity</dt><dd>{ev.sensitivity}</dd></>)}
            {ev.raw?.destination && (<><dt>Destination</dt><dd className="mono">{ev.raw.destination}</dd></>)}
            <dt>Request status</dt><dd>{formatRequestStatus(ev.requestStatus).label}</dd>
          </dl>

          {ev.description && (
            <Evidence title="Agent's stated action">
              <div className="reason">{ev.description}</div>
            </Evidence>
          )}

          {ev.explanations?.length > 0 && (
            <Evidence title="Why the backend decided this">
              {ev.explanations.map((r, i) => <div className="reason" key={i}>• {r}</div>)}
            </Evidence>
          )}

          <Evidence title="Detector signals">
            <SignalRow label="Policy" result={ev.policyResult} />
            <SignalRow label="Behavioral" result={ev.behavioralResult} />
            <SignalRow label="Semantic" result={ev.semanticResult} />
            <SignalRow label="Context" result={ev.contextResult} />
          </Evidence>

          <Evidence title="Execution outcome">
            {ev.executionResult ? (
              <>
                <div className="reason">
                  Status: <strong>{ev.executionResult.status || exe.label}</strong>
                  {ev.executionResult.backend ? ` · via ${ev.executionResult.backend}` : ''}
                </div>
                {ev.executionResult.error && (
                  <div className="reason" style={{ color: 'var(--danger)' }}>
                    Error: {ev.executionResult.error}
                  </div>
                )}
                {ev.executionResult.summary && (
                  <pre className="json">{JSON.stringify(ev.executionResult.summary, null, 2)}</pre>
                )}
                <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                  Full tool output is restricted to the owning agent; the dashboard shows only this summary.
                </div>
              </>
            ) : (
              <div className="reason muted">
                No execution outcome recorded{ev.decision === 'BLOCK' ? ' — the action was blocked and never executed.' : ev.decision === 'REVIEW' ? ' — the action is held for review.' : '.'}
              </div>
            )}
          </Evidence>

          {ev.escalation && (
            <Evidence title="Escalation">
              <div className="reason" style={{ color: 'var(--warn)' }}>{ev.escalation.reason || JSON.stringify(ev.escalation)}</div>
            </Evidence>
          )}

          {detail?.raw?.detections?.length > 0 && (
            <Evidence title="Raw detection records">
              <pre className="json">{JSON.stringify(detail.raw.detections, null, 2)}</pre>
            </Evidence>
          )}

          {detail?.raw?.audit_trail?.length > 0 && (
            <Evidence title="Audit trail">
              {detail.raw.audit_trail.map((a, i) => (
                <div className="reason" key={i}>
                  <span className="muted">{formatTimestamp(a.created_at)}</span> — <strong>{a.action_type}</strong>
                  <span className="dim"> by {a.actor}</span>
                </div>
              ))}
            </Evidence>
          )}
        </>
      )}
    </Drawer>
  )
}
