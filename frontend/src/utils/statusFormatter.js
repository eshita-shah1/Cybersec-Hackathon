// Presentation mapping only. These do NOT recompute risk or re-decide anything — they map the
// backend's own values to a colour tone and label. Unknown values get a neutral fallback.

// tones: ok (green) | info (cyan) | warn (amber) | danger (red) | neutral (gray)
const DECISION = {
  ALLOW: { tone: 'ok', label: 'Allow' },
  MONITOR: { tone: 'info', label: 'Monitor' },
  REVIEW: { tone: 'warn', label: 'Review' },
  BLOCK: { tone: 'danger', label: 'Block' },
}

const EXECUTION = {
  NOT_STARTED: { tone: 'neutral', label: 'Not started' },
  AWAITING_REVIEW: { tone: 'warn', label: 'Awaiting review' },
  RUNNING: { tone: 'info', label: 'Running' },
  SUCCEEDED: { tone: 'ok', label: 'Succeeded' },
  FAILED: { tone: 'danger', label: 'Failed' },
  NOT_EXECUTED: { tone: 'neutral', label: 'Not executed' },
}

const RISK_LEVEL = {
  LOW: { tone: 'ok', label: 'Low' },
  MEDIUM: { tone: 'info', label: 'Medium' },
  HIGH: { tone: 'warn', label: 'High' },
  CRITICAL: { tone: 'danger', label: 'Critical' },
}

const REQUEST_STATUS = {
  ATTEMPTED: { tone: 'neutral', label: 'Attempted' },
  EVALUATED: { tone: 'info', label: 'Evaluated' },
  BLOCKED: { tone: 'danger', label: 'Blocked' },
  PENDING_REVIEW: { tone: 'warn', label: 'Pending review' },
  APPROVED: { tone: 'info', label: 'Approved' },
  REJECTED: { tone: 'neutral', label: 'Rejected' },
  EXECUTING: { tone: 'info', label: 'Executing' },
  EXECUTED: { tone: 'ok', label: 'Executed' },
  FAILED: { tone: 'danger', label: 'Failed' },
}

const AGENT_STATUS = {
  active: { tone: 'ok', label: 'Active' },
  suspended: { tone: 'warn', label: 'Suspended' },
  deactivated: { tone: 'neutral', label: 'Deactivated' },
}

const SEVERITY = {
  none: { tone: 'neutral', label: 'None' },
  low: { tone: 'info', label: 'Low' },
  medium: { tone: 'warn', label: 'Medium' },
  high: { tone: 'danger', label: 'High' },
  critical: { tone: 'danger', label: 'Critical' },
}

function lookup(map, value) {
  if (value === null || value === undefined) return { tone: 'neutral', label: '—' }
  const hit = map[value] || map[String(value).toUpperCase()] || map[String(value).toLowerCase()]
  return hit || { tone: 'neutral', label: String(value) }
}

export const formatDecision = (v) => lookup(DECISION, v)
export const formatExecution = (v) => lookup(EXECUTION, v)
export const formatRiskLevel = (v) => lookup(RISK_LEVEL, v)
export const formatRequestStatus = (v) => lookup(REQUEST_STATUS, v)
export const formatAgentStatus = (v) => lookup(AGENT_STATUS, v)
export const formatSeverity = (v) => lookup(SEVERITY, v)

// A display-only risk tone from a numeric score, matching the backend's documented default
// thresholds (monitor 25 / review 50 / block 75). This is a presentation convenience and is
// NOT used to authorize or block anything.
export function riskScoreTone(score) {
  if (typeof score !== 'number') return 'neutral'
  if (score >= 75) return 'danger'
  if (score >= 50) return 'warn'
  if (score >= 25) return 'info'
  return 'ok'
}
