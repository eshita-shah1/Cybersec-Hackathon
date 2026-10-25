import { apiRequest } from './apiClient.js'

// GET /api/events -> Page[EventResult] { items, total, limit, offset }
export const listEvents = (filters = {}, signal) =>
  apiRequest('/api/events', { params: filters, signal })

// GET /api/events/{id} -> EventDetail
export const getEvent = (id, signal) => apiRequest(`/api/events/${id}`, { signal })

// GET /api/audit -> { items, total, limit, offset }
export const listAudit = (filters = {}, signal) =>
  apiRequest('/api/audit', { params: filters, signal })
