// One shared WebSocket connection to /ws/events, with reconnection, dedup and clean teardown.
// The backend envelope is { type, version, sent_at, data }. We fan out typed messages to
// subscribers. REST remains the source of truth for persisted data; a live connection is not
// proof the backend is healthy.

import { deriveWsUrl } from './apiClient.js'

const MAX_BACKOFF = 15000
const BASE_BACKOFF = 1000
const SEEN_LIMIT = 500

export class WebSocketService {
  constructor(url = deriveWsUrl()) {
    this.url = url
    this.ws = null
    this.status = 'disconnected' // disconnected | connecting | connected
    this.attempts = 0
    this.stopped = false
    this.subscribers = new Set()
    this.statusSubscribers = new Set()
    this._seen = new Set()
    this._seenOrder = []
    this._reconnectTimer = null
  }

  connect() {
    this.stopped = false
    this._open()
  }

  _open() {
    if (this.stopped) return
    this._setStatus('connecting')
    let ws
    try {
      ws = new WebSocket(this.url)
    } catch {
      this._scheduleReconnect()
      return
    }
    this.ws = ws
    ws.onopen = () => {
      this.attempts = 0
      this._setStatus('connected')
    }
    ws.onmessage = (ev) => this._onMessage(ev)
    ws.onerror = () => {
      /* onclose handles reconnect */
    }
    ws.onclose = () => {
      this.ws = null
      if (!this.stopped) {
        this._setStatus('disconnected')
        this._scheduleReconnect()
      }
    }
  }

  _onMessage(ev) {
    let msg
    try {
      msg = JSON.parse(ev.data)
    } catch {
      return // ignore malformed frames
    }
    if (!msg || typeof msg !== 'object' || !msg.type) return
    // Deduplicate persisted security events by event_id (live + replays can repeat).
    if (msg.type === 'security_event') {
      const id = msg.data && msg.data.event_id
      if (id) {
        if (this._seen.has(id)) return
        this._seen.add(id)
        this._seenOrder.push(id)
        if (this._seenOrder.length > SEEN_LIMIT) {
          this._seen.delete(this._seenOrder.shift())
        }
      }
    }
    this.subscribers.forEach((fn) => {
      try {
        fn(msg)
      } catch {
        /* a bad subscriber must not break the socket */
      }
    })
  }

  _scheduleReconnect() {
    if (this.stopped || this._reconnectTimer) return
    const delay = Math.min(BASE_BACKOFF * 2 ** this.attempts, MAX_BACKOFF)
    this.attempts += 1
    this._reconnectTimer = setTimeout(() => {
      this._reconnectTimer = null
      this._open()
    }, delay)
  }

  _setStatus(s) {
    this.status = s
    this.statusSubscribers.forEach((fn) => fn(s))
  }

  ping() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send('ping')
  }

  subscribe(fn) {
    this.subscribers.add(fn)
    return () => this.subscribers.delete(fn)
  }

  onStatus(fn) {
    this.statusSubscribers.add(fn)
    fn(this.status)
    return () => this.statusSubscribers.delete(fn)
  }

  disconnect() {
    this.stopped = true
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
    if (this.ws) {
      try {
        this.ws.close()
      } catch {
        /* ignore */
      }
      this.ws = null
    }
    this._setStatus('disconnected')
  }
}

// App-wide singleton so every component shares one connection.
let _singleton = null
export function getWebSocketService() {
  if (!_singleton) _singleton = new WebSocketService()
  return _singleton
}
