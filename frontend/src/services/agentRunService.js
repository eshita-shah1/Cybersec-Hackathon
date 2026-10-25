import { apiRequest } from './apiClient.js'

// GET /api/agent/status -> whether the server can launch the real LLM agent
export const getAgentStatus = (signal) => apiRequest('/api/agent/status', { signal })

// POST /api/agent/run (admin) -> starts the agent server-side, returns a run record
export const runAgent = (task = 'benign') =>
  apiRequest('/api/agent/run', { method: 'POST', admin: true, body: { task } })

// GET /api/agent/runs/{id} -> poll run status/result
export const getAgentRun = (id, signal) => apiRequest(`/api/agent/runs/${id}`, { signal })
