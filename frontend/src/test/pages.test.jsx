import { describe, it, expect, vi, beforeEach } from 'vitest'
import { screen, waitFor, fireEvent } from '@testing-library/react'
import { renderWithProviders } from './renderUtils.jsx'

// Shared mocks for context + live events so pages render in isolation.
vi.mock('../app/ConnectionContext.jsx', () => ({
  useConnection: () => ({
    admin: true, canAdmin: true, adminRequired: true,
    ws: { subscribe: () => () => {} }, systemStatus: null, health: { state: 'ok' },
  }),
}))
vi.mock('../hooks/useLiveEvents.js', () => ({ useLiveEvents: () => ({ events: [], lastAt: null, clear: () => {} }) }))

import * as agentService from '../services/agentService.js'
import * as eventService from '../services/eventService.js'
import * as reviewService from '../services/reviewService.js'
import * as scenarioService from '../services/scenarioService.js'
import { ApiError } from '../services/apiClient.js'

import Agents from '../pages/Agents.jsx'
import Reviews from '../pages/Reviews.jsx'
import Scenarios from '../pages/Scenarios.jsx'

beforeEach(() => vi.restoreAllMocks())

describe('Agents page', () => {
  it('renders agents returned by the registry API', async () => {
    vi.spyOn(agentService, 'listAgents').mockResolvedValue([
      { agent_id: 'finance-agent-01', agent_name: 'Finance Agent', role: 'financial_analyst',
        assigned_task: 'Analyze expenses', status: 'active', allowed_tools: ['read_expenses'] },
    ])
    renderWithProviders(<Agents />)
    expect(await screen.findByText('Finance Agent')).toBeInTheDocument()
    expect(screen.getByText('finance-agent-01')).toBeInTheDocument()
  })

  it('shows an empty state when there are no agents', async () => {
    vi.spyOn(agentService, 'listAgents').mockResolvedValue([])
    renderWithProviders(<Agents />)
    expect(await screen.findByText(/No agents/i)).toBeInTheDocument()
  })

  it('shows a disconnected error state on network failure', async () => {
    vi.spyOn(agentService, 'listAgents').mockRejectedValue(new ApiError('Unable to reach the D3COY gateway.', { status: 0, code: 'network' }))
    renderWithProviders(<Agents />)
    expect(await screen.findByText('Unable to reach the D3COY gateway.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Retry/i })).toBeInTheDocument()
  })
})

describe('Reviews page', () => {
  const pending = { items: [{ review_id: 'rev_1', event_id: 'evt_1', agent_id: 'email-agent-01', reason: 'send requires authorization', created_at: new Date().toISOString(), expires_at: null }], total: 1 }

  it('renders pending reviews from the API', async () => {
    vi.spyOn(reviewService, 'listPendingReviews').mockResolvedValue(pending)
    renderWithProviders(<Reviews />)
    expect(await screen.findByText(/send requires authorization/)).toBeInTheDocument()
  })

  it('does NOT show success when approval fails', async () => {
    vi.spyOn(reviewService, 'listPendingReviews').mockResolvedValue(pending)
    vi.spyOn(reviewService, 'approveReview').mockRejectedValue(new ApiError('This action is not authorized.', { status: 403, code: 'unauthorized' }))
    renderWithProviders(<Reviews />)
    fireEvent.click(await screen.findByText(/send requires authorization/))
    fireEvent.click(await screen.findByRole('button', { name: /Approve/i })) // open dialog
    const confirm = await screen.findAllByRole('button', { name: /Approve/i })
    fireEvent.click(confirm[confirm.length - 1]) // dialog confirm
    await waitFor(() => expect(reviewService.approveReview).toHaveBeenCalled())
    expect(screen.queryByText(/executed/i)).not.toBeInTheDocument()
  })

  it('distinguishes approved+executed from revalidation failure', async () => {
    vi.spyOn(reviewService, 'listPendingReviews').mockResolvedValue(pending)
    vi.spyOn(reviewService, 'approveReview').mockResolvedValue({ status: 'revalidation_failed', executed: false })
    renderWithProviders(<Reviews />)
    fireEvent.click(await screen.findByText(/send requires authorization/))
    fireEvent.click(await screen.findByRole('button', { name: /Approve/i }))
    const confirm = await screen.findAllByRole('button', { name: /Approve/i })
    fireEvent.click(confirm[confirm.length - 1])
    await waitFor(() => expect(reviewService.approveReview).toHaveBeenCalled())
    expect(await screen.findByText(/blocked on revalidation/i)).toBeInTheDocument()
  })
})

describe('Scenarios page', () => {
  it('prevents duplicate submissions while a run is in flight', async () => {
    vi.spyOn(scenarioService, 'listScenarios').mockResolvedValue({
      items: [{ scenario_id: 'normal_research', title: 'A. Normal', description: 'desc', agent_id: 'research-agent-01', expected_outcome: 'allowed', steps: 6 }],
    })
    let resolveRun
    vi.spyOn(scenarioService, 'runScenario').mockImplementation(() => new Promise((r) => { resolveRun = r }))
    renderWithProviders(<Scenarios />)
    const btn = await screen.findByRole('button', { name: /Run/i })
    fireEvent.click(btn)
    await waitFor(() => expect(btn).toBeDisabled())
    fireEvent.click(btn) // second click ignored
    expect(scenarioService.runScenario).toHaveBeenCalledTimes(1)
    resolveRun({ scenario_id: 'normal_research', title: 'A. Normal', run_id: 'r1', agent_id: 'research-agent-01', decision_counts: { ALLOW: 6 }, all_expectations_met: true, steps: [] })
  })
})
