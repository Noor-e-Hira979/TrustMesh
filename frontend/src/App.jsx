/**
 * TrustMesh console — application root.
 *
 * `/` is the Hero landing page; `/overview` is Mission Control, the live dashboard the
 * Hero's primary action transitions into. Every other page stays reachable from the top
 * navigation bar (spec §15).
 *
 * The SSE provider is mounted above the router so a navigation never drops the event
 * stream, which matters during a live demo when a presenter jumps between pages mid-run.
 */

import { Component } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import Layout from './components/layout/Layout'
import { GlassCard } from './components/ui/Primitives'
import { GlobalEventProvider } from './hooks/useEventStream'
import About from './pages/About'
import AgentNetwork from './pages/AgentNetwork'
import Blockchain from './pages/Blockchain'
import Hero from './pages/Hero'
import Overview from './pages/Overview'
import Security from './pages/Security'
import Transactions from './pages/Transactions'

/**
 * Route-level error boundary.
 *
 * A rendering failure in one page must not blank the whole console mid-demo, so this
 * shows a recoverable error with a way back rather than an empty screen.
 */
class PageBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('TrustMesh page error:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <Layout>
        <div className="mx-auto max-w-2xl px-4 py-16">
          <GlassCard className="border-red/25 p-6" glow="glow-red">
            <h2 className="text-sm font-semibold text-red">This page failed to render</h2>
            <p className="mt-2 text-xs leading-relaxed text-ink-muted">
              {this.state.error.message || 'An unexpected error occurred.'}
            </p>
            <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
              The backend and the live event stream are unaffected. Reload this page, or return
              to the overview.
            </p>
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                onClick={() => this.setState({ error: null })}
                className="rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-xs text-cyan transition-colors hover:bg-cyan/20"
              >
                Try again
              </button>
              <a
                href="/overview"
                className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-ink-muted transition-colors hover:border-white/25 hover:text-ink"
              >
                Back to Mission Control
              </a>
            </div>
          </GlassCard>
        </div>
      </Layout>
    )
  }
}

function NotFound() {
  return (
    <GlassCard className="p-8 text-center">
      <p className="font-mono text-2xs uppercase tracking-widest text-ink-faint">404</p>
      <h2 className="mt-2 text-sm font-semibold text-ink">No such page</h2>
      <p className="mt-1.5 text-xs text-ink-faint">
        That route is not part of the TrustMesh console.
      </p>
      <a
        href="/overview"
        className="mt-4 inline-block rounded-lg border border-cyan/30 bg-cyan/10 px-3 py-1.5 text-xs text-cyan transition-colors hover:bg-cyan/20"
      >
        Back to Mission Control
      </a>
    </GlassCard>
  )
}

export default function App() {
  return (
    <GlobalEventProvider>
      <Layout>
        <PageBoundary>
          <Routes>
            <Route path="/" element={<Hero />} />
            <Route path="/overview" element={<Overview />} />
            <Route path="/network" element={<AgentNetwork />} />
            <Route path="/transactions" element={<Transactions />} />
            <Route path="/security" element={<Security />} />
            <Route path="/blockchain" element={<Blockchain />} />
            <Route path="/about" element={<About />} />
            <Route path="/index.html" element={<Navigate to="/" replace />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </PageBoundary>
      </Layout>
    </GlobalEventProvider>
  )
}
