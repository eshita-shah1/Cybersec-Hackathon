import { useState } from 'react'
import { KeyRound } from 'lucide-react'
import { setAdminKey } from '../../services/apiClient.js'
import { useToast } from '../../app/ToastContext.jsx'

// Local-demo admin authentication: the operator pastes the ADMIN_API_KEY. It is held in memory
// only (via setAdminKey), never persisted, never shown back.
export default function AdminDialog({ onClose }) {
  const [value, setValue] = useState('')
  const toast = useToast()
  const save = () => {
    if (!value.trim()) return
    setAdminKey(value.trim())
    toast.success('Admin session started', 'The key is kept in memory for this tab only.')
    onClose()
  }
  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h3><KeyRound size={16} style={{ verticalAlign: '-2px' }} /> Administrator authorization</h3>
        </div>
        <div className="dialog-body">
          <p className="mb16">
            Enter the backend <span className="mono">ADMIN_API_KEY</span> to authorize review
            decisions, agent lifecycle changes and scenario runs. It stays in memory for this
            browser tab only and is sent as the <span className="mono">X-API-Key</span> header. It
            is never stored or displayed.
          </p>
          <div className="field">
            <label>Admin API key</label>
            <input
              className="input"
              type="password"
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && save()}
              placeholder="paste key…"
            />
          </div>
        </div>
        <div className="dialog-foot">
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary" onClick={save} disabled={!value.trim()}>Authorize</button>
        </div>
      </div>
    </div>
  )
}
