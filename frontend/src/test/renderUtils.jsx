import { MemoryRouter } from 'react-router-dom'
import { render } from '@testing-library/react'
import { ToastProvider } from '../app/ToastContext.jsx'

// A light harness for pages that use toasts + routing but NOT the full ConnectionProvider.
// Pages needing connection state receive props or are tested with a mocked context.
export function renderWithProviders(ui, { route = '/' } = {}) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <ToastProvider>{ui}</ToastProvider>
    </MemoryRouter>
  )
}

export function fetchOk(body) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) }
}
export function fetchErr(status, error) {
  return { ok: false, status, text: async () => JSON.stringify({ error }) }
}
