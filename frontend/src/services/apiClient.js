// Single API integration layer for the D3COY gateway.
// - resolves the base URL from VITE_API_BASE_URL
// - attaches the admin key (X-API-Key) only when a call opts in (admin: true)
// - parses JSON, surfaces the backend's {error:{code,message,details}} shape
// - supports query params, request cancellation and timeouts
//
// The admin key lives only in memory here (set via setAdminKey). It is never read from a
// VITE_ env var, never logged, and never serialized.

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
).replace(/\/+$/, '')

export function deriveWsUrl() {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  const u = new URL(API_BASE_URL)
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
  u.pathname = '/ws/events'
  return u.toString()
}

let _adminKey = null
// Whether the backend requires an admin key. Set from /api/system/status
// (security.admin_key_configured). When false, the backend runs in open mode and admin
// endpoints are allowed without a key.
let _adminRequired = true
const _listeners = new Set()

export function setAdminRequired(required) {
  _adminRequired = Boolean(required)
}
export function adminRequired() {
  return _adminRequired
}

export function setAdminKey(key) {
  _adminKey = key && key.trim() ? key.trim() : null
  _listeners.forEach((fn) => fn(Boolean(_adminKey)))
}
export function clearAdminKey() {
  setAdminKey(null)
}
export function hasAdminKey() {
  return Boolean(_adminKey)
}
export function onAdminKeyChange(fn) {
  _listeners.add(fn)
  return () => _listeners.delete(fn)
}

// An error with a stable code the UI can branch on. Never contains secrets.
export class ApiError extends Error {
  constructor(message, { status = 0, code = 'error', details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
  get isAuth() {
    return this.status === 401 || this.status === 403
  }
  get isNetwork() {
    return this.status === 0
  }
}

function buildQuery(params) {
  if (!params) return ''
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue
    q.append(k, String(v))
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

export async function apiRequest(
  path,
  { method = 'GET', body, params, admin = false, signal, timeout = 30000 } = {}
) {
  const url = `${API_BASE_URL}${path}${buildQuery(params)}`
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (admin) {
    if (_adminKey) {
      headers['X-API-Key'] = _adminKey
    } else if (_adminRequired) {
      throw new ApiError('Administrator authorization is required for this action.', {
        status: 401,
        code: 'admin_required',
      })
    }
    // else: open mode — the backend has no admin key configured, so send no header.
  }

  const controller = new AbortController()
  const timer = timeout ? setTimeout(() => controller.abort(), timeout) : null
  // chain an external cancel signal into our controller
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  let res
  try {
    res = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    })
  } catch (err) {
    if (timer) clearTimeout(timer)
    if (err.name === 'AbortError') {
      throw new ApiError('Request was cancelled or timed out.', { status: 0, code: 'aborted' })
    }
    throw new ApiError('Unable to reach the D3COY gateway.', { status: 0, code: 'network' })
  }
  if (timer) clearTimeout(timer)

  let payload = null
  const text = await res.text()
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      if (!res.ok) {
        throw new ApiError(`Request failed (HTTP ${res.status}).`, {
          status: res.status,
          code: 'bad_response',
        })
      }
      throw new ApiError('The gateway returned a response the dashboard could not read.', {
        status: res.status,
        code: 'invalid_json',
      })
    }
  }

  if (!res.ok) {
    const e = payload && payload.error ? payload.error : {}
    throw new ApiError(e.message || `Request failed (HTTP ${res.status}).`, {
      status: res.status,
      code: e.code || 'http_error',
      details: e.details ?? null,
    })
  }
  return payload
}
