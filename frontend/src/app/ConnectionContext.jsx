import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react'
import { getHealth, getSystemStatus } from '../services/analyticsService.js'
import { getWebSocketService } from '../services/websocketService.js'
import { hasAdminKey, onAdminKeyChange, setAdminRequired } from '../services/apiClient.js'

const ConnectionContext = createContext(null)

export function ConnectionProvider({ children }) {
  const [health, setHealth] = useState({ state: 'unknown', lastOk: null, app: null, env: null })
  const [systemStatus, setSystemStatus] = useState(null)
  const [wsStatus, setWsStatus] = useState('disconnected')
  const [admin, setAdmin] = useState(hasAdminKey())
  // adminRequired starts true (safe default) and is corrected once we learn the backend's mode.
  const [adminRequired, setAdminRequiredState] = useState(true)
  const ws = useRef(getWebSocketService())

  const refreshHealth = useCallback(async () => {
    try {
      const h = await getHealth()
      setHealth({ state: 'ok', lastOk: new Date().toISOString(), app: h.app, env: h.environment })
      try {
        const status = await getSystemStatus()
        setSystemStatus(status)
        const required = status?.security?.admin_key_configured !== false
        setAdminRequiredState(required)
        setAdminRequired(required)
      } catch {
        /* status is best-effort */
      }
      return true
    } catch {
      setHealth((prev) => ({ ...prev, state: 'down' }))
      return false
    }
  }, [])

  useEffect(() => {
    refreshHealth()
    const interval = setInterval(refreshHealth, 20000)
    return () => clearInterval(interval)
  }, [refreshHealth])

  useEffect(() => {
    const svc = ws.current
    const off = svc.onStatus(setWsStatus)
    svc.connect()
    return () => {
      off()
      svc.disconnect()
    }
  }, [])

  useEffect(() => onAdminKeyChange(setAdmin), [])

  return (
    <ConnectionContext.Provider
      value={{
        health, systemStatus, wsStatus, admin, adminRequired,
        // Effective authorization: either the operator entered a key, or the backend is open.
        canAdmin: admin || !adminRequired,
        refreshHealth, ws: ws.current,
      }}
    >
      {children}
    </ConnectionContext.Provider>
  )
}

export function useConnection() {
  const ctx = useContext(ConnectionContext)
  if (!ctx) throw new Error('useConnection must be used within ConnectionProvider')
  return ctx
}
