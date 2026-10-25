import { describe, it, expect } from 'vitest'
import { normalizeEvent, normalizeWsEvent } from '../utils/eventNormalizer.js'
import {
  formatDecision, formatExecution, formatRiskLevel, riskScoreTone, formatSeverity,
} from '../utils/statusFormatter.js'

describe('eventNormalizer', () => {
  it('normalizes a REST EventResult and preserves raw', () => {
    const raw = {
      event_id: 'evt_1', agent_id: 'a1', tool_name: 'delete_customer', action: 'delete',
      target_resource: 'customers:records/all', risk_score: 88.8, risk_level: 'CRITICAL',
      decision: 'BLOCK', execution_status: 'NOT_EXECUTED', request_status: 'BLOCKED',
      explanations: ['Blocked (risk 88.8).'], policy_result: { status: 'DENY' },
    }
    const e = normalizeEvent(raw)
    expect(e.eventId).toBe('evt_1')
    expect(e.decision).toBe('BLOCK')
    expect(e.riskScore).toBe(88.8)
    expect(e.explanations).toHaveLength(1)
    expect(e.raw).toBe(raw)
  })

  it('does not fabricate missing fields', () => {
    const e = normalizeEvent({ event_id: 'x', agent_id: 'a' })
    expect(e.riskScore).toBeNull()
    expect(e.executionResult).toBeNull()
    expect(e.explanations).toEqual([])
    expect(e.alertIds).toEqual([])
  })

  it('normalizes a WebSocket security_event data block', () => {
    const e = normalizeWsEvent({
      event_id: 'evt_2', agent_id: 'a2', tool_name: 'search', action: 'search',
      decision: 'ALLOW', execution_status: 'SUCCEEDED', risk_score: 2, policy_status: 'ALLOW',
    })
    expect(e.eventId).toBe('evt_2')
    expect(e.policyResult).toEqual({ status: 'ALLOW' })
    expect(e.executionStatus).toBe('SUCCEEDED')
  })

  it('returns null for junk input', () => {
    expect(normalizeEvent(null)).toBeNull()
    expect(normalizeWsEvent(undefined)).toBeNull()
  })
})

describe('statusFormatter', () => {
  it('maps known decisions to tones', () => {
    expect(formatDecision('ALLOW').tone).toBe('ok')
    expect(formatDecision('BLOCK').tone).toBe('danger')
    expect(formatDecision('REVIEW').tone).toBe('warn')
    expect(formatDecision('MONITOR').tone).toBe('info')
  })

  it('gives unknown values a neutral fallback, not a crash', () => {
    expect(formatDecision('WAT')).toEqual({ tone: 'neutral', label: 'WAT' })
    expect(formatExecution(undefined).tone).toBe('neutral')
    expect(formatRiskLevel(null).label).toBe('—')
    expect(formatSeverity('weird').tone).toBe('neutral')
  })

  it('execution status distinguishes not-executed from succeeded', () => {
    expect(formatExecution('SUCCEEDED').tone).toBe('ok')
    expect(formatExecution('NOT_EXECUTED').tone).toBe('neutral')
    expect(formatExecution('FAILED').tone).toBe('danger')
  })

  it('riskScoreTone matches documented thresholds and is display-only', () => {
    expect(riskScoreTone(10)).toBe('ok')
    expect(riskScoreTone(30)).toBe('info')
    expect(riskScoreTone(60)).toBe('warn')
    expect(riskScoreTone(90)).toBe('danger')
    expect(riskScoreTone('n/a')).toBe('neutral')
  })
})
