import { apiRequest } from './apiClient.js'

export const listAgents = (status, signal) =>
  apiRequest('/api/agents', { params: status ? { status } : undefined, signal })

export const getAgent = (id, signal) => apiRequest(`/api/agents/${id}`, { signal })

// Admin-only lifecycle operations. Body matches StatusChange {reason, actor}.
export const suspendAgent = (id, reason, actor = 'analyst') =>
  apiRequest(`/api/agents/${id}/suspend`, { method: 'POST', admin: true, body: { reason, actor } })

export const reactivateAgent = (id, reason, actor = 'analyst') =>
  apiRequest(`/api/agents/${id}/reactivate`, { method: 'POST', admin: true, body: { reason, actor } })

export const deactivateAgent = (id, reason, actor = 'analyst') =>
  apiRequest(`/api/agents/${id}/deactivate`, { method: 'POST', admin: true, body: { reason, actor } })

export const listCredentials = (id) => apiRequest(`/api/agents/${id}/credentials`, { admin: true })
