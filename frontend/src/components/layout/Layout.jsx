/**
 * Application shell: responsive navbar, live-connection banner and footer (spec §15).
 *
 * The connection banner is deliberately always visible while the stream is unhealthy:
 * spec §22 requires a disconnected state, and a silently-stale feed is worse than an
 * obvious one during a live demo.
 */

import { NavLink, useLocation } from 'react-router-dom'
import { APP_NAME, FOOTER_NOTE, NAV_ROUTES, TAGLINE, PRINCIPLE } from '../../lib/constants'
import { StatusPill } from '../ui/Primitives'
import { useGlobalEvents } from '../../state/GlobalEventContext'

function ConnectionBanner() {
  const { state } = useGlobalEvents()
  const { status, attempt, lastSeq } = state.connection

  if (status === 'connected') {
    return (
      <div className="flex items-center gap-2 text-2xs text-ink-faint">
        <span className="h-1.5 w-1.5 rounded-full bg-teal" />
        <span className="hidden sm:inline">live</span>
        <span className="font-mono tabular-nums">#{lastSeq}</span>
      </div>
    )
  }

  if (status === 'closed') return null

  const label =
    status === 'connecting'
      ? 'connecting to event stream'
      : status === 'reconnecting'
        ? `reconnecting${attempt ? ` · attempt ${attempt}` : ''}`
        : status === 'stale'
          ? 'feed stale'
          : 'disconnected'

  return (
    <div
      className="flex items-center gap-2 rounded-full border border-amber/30 bg-amber/10 px-2.5 py-1 text-2xs text-amber"
      role="status"
      title="The live event stream is not connected. REST data is still valid."
    >
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber" />
      <span className="font-mono">{label}</span>
    </div>
  )
}

export function Navbar() {
  const location = useLocation()

  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.06] bg-base/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1600px] items-center gap-4 px-4 py-3 sm:px-6">
        {/* Brand */}
        <NavLink to="/" className="flex shrink-0 items-center gap-2.5" aria-label={APP_NAME}>
          <span className="relative flex h-8 w-8 items-center justify-center rounded-lg border border-cyan/30 bg-cyan/10">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path
                d="M12 2.8 4.5 6.1v5.4c0 4.6 3.2 8.5 7.5 9.7 4.3-1.2 7.5-5.1 7.5-9.7V6.1L12 2.8Z"
                stroke="#22D3EE"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
              <path
                d="m8.8 12.1 2.2 2.2 4.2-4.4"
                stroke="#2DD4BF"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <span className="hidden sm:block">
            <span className="block text-sm font-semibold leading-none tracking-tight text-ink">
              {APP_NAME}
            </span>
            <span className="mt-0.5 block font-mono text-2xs leading-none text-ink-faint">
              {PRINCIPLE}
            </span>
          </span>
        </NavLink>

        {/* Desktop nav */}
        <nav className="ml-2 hidden flex-1 items-center gap-1 lg:flex" aria-label="Primary">
          {NAV_ROUTES.map((route) => (
            <NavLink
              key={route.to}
              to={route.to}
              end={route.end}
              className={({ isActive }) =>
                `rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  isActive
                    ? 'bg-cyan/12 text-cyan'
                    : 'text-ink-muted hover:bg-white/[0.05] hover:text-ink'
                }`
              }
            >
              {route.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <ConnectionBanner />
          <StatusPill tone="neutral" dot={false} className="hidden md:inline-flex">
            prototype
          </StatusPill>
        </div>
      </div>

      {/* Mobile nav — horizontally scrollable, so it never wraps into a tall block. */}
      <nav
        className="flex gap-1 overflow-x-auto border-t border-white/[0.05] px-4 py-2 lg:hidden"
        aria-label="Primary mobile"
      >
        {NAV_ROUTES.map((route) => {
          const active =
            route.end ? location.pathname === route.to : location.pathname.startsWith(route.to)
          return (
            <NavLink
              key={route.to}
              to={route.to}
              end={route.end}
              className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                active ? 'bg-cyan/12 text-cyan' : 'text-ink-muted hover:bg-white/[0.05]'
              }`}
            >
              {route.label}
            </NavLink>
          )
        })}
      </nav>
    </header>
  )
}

export function Footer() {
  return (
    <footer className="mt-12 border-t border-white/[0.06] bg-base-900/40">
      <div className="mx-auto flex max-w-[1600px] flex-col gap-3 px-4 py-6 sm:px-6 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs font-medium text-ink-muted">
            {APP_NAME} — {TAGLINE}
          </p>
          <p className="mt-1 font-mono text-2xs text-ink-faint">{FOOTER_NOTE}</p>
        </div>
        <div className="flex flex-col items-start gap-1 md:items-end">
          <p className="text-2xs text-ink-faint">
            Threat scenarios are controlled simulations. No production readiness or
            certification is claimed.
          </p>
          <p className="font-mono text-2xs text-ink-faint/70">
            The AI proposes. TrustMesh decides. The blockchain records.
          </p>
        </div>
      </div>
    </footer>
  )
}

/** Page shell with a consistent max width and vertical rhythm. */
export function Page({ children, className = '' }) {
  return <div className={`mx-auto max-w-[1600px] px-4 py-6 sm:px-6 ${className}`}>{children}</div>
}

/** Consistent page heading. */
export function PageHeader({ title, subtitle, right, eyebrow }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow mb-1.5">{eyebrow}</div> : null}
        <h1 className="text-lg font-semibold tracking-tight text-ink sm:text-xl">{title}</h1>
        {subtitle ? (
          <p className="mt-1.5 max-w-3xl text-xs leading-relaxed text-ink-faint">{subtitle}</p>
        ) : null}
      </div>
      {right ? <div className="flex shrink-0 flex-wrap items-center gap-2">{right}</div> : null}
    </div>
  )
}

/** Primary action button. */
export function ActionButton({
  children,
  onClick,
  disabled = false,
  pending = false,
  tone = 'primary',
  className = '',
  type = 'button',
  title,
}) {
  const tones = {
    primary: 'border-cyan/30 bg-cyan/12 text-cyan hover:bg-cyan/20',
    danger: 'border-red/30 bg-red/12 text-red hover:bg-red/20',
    ghost: 'border-white/10 bg-white/[0.04] text-ink-muted hover:bg-white/[0.08] hover:text-ink',
  }
  return (
    <button
      type={type}
      title={title}
      onClick={onClick}
      disabled={disabled || pending}
      className={`inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${tones[tone]} ${className}`}
    >
      {pending ? (
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
      ) : null}
      {children}
    </button>
  )
}

export default function Layout({ children }) {
  return (
    <div className="flex min-h-screen flex-col">
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  )
}
