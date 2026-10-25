// Normalize the two shapes the backend emits for an event:
//  - REST EventResult (full fields, nested result objects)
//  - WebSocket security_event data (flattened subset)
// We keep the original object around (raw) so detail panels can show everything.
// We never fabricate fields: anything absent stays null/undefined.

function pick(obj, ...keys) {
  for (const k of keys) {
    if (obj && obj[k] !== undefined && obj[k] !== null) return obj[k]
  }
  return undefined
}

// Accepts an EventResult (REST) object.
export function normalizeEvent(e) {
  if (!e || typeof e !== 'object') return null
  return {
    eventId: e.event_id,
    agentId: e.agent_id,
    taskId: e.task_id ?? null,
    receivedAt: e.received_at ?? null,
    toolName: e.tool_name,
    action: e.action,
    targetResource: e.target_resource,
    sensitivity: e.resource_sensitivity ?? null,
    description: e.action_description ?? null,
    decision: e.decision ?? null,
    recommendedResponse: e.recommended_response ?? null,
    requestStatus: e.request_status ?? null,
    executionStatus: e.execution_status ?? null,
    riskScore: typeof e.risk_score === 'number' ? e.risk_score : null,
    riskLevel: e.risk_level ?? null,
    explanations: Array.isArray(e.explanations) ? e.explanations : [],
    policyResult: e.policy_result ?? null,
    behavioralResult: e.behavioral_result ?? null,
    semanticResult: e.semantic_result ?? null,
    contextResult: e.context_result ?? null,
    riskBreakdown: e.risk_breakdown ?? null,
    executionResult: e.execution_result ?? null,
    escalation: e.escalation ?? null,
    reviewId: e.review_id ?? null,
    alertIds: Array.isArray(e.alert_ids) ? e.alert_ids : [],
    duplicate: Boolean(e.duplicate),
    raw: e,
  }
}

// Accepts the data block of a WebSocket security_event message.
export function normalizeWsEvent(d) {
  if (!d || typeof d !== 'object') return null
  return {
    eventId: d.event_id,
    agentId: d.agent_id,
    taskId: null,
    receivedAt: d.timestamp ?? null,
    toolName: d.tool_name,
    action: d.action,
    targetResource: d.target_resource,
    sensitivity: null,
    description: null,
    decision: d.decision ?? null,
    recommendedResponse: null,
    requestStatus: d.request_status ?? null,
    executionStatus: d.execution_status ?? null,
    riskScore: typeof d.risk_score === 'number' ? d.risk_score : null,
    riskLevel: d.risk_level ?? null,
    explanations: Array.isArray(d.explanations) ? d.explanations : [],
    policyResult: pick(d, 'policy_status') ? { status: d.policy_status } : null,
    behavioralResult: null,
    semanticResult: null,
    contextResult: null,
    riskBreakdown: null,
    executionResult: null,
    escalation: d.escalation ?? null,
    reviewId: d.review_id ?? null,
    alertIds: Array.isArray(d.alert_ids) ? d.alert_ids : [],
    duplicate: false,
    raw: d,
  }
}
