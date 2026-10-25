import { apiRequest } from './apiClient.js'

// GET /api/reviews/pending -> { items, total }
export const listPendingReviews = (signal) => apiRequest('/api/reviews/pending', { signal })

export const getReview = (id, signal) => apiRequest(`/api/reviews/${id}`, { signal })

// Admin-only. Returns the review dict plus {executed, execution?} from the backend.
export const approveReview = (id, reviewer = 'analyst', notes = null) =>
  apiRequest(`/api/reviews/${id}/approve`, { method: 'POST', admin: true, body: { reviewer, notes } })

export const rejectReview = (id, reviewer = 'analyst', notes = null) =>
  apiRequest(`/api/reviews/${id}/reject`, { method: 'POST', admin: true, body: { reviewer, notes } })

// Alerts
export const listAlerts = (filters = {}, signal) =>
  apiRequest('/api/alerts', { params: filters, signal })

export const getAlert = (id, signal) => apiRequest(`/api/alerts/${id}`, { signal })

export const reviewAlert = (id, status, reviewer = 'analyst', notes = null) =>
  apiRequest(`/api/alerts/${id}/review`, { method: 'POST', admin: true, body: { status, reviewer, notes } })
