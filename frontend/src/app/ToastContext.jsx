import { createContext, useCallback, useContext, useState } from 'react'

const ToastContext = createContext(null)

let _id = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const remove = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const push = useCallback(
    (toast) => {
      const id = ++_id
      setToasts((t) => [...t, { id, tone: 'info', ...toast }])
      setTimeout(() => remove(id), toast.duration || 5000)
    },
    [remove]
  )
  const api = {
    success: (title, msg) => push({ tone: 'ok', title, msg }),
    error: (title, msg) => push({ tone: 'danger', title, msg, duration: 7000 }),
    info: (title, msg) => push({ tone: 'info', title, msg }),
  }
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`} onClick={() => remove(t.id)}>
            {t.title && <div className="t-title">{t.title}</div>}
            {t.msg && <div className="t-msg">{t.msg}</div>}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
