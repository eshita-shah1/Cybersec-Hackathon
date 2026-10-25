import { describe, it, expect, beforeEach, vi } from 'vitest'
import { WebSocketService } from '../services/websocketService.js'

// Minimal fake WebSocket we can drive manually.
class FakeWS {
  static instances = []
  constructor(url) {
    this.url = url
    this.readyState = 0
    FakeWS.instances.push(this)
  }
  send() {}
  close() {
    this.readyState = 3
    this.onclose && this.onclose()
  }
  _open() {
    this.readyState = 1
    this.onopen && this.onopen()
  }
  _msg(obj) {
    this.onmessage && this.onmessage({ data: typeof obj === 'string' ? obj : JSON.stringify(obj) })
  }
}

describe('WebSocketService', () => {
  beforeEach(() => {
    FakeWS.instances = []
    global.WebSocket = FakeWS
    global.WebSocket.OPEN = 1
  })

  it('reports connected and delivers parsed messages to subscribers', () => {
    const svc = new WebSocketService('ws://x/ws/events')
    const received = []
    svc.subscribe((m) => received.push(m))
    const statuses = []
    svc.onStatus((s) => statuses.push(s))
    svc.connect()
    const ws = FakeWS.instances[0]
    ws._open()
    ws._msg({ type: 'security_event', data: { event_id: 'e1', decision: 'BLOCK' } })
    expect(statuses).toContain('connected')
    expect(received).toHaveLength(1)
    expect(received[0].data.event_id).toBe('e1')
    svc.disconnect()
  })

  it('deduplicates security_events by event_id', () => {
    const svc = new WebSocketService('ws://x')
    const ids = []
    svc.subscribe((m) => ids.push(m.data.event_id))
    svc.connect()
    const ws = FakeWS.instances[0]
    ws._open()
    ws._msg({ type: 'security_event', data: { event_id: 'dup' } })
    ws._msg({ type: 'security_event', data: { event_id: 'dup' } })
    ws._msg({ type: 'security_event', data: { event_id: 'other' } })
    expect(ids).toEqual(['dup', 'other'])
    svc.disconnect()
  })

  it('ignores malformed frames without throwing', () => {
    const svc = new WebSocketService('ws://x')
    const got = []
    svc.subscribe((m) => got.push(m))
    svc.connect()
    const ws = FakeWS.instances[0]
    ws._open()
    expect(() => ws._msg('<<not json>>')).not.toThrow()
    expect(() => ws._msg({ nope: true })).not.toThrow() // no "type"
    expect(got).toHaveLength(0)
    svc.disconnect()
  })

  it('schedules a reconnect after an unexpected close', () => {
    vi.useFakeTimers()
    const svc = new WebSocketService('ws://x')
    svc.connect()
    const first = FakeWS.instances[0]
    first._open()
    first.close() // unexpected
    vi.advanceTimersByTime(1100)
    expect(FakeWS.instances.length).toBe(2) // reconnected
    svc.disconnect()
    vi.useRealTimers()
  })

  it('does not reconnect after an explicit disconnect()', () => {
    vi.useFakeTimers()
    const svc = new WebSocketService('ws://x')
    svc.connect()
    FakeWS.instances[0]._open()
    svc.disconnect()
    vi.advanceTimersByTime(5000)
    expect(FakeWS.instances.length).toBe(1)
    vi.useRealTimers()
  })
})
