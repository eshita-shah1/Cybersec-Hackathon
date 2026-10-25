import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './app/App.jsx'
import { ConnectionProvider } from './app/ConnectionContext.jsx'
import { ToastProvider } from './app/ToastContext.jsx'
import './styles/global.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <ConnectionProvider>
          <App />
        </ConnectionProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>
)
