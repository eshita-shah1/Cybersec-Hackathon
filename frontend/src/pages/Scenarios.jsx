import { useCallback, useEffect, useState } from 'react'
import { Play, FlaskConical, CheckCircle2, AlertCircle } from 'lucide-react'
import { Card, CardHead } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import Drawer from '../components/common/Drawer.jsx'
import { Loading, ErrorState, EmptyState } from '../components/common/States.jsx'
import EventDetail from '../components/activity/EventDetail.jsx'
import { listScenarios, runScenario } from '../services/scenarioService.js'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useToast } from '../app/ToastContext.jsx'
import { formatDecision, formatExecution } from '../utils/statusFormatter.js'
import { roundScore } from '../utils/formatters.js'

export default function Scenarios() {
  const { canAdmin: admin } = useConnection()
  const toast = useToast()
  const [scenarios, setScenarios] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [running, setRunning] = useState(null)
  const [result, setResult] = useState(null)
  const [eventId, setEventId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listScenarios()
      setScenarios(res.items || [])
    } catch (e) {
      setError(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const run = async (id) => {
    if (running) return
    setRunning(id)
    try {
      const res = await runScenario(id)
      setResult(res)
      if (res.all_expectations_met) {
        toast.success('Scenario complete', `All ${res.steps?.length ?? 0} steps matched expectations.`)
      } else {
        toast.info('Scenario complete', `Steps ${(res.mismatched_steps || []).join(', ')} differed from the expected outcome.`)
      }
    } catch (e) {
      toast.error('Scenario run failed', e.message)
    } finally {
      setRunning(null)
    }
  }

  return (
    <>
      <div className="mb16 muted">
        Backend simulator scenarios. Each step runs through the real <span className="mono">EventService.process</span>
        {' '}pipeline (same path as live events). These are deterministic simulator runs, not a live LLM.
      </div>

      {loading ? <Loading /> : error ? <ErrorState message={error.message} onRetry={load} disconnected={error.isNetwork} />
        : scenarios.length === 0 ? <EmptyState title="No scenarios" icon={FlaskConical} />
        : (
          <div className="grid grid-2">
            {scenarios.map((s) => (
              <Card key={s.scenario_id}>
                <CardHead title={s.title} sub={`${s.agent_id} · ${s.steps} steps`} />
                <div className="card-pad">
                  <p className="dim">{s.description}</p>
                  <div className="evidence">
                    <h5>Expected outcome</h5>
                    <div className="reason dim">{s.expected_outcome}</div>
                  </div>
                  <div className="flex between center">
                    <span className="mono muted">{s.scenario_id}</span>
                    <button className="btn primary sm" disabled={!admin || running} onClick={() => run(s.scenario_id)}
                      title={admin ? '' : 'Admin login required'}>
                      {running === s.scenario_id ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Play size={14} />}
                      Run
                    </button>
                  </div>
                  {!admin && <div className="muted mt8" style={{ fontSize: 12 }}>Admin authorization required to run scenarios.</div>}
                </div>
              </Card>
            ))}
          </div>
        )}

      {result && (
        <Drawer title={result.title} onClose={() => setResult(null)}
          right={<Badge tone={result.all_expectations_met ? 'ok' : 'warn'}>
            {result.all_expectations_met ? <><CheckCircle2 size={13} /> all matched</> : <><AlertCircle size={13} /> differed</>}
          </Badge>}>
          <dl className="kv mb16">
            <dt>Scenario</dt><dd className="mono">{result.scenario_id}</dd>
            <dt>Run ID</dt><dd className="mono">{result.run_id}</dd>
            <dt>Agent</dt><dd>{result.agent_id}</dd>
            <dt>Decisions</dt><dd>{Object.entries(result.decision_counts || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || '—'}</dd>
          </dl>
          {(result.preconditions || []).length > 0 && (
            <div className="evidence">
              <h5>Preconditions</h5>
              {result.preconditions.map((p, i) => <div className="reason dim" key={i}>• {p}</div>)}
            </div>
          )}
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>#</th><th>Tool</th><th>Decision</th><th>Risk</th><th>Exec</th><th></th></tr></thead>
              <tbody>
                {(result.steps || []).map((st) => (
                  <tr key={st.step}>
                    <td>{st.step} {st.matches_expectation ? '' : '⚠'}</td>
                    <td className="mono">{st.tool_name}</td>
                    <td><Badge tone={formatDecision(st.decision).tone}>{formatDecision(st.decision).label}</Badge></td>
                    <td>{typeof st.risk_score === 'number' ? roundScore(st.risk_score) : '—'}</td>
                    <td><Badge tone={formatExecution(st.execution_status).tone}>{formatExecution(st.execution_status).label}</Badge></td>
                    <td>{st.event_id && <button className="btn ghost sm" onClick={() => setEventId(st.event_id)}>view</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Drawer>
      )}

      {eventId && <EventDetail eventId={eventId} onClose={() => setEventId(null)} />}
    </>
  )
}
