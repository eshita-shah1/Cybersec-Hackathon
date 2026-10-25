import { apiRequest } from './apiClient.js'

// GET /api/analytics/overview -> database-derived dashboard stats
export const getOverview = (signal) => apiRequest('/api/analytics/overview', { signal })

// GET /health -> { status, app, environment, time }
export const getHealth = (signal) => apiRequest('/health', { signal, timeout: 8000 })

// GET /api/system/status -> components, detectors, risk config, executor health (no secrets)
export const getSystemStatus = (signal) => apiRequest('/api/system/status', { signal })
