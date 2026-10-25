import { useCallback, useEffect, useState } from 'react'
import { ClipboardCheck, Check, X, RefreshCw } from 'lucide-react'
import { Card, CardHead } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import Drawer from '../components/common/Drawer.jsx'
import ConfirmDialog from '../components/common/ConfirmDialog.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import EventDetail from '../components/activity/EventDetail.jsx'
import { listPendingReviews, approveReview, rejectReview } from '../services/reviewService.js'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useToast } from '../app/ToastContext.jsx'
import { useLiveEvents } from '../hooks/useLiveEvents.js'
import { formatExecution } from '../utils/statusFormatter.js'
import { formatRelative, formatTimestamp } from '../utils/formatters.js'

export default function Reviews({ onCountChange }) {
  const { canAdmin: admin } = useConnection()
  const toast = useToast()
  const [reviews, setReviews] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [eventId, setEventId] = useState(null)
  const [decision, setDecision] = useState(null) // {type, review}
  const [busy, setBusy] = useState(false)
  const [decisionError, setDecisionError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listPendingReviews()
      setReviews(res.items || [])
      if (onCountChange) onCountChange(res.total ?? (res.items || []).length)
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [onCountChange])

  useEffect(() => { load() }, [load])
  useLiveEvents({ limit: 1, onMessage: (m) => {
    if (m.type === 'security_event' && (m.data?.review_id || m.data?.decision === 'REVIEW')) load()
  } })

  const submit = async (notes) => {
    setBusy(true)
    setDecisionError(null)
    const { type, review } = decision
    try {
      const fn = type === 'approve' ? approveReview : rejectReview
      const res = await fn(review.review_id, 'analyst', notes || null)
      if (type === 'approve') {
        if (res.executed) {
          toast.success('Approved and executed', `Tool executed (${res.execution?.status || 'succeeded'}).`)
        } else if (res.status === 'revalidation_failed') {
          toast.error('Approved but blocked on revalidation', 'Policy re-check denied the action; it was not executed.')
        } else {
          toast.info('Approved', 'Execution did not complete; see the event for details.')
        }
      } else {
        toast.info('Rejected', 'The action was rejected and never executed.')
      }
      setDecision(null)
      setSelected(null)
      await load()
    } catch (e) {
      setDecisionError(e.message)
      if (e.isAuth) toast.error('Not authorized', 'Admin authorization is required.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="flex between center mb16">
        <div className="muted">Actions held before execution until an authorized analyst decides.</div>
        <button className="btn sm" onClick={load}><RefreshCw size={14} /> Refresh</button>
      </div>

      <Card>
        <CardHead title="Pending reviews" sub="Held actions — approval is not execution" />
        {loading ? <Loading /> : error ? <ErrorState message={error.message} onRetry={load} disconnected={error.isNetwork} />
          : reviews.length === 0 ? <EmptyState title="No actions awaiting review" message="Nothing is currently held for approval." icon={ClipboardCheck} />
          : (
            <div className="table-wrap">
              <table className="data">
                <thead><tr><th>Agent</th><th>Reason</th><th>Created</th><th>Expires</th><th></th></tr></thead>
                <tbody>
                  {reviews.map((r) => (
                    <tr key={r.review_id} className="clickable" onClick={() => setSelected(r)}>
                      <td className="nowrap">{r.agent_id}</td>
                      <td className="dim">{r.reason}</td>
                      <td className="muted nowrap">{formatRelative(r.created_at)}</td>
                      <td className="muted nowrap">{r.expires_at ? formatRelative(r.expires_at) : '—'}</td>
                      <td><Badge tone="warn">Pending</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>

      {selected && (
        <Drawer title="Review held action" onClose={() => setSelected(null)} right={<Badge tone="warn">Pending</Badge>}>
          <dl className="kv mb16">
            <dt>Review ID</dt><dd className="mono">{selected.review_id}</dd>
            <dt>Event ID</dt><dd className="mono">{selected.event_id}</dd>
            <dt>Agent</dt><dd>{selected.agent_id}</dd>
            <dt>Created</dt><dd>{formatTimestamp(selected.created_at)}</dd>
            {selected.expires_at && (<><dt>Expires</dt><dd>{formatTimestamp(selected.expires_at)} ({formatRelative(selected.expires_at)})</dd></>)}
          </dl>
          <div className="evidence">
            <h5>Why it's held</h5>
            <div className="reason">{selected.reason}</div>
          </div>
          <button className="btn sm mb16" onClick={() => setEventId(selected.event_id)}>View full event evidence</button>
          <div className="evidence">
            <h5>What approval does</h5>
            <div className="reason dim">
              Approval re-runs the policy against the agent's current profile. A hard denial still
              blocks execution (recorded as revalidation failed). If it passes, the gateway issues a
              single-use permission and the tool executes. The result is shown from persisted state.
            </div>
          </div>
          {admin ? (
            <div className="flex gap8">
              <button className="btn success" onClick={() => { setDecisionError(null); setDecision({ type: 'approve', review: selected }) }}>
                <Check size={15} /> Approve
              </button>
              <button className="btn danger" onClick={() => { setDecisionError(null); setDecision({ type: 'reject', review: selected }) }}>
                <X size={15} /> Reject
              </button>
            </div>
          ) : <div className="muted" style={{ fontSize: 12 }}>Admin authorization required to decide reviews.</div>}
        </Drawer>
      )}

      {eventId && <EventDetail eventId={eventId} onClose={() => setEventId(null)} />}

      {decision && (
        <ConfirmDialog
          title={decision.type === 'approve' ? 'Approve held action' : 'Reject held action'}
          message={decision.type === 'approve'
            ? 'This submits a real approval. The policy is revalidated before any execution; a hard denial still blocks it.'
            : 'This rejects the held action. It will never execute.'}
          confirmLabel={decision.type === 'approve' ? 'Approve' : 'Reject'}
          confirmTone={decision.type === 'approve' ? 'success' : 'danger'}
          withReason
          reasonRequired={false}
          reasonLabel="Notes (optional, audited)"
          busy={busy}
          error={decisionError}
          onConfirm={submit}
          onCancel={() => !busy && setDecision(null)}
        />
      )}
    </>
  )
}
