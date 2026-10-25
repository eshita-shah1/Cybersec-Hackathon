import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  apiRequest, ApiError, setAdminKey, clearAdminKey, hasAdminKey, setAdminRequired,
} from '../services/apiClient.js'

function mockFetch(status, body, ok = status < 400) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  })
}

describe('apiClient', () => {
  beforeEach(() => {
    clearAdminKey()
    setAdminRequired(true) // default; open-mode test overrides locally
  })
  afterEach(() => vi.restoreAllMocks())

  it('builds the URL with base and query params, no auth header by default', async () => {
    const f = mockFetch(200, { ok: true })
    global.fetch = f
    await apiRequest('/api/events', { params: { agent_id: 'a1', decision: 'BLOCK', empty: '' } })
    const [url, opts] = f.mock.calls[0]
    expect(url).toBe('http://127.0.0.1:8000/api/events?agent_id=a1&decision=BLOCK')
    expect(opts.headers['X-API-Key']).toBeUndefined()
  })

  it('attaches the admin key only when admin:true', async () => {
    const f = mockFetch(200, {})
    global.fetch = f
    setAdminKey('secret-key')
    expect(hasAdminKey()).toBe(true)
    await apiRequest('/api/scenarios/x/run', { method: 'POST', admin: true })
    expect(f.mock.calls[0][1].headers['X-API-Key']).toBe('secret-key')
  })

  it('throws admin_required (without calling fetch) when admin:true but no key set', async () => {
    const f = mockFetch(200, {})
    global.fetch = f
    await expect(apiRequest('/x', { admin: true })).rejects.toMatchObject({ code: 'admin_required', status: 401 })
    expect(f).not.toHaveBeenCalled()
  })

  it('open mode: admin:true call proceeds WITHOUT a key and sends no X-API-Key header', async () => {
    const f = mockFetch(200, { ok: true })
    global.fetch = f
    setAdminRequired(false) // backend has no admin key configured
    await apiRequest('/api/scenarios/x/run', { method: 'POST', admin: true })
    expect(f).toHaveBeenCalledOnce()
    expect(f.mock.calls[0][1].headers['X-API-Key']).toBeUndefined()
  })

  it('surfaces the backend error envelope {error:{code,message}}', async () => {
    global.fetch = mockFetch(404, { error: { code: 'unknown_agent', message: 'not registered' } }, false)
    await expect(apiRequest('/api/events', { method: 'POST' })).rejects.toMatchObject({
      status: 404, code: 'unknown_agent', message: 'not registered',
    })
  })

  it('maps network failure to a network ApiError', async () => {
    global.fetch = vi.fn().mockRejectedValue(new TypeError('failed to fetch'))
    const err = await apiRequest('/health').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.isNetwork).toBe(true)
    expect(err.message).toMatch(/Unable to reach/)
  })

  it('reports invalid JSON rather than fabricating data', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => '<<notjson>>' })
    await expect(apiRequest('/x')).rejects.toMatchObject({ code: 'invalid_json' })
  })

  it('serializes a JSON body and sets content-type', async () => {
    const f = mockFetch(200, {})
    global.fetch = f
    await apiRequest('/x', { method: 'POST', body: { a: 1 } })
    expect(f.mock.calls[0][1].body).toBe('{"a":1}')
    expect(f.mock.calls[0][1].headers['Content-Type']).toBe('application/json')
  })
})
