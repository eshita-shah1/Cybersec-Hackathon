import { useState } from 'react'

export default function ConfirmDialog({
  title,
  message,
  confirmLabel = 'Confirm',
  confirmTone = 'primary',
  withReason = false,
  reasonRequired = true,
  reasonLabel = 'Reason',
  busy = false,
  error = null,
  onConfirm,
  onCancel,
}) {
  const [reason, setReason] = useState('')
  const canConfirm = !busy && (!withReason || !reasonRequired || reason.trim().length >= 3)
  return (
    <div className="dialog-overlay" onClick={busy ? undefined : onCancel}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <h3>{title}</h3>
        </div>
        <div className="dialog-body">
          <div className="mb16">{message}</div>
          {withReason && (
            <div className="field">
              <label>{reasonLabel}</label>
              <textarea
                className="input"
                rows={2}
                value={reason}
                disabled={busy}
                placeholder="Recorded in the audit trail…"
                onChange={(e) => setReason(e.target.value)}
              />
            </div>
          )}
          {error && <div className="badge danger" style={{ whiteSpace: 'normal' }}>{error}</div>}
        </div>
        <div className="dialog-foot">
          <button className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button>
          <button
            className={`btn ${confirmTone}`}
            onClick={() => onConfirm(reason.trim())}
            disabled={!canConfirm}
          >
            {busy ? <span className="spinner" style={{ width: 14, height: 14 }} /> : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
