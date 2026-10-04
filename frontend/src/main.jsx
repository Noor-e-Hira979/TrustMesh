/**
 * Entry point.
 *
 * HashRouter rather than BrowserRouter so the built bundle can be served from any static
 * path (or opened from disk) without a server-side rewrite rule. The routes are shallow
 * and this is a single-page console, so the tradeoff costs nothing.
 */

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import './index.css'

const container = document.getElementById('root')

if (!container) {
  throw new Error('TrustMesh could not mount: #root is missing from index.html')
}

createRoot(container).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
)
