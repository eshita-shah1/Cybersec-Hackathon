import { useEffect, useRef, useState, useCallback } from 'react'
import { useConnection } from '../app/ConnectionContext.jsx'
import { normalizeWsEvent } from '../utils/eventNormalizer.js'

// Subscribe to live security events from the shared socket. Returns a rolling list of the most
// recent normalized events plus the raw message stream via onMessage callback.
export function useLiveEvents({ limit = 60, onMessage } = {}) {
  const { ws } = useConnection()
  const [events, setEvents] = useState([])
  const [lastAt, setLastAt] = useState(null)
  const cbRef = useRef(onMessage)
  cbRef.current = onMessage

  useEffect(() => {
    return ws.subscribe((msg) => {
      if (cbRef.current) cbRef.current(msg)
      if (msg.type === 'security_event') {
        const ev = normalizeWsEvent(msg.data)
        if (!ev || !ev.eventId) return
        setLastAt(new Date().toISOString())
        setEvents((prev) => {
          if (prev.some((e) => e.eventId === ev.eventId)) return prev
          return [ev, ...prev].slice(0, limit)
        })
      }
    })
  }, [ws, limit])

  const clear = useCallback(() => setEvents([]), [])
  return { events, lastAt, clear }
}
