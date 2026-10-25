import { AlertTriangle, Inbox, WifiOff, RefreshCw } from 'lucide-react'

export function Loading({ label = 'Loading…' }) {
  return (
    <div className="state" role="status">
      <div className="spinner" />
      <div className="mt16 dim">{label}</div>
    </div>
  )
}

export function EmptyState({ title = 'Nothing here yet', message, icon: Icon = Inbox }) {
  return (
    <div className="state">
      <div className="icon"><Icon size={34} /></div>
      <h4>{title}</h4>
      {message && <div>{message}</div>}
    </div>
  )
}

export function ErrorState({ title = 'Something went wrong', message, onRetry, disconnected }) {
  const Icon = disconnected ? WifiOff : AlertTriangle
  return (
    <div className="state error">
      <div className="icon"><Icon size={34} /></div>
      <h4>{title}</h4>
      {message && <div className="mb16">{message}</div>}
      {onRetry && (
        <button className="btn sm" onClick={onRetry}>
          <RefreshCw size={14} /> Retry
        </button>
      )}
    </div>
  )
}
