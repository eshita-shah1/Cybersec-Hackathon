import { useCallback, useEffect, useState } from 'react'
import { Terminal, Copy, Check, Radio, Bot, Play, Sparkles } from 'lucide-react'
import { Card, CardHead } from '../components/common/Card.jsx'
import Badge from '../components/common/Badge.jsx'
import { EmptyState } from '../components/common/States.jsx'
import { useConnection } from '../app/ConnectionContext.jsx'
import { useToast } from '../app/ToastContext.jsx'
import { useLiveEvents } from '../hooks/useLiveEvents.js'
import { getAgentStatus, runAgent, getAgentRun } from '../services/agentRunService.js'
import { formatDecision, formatExecution } from '../utils/statusFormatter.js'
import { formatRelative, roundScore } from '../utils/formatters.js'

const COMMANDS = [
  { label: '1 · Sandbox executor (Terminal 1)', cmd: 'uvicorn sandbox_executor.main:app --port 8100' },
  { label: '2 · D3COY gateway (Terminal 2)', cmd: 'uvicorn app.main:app --port 8000' },
  { label: '3 · Real Gemini agent (Terminal 3)', cmd: 'python -m agent.run_procurement_demo' },
]

function CopyRow({ label, cmd }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(cmd).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <div className="field">
      <label>{label}</label>
      <div className="flex gap8 center">
        <code className="mono" style={{ flex: 1, background: 'var(--bg)', padding: '9px 11px', borderRadius: 7, border: '1px solid var(--border-strong)' }}>{cmd}</code>
        <button className="btn sm" onClick={copy}>{copied ? <Check size={14} /> : <Copy size={14} />}</button>
      </div>
    </div>
  )
}

function ServerRunPanel() {
  const { canAdmin: admin } = useConnection()
  const toast = useToast()
  const [status, setStatus] = useState(null)
  const [run, setRun] = useState(null)
  const [busy, setBusy] = useState(false)

  const refreshStatus = useCallback(() => {
    getAgentStatus().then(setStatus).catch(() => setStatus({ ready: false }))
  }, [])
  useEffect(() => { refreshStatus() }, [refreshStatus])

  // Poll the active run until it finishes.
  useEffect(() => {
    if (!run || run.status === 'completed' || run.status === 'failed') return
    const t = setInterval(() => {
      getAgentRun(run.run_id).then((r) => {
        setRun(r)
        if (r.status === 'completed') toast.success('Agent run complete', `${r.decisions?.length ?? 0} tool decisions.`)
        if (r.status === 'failed') toast.error('Agent run failed', r.error || '')
      }).catch(() => {})
    }, 2000)
    return () => clearInterval(t)
  }, [run, toast])

  const start = async (task) => {
    setBusy(true)
    try {
      const r = await runAgent(task)
      setRun(r)
      toast.info('Agent launched', 'Watch its actions stream in below.')
    } catch (e) {
      toast.error('Could not launch agent', e.message)
    } finally {
      setBusy(false)
      refreshStatus()
    }
  }

  const ready = status?.ready
  const running = run && run.status !== 'completed' && run.status !== 'failed'

  return (
    <Card>
      <CardHead title="Run the agent from here" sub="Server-triggered — no terminal needed"
        right={<Sparkles size={16} className="muted" />} />
      <div className="card-pad">
        {status && (
          <dl className="kv mb16">
            <dt>Status</dt>
            <dd><Badge tone={ready ? 'ok' : 'warn'}>{ready ? 'ready' : status.enabled === false ? 'disabled' : 'LLM key not configured'}</Badge></dd>
            {status.provider && (<><dt>Provider</dt><dd>{status.provider}{status.model ? ` · ${status.model}` : ''}</dd></>)}
          </dl>
        )}
        <p className="dim mb16">
          Launches the real procurement agent inside the backend (where the LLM key lives) and
          drives the normal enforcement pipeline. The browser never calls the LLM. Requires admin.
        </p>
        <div className="flex gap8 wrap">
          <button className="btn primary" disabled={!admin || !ready || busy || running} onClick={() => start('benign')}>
            {running ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Play size={15} />}
            Run procurement task
          </button>
          <button className="btn" disabled={!admin || !ready || busy || running} onClick={() => start('adversarial')}
            title="Asks the agent to reach customer data — it has no such tool, so nothing is attempted">
            Run adversarial prompt
          </button>
        </div>
        {!admin && <div className="muted mt8" style={{ fontSize: 12 }}>Admin authorization required to launch the agent.</div>}
        {!ready && admin && status?.enabled !== false && (
          <div className="muted mt8" style={{ fontSize: 12 }}>
            Set <span className="mono">GEMINI_API_KEY</span> (or <span className="mono">ANTHROPIC_API_KEY</span>) in the
            backend environment and restart the gateway.
          </div>
        )}
        {run && (
          <div className="evidence mt16">
            <h5>Run {run.run_id} · {run.task}</h5>
            <div className="reason">Status: <Badge tone={run.status === 'completed' ? 'ok' : run.status === 'failed' ? 'danger' : 'info'}>{run.status}</Badge>{run.provider ? ` · ${run.provider}` : ''}</div>
            {(run.decisions || []).map((d, i) => (
              <div className="reason" key={i}>
                <span className="mono">{d.tool}</span> → <Badge tone={formatDecision(d.decision).tone}>{formatDecision(d.decision).label}</Badge>
                <span className="muted"> {d.execution_status}</span>
              </div>
            ))}
            {run.final_text && <div className="dim mt8" style={{ fontSize: 12 }}>{run.final_text}</div>}
            {run.error && <div className="reason" style={{ color: 'var(--danger)' }}>{run.error}</div>}
          </div>
        )}
      </div>
    </Card>
  )
}

export default function LiveAgentDemo() {
  const { wsStatus } = useConnection()
  const { events, lastAt } = useLiveEvents({ limit: 40 })

  return (
    <>
      <div className="section-gap"><ServerRunPanel /></div>
      <div className="grid grid-2 section-gap">
        <Card>
          <CardHead title="Run the real Gemini agent" sub="Runs locally in your terminal" right={<Terminal size={16} className="muted" />} />
          <div className="card-pad">
            <p className="dim mb16">
              The procurement agent is a local Python process (LangGraph + the configured LLM
              provider). The browser cannot and must not launch it or call the LLM directly — the
              Gemini key stays in the backend environment. Run the commands below in VS Code
              terminals from <span className="mono">d3coy-backend</span>, then watch its activity
              appear here live through the gateway's REST and WebSocket interfaces.
            </p>
            {COMMANDS.map((c) => <CopyRow key={c.label} {...c} />)}
            <div className="muted" style={{ fontSize: 12 }}>
              The agent's tools are D3COY-protected wrappers: every tool call it makes is evaluated
              and enforced by the gateway. Blocked actions never execute; a send is held for review.
            </div>
          </div>
        </Card>

        <Card>
          <CardHead title="How to read the run" sub="What you should see" right={<Bot size={16} className="muted" />} />
          <div className="card-pad">
            <ul className="dim" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              <li>Read supplier emails / search catalog → <Badge tone="ok">Allow</Badge> and executed</li>
              <li>A send → <Badge tone="warn">Review</Badge>, held until you approve it under Human Reviews</li>
              <li>Any delete / export of customer data → <Badge tone="danger">Block</Badge>, never executed</li>
              <li>Customer rows stay intact; the outbox only ever holds approved messages</li>
            </ul>
            <div className="muted mt16" style={{ fontSize: 12 }}>
              Provider/run metadata is shown only when the backend records it on an event. The
              dashboard does not assert "real Gemini" on its own.
            </div>
          </div>
        </Card>
      </div>

      <Card>
        <CardHead title="Live agent activity" sub={lastAt ? `updated ${formatRelative(lastAt)}` : 'waiting for events…'}
          right={<span className="conn-pill"><span className={`dot ${wsStatus === 'connected' ? 'ok' : 'warn'}`} /><Radio size={12} /> {wsStatus}</span>} />
        {events.length === 0 ? (
          <EmptyState title="No live events yet"
            message={wsStatus === 'connected' ? 'Run the agent (or a scenario) and events will stream in here.' : 'Live connection is not established yet.'}
            icon={Radio} />
        ) : (
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th>Agent</th><th>Tool · op</th><th>Resource</th><th>Decision</th><th>Risk</th><th>Execution</th><th>When</th></tr></thead>
              <tbody>
                {events.map((e) => (
                  <tr key={e.eventId}>
                    <td className="nowrap">{e.agentId}</td>
                    <td className="mono nowrap">{e.toolName} · {e.action}</td>
                    <td className="mono dim">{e.targetResource}</td>
                    <td><Badge tone={formatDecision(e.decision).tone}>{formatDecision(e.decision).label}</Badge></td>
                    <td>{typeof e.riskScore === 'number' ? roundScore(e.riskScore) : '—'}</td>
                    <td><Badge tone={formatExecution(e.executionStatus).tone}>{formatExecution(e.executionStatus).label}</Badge></td>
                    <td className="muted nowrap">{formatRelative(e.receivedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  )
}
