import { apiRequest } from './apiClient.js'

// GET /api/scenarios -> { items: [{scenario_id, title, description, agent_id, expected_outcome, steps}] }
export const listScenarios = (signal) => apiRequest('/api/scenarios', { signal })

// POST /api/scenarios/{id}/run  (admin) -> run summary with per-step decisions
export const runScenario = (id) =>
  apiRequest(`/api/scenarios/${id}/run`, { method: 'POST', admin: true, timeout: 120000 })
