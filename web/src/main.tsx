import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { consumeBrowserLink } from './auth'

consumeBrowserLink()
window.addEventListener('hashchange', () => {
  if (new URLSearchParams(location.hash.slice(1)).has('cao_token')) {
    consumeBrowserLink()
    location.reload()
  }
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
