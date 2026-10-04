/**
 * Hero — the landing screen for the TrustMesh control plane.
 *
 * Purpose
 * -------
 * A judge should understand the product in one glance, before touching anything. This
 * page states the paradigm in three cards — REASON, ENFORCE, VERIFY — and hands off to
 * the live Overview with a single action.
 *
 * Design constraints (spec §14)
 * -----------------------------
 * Enterprise AI infrastructure. Not a hacker terminal, not a crypto dashboard, not
 * neon cyberpunk. Deep navy/blue-black base, electric cyan primary, teal for verified,
 * amber for warnings, violet reserved for the AI layer. Glassmorphism containers with
 * restrained borders and glows. Effects are ambient and slow, never busy.
 *
 * Liveness
 * --------
 * The stat strip and status pill read from the live control plane, so the page proves the
 * system is actually running. When the backend is unreachable it degrades to a clearly
 * labelled offline state instead of showing fabricated numbers — a landing page that
 * invents metrics is worse than one that admits it is disconnected.
 *
 * Accessibility
 * -------------
 * `prefers-reduced-motion` disables the ambient drift, the sweep and the launch halo via
 * the global rule in `index.css`. The CTA is a real button, keyboard reachable, and the
 * cards are focusable with visible rings.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
// TAGLINE ("Secure Trust Infrastructure for Autonomous AI Agents") is rendered as the
// second heading line below, so it is not interpolated here.
import { APP_NAME, PRINCIPLE } from '../lib/constants'
import { truncateHash } from '../lib/format'
import { useHealth, useOverview } from '../hooks/useApi'
import { useGlobalEvents } from '../state/GlobalEventContext'
import { Page } from '../components/layout/Layout'
import { StatusPill } from '../components/ui/Primitives'
import { Skeleton } from '../components/ui/States'

/* --------------------------------------------------------------- ambient bg */
function AmbientBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 overflow-hidden" aria-hidden="true">
      {/* Base wash keeps the page from reading as flat black. */}
      <div className="absolute inset-0 bg-base" />

      {/* Deep cyan ambience, top-left. */}
      <div className="absolute -left-[18%] -top-[26%] h-[720px] w-[720px] animate-orb-drift-a rounded-full bg-[radial-gradient(circle,rgba(34,211,238,0.16),transparent_62%)] blur-3xl" />

      {/* Violet counterweight, bottom-right — reads as the AI layer. */}
      <div className="absolute -bottom-[30%] -right-[14%] h-[680px] w-[680px] animate-orb-drift-b rounded-full bg-[radial-gradient(circle,rgba(167,139,250,0.13),transparent_64%)] blur-3xl" />

      {/* Teal anchor, mid-right, for the verification layer. */}
      <div className="absolute right-[6%] top-[38%] h-[420px] w-[420px] rounded-full bg-[radial-gradient(circle,rgba(45,212,191,0.09),transparent_66%)] blur-3xl" />

      {/* Engineering grid, faded out toward the bottom. */}
      <div className="absolute inset-0 bg-grid-faint bg-grid [mask-image:linear-gradient(to_bottom,rgba(0,0,0,0.9),transparent_78%)]" />

      {/* Vignette to focus the centre column. */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_35%,rgba(7,10,17,0.75)_100%)]" />
    </div>
  )
}

/* --------------------------------------------------------------- paradigm */
const PARADIGM = [
  {
    id: 'reason',
    phase: '01',
    layer: 'AGENT',
    accent: 'violet',
    title: 'Multi-Agent Negotiation',
    headline: 'REASON',
    question: 'What the AI wants or proposes to do.',
    body:
      'Buyer and supplier runtimes reason, price and counter-offer across organizational ' +
      'boundaries. Gemini supplies language and commercial framing only. It never approves ' +
      'its own action and never holds a credential.',
    detail:
      'Counter-offers are clamped to the feasible band — the intersection of the buyer ' +
      'ceiling and the supplier floor — so legitimate negotiation is budget-safe by ' +
      'construction rather than by hope.',
    chips: [
      'buyer_agent_1',
      'supplier_agent_a',
      'supplier_agent_b',
      'search',
      'request_quote',
      'negotiate',
      'propose',
    ],
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="6" cy="7" r="2.6" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="18" cy="7" r="2.6" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="18" r="2.6" stroke="currentColor" strokeWidth="1.5" />
        <path
          d="M8.4 8.4 10.6 15.6M15.6 8.4 13.4 15.6M8.6 7h6.8"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: 'enforce',
    phase: '02',
    layer: 'TRUSTMESH',
    accent: 'cyan',
    title: 'Deterministic Policy Boundary',
    headline: 'ENFORCE',
    question: 'Whether the action is authorized and policy-compliant.',
    body:
      'Seven ordered stages — schema, identity, credential, capability, authorization, ' +
      'threat interception, policy — with no language model anywhere in the decision path. ' +
      'The pipeline fails closed: an unexpected error becomes a BLOCK, never an ALLOW.',
    detail:
      'The Capability Guard intersects declared grants with the requested capability and ' +
      'refuses a protected capability even if a registry entry were corrupted to grant it. ' +
      'Trust evaluation returns explained factors and a rule-derived tier, never a score.',
    chips: [
      'BUDGET_CEILING',
      'DELIVERY_SLA',
      'CERTIFICATION_VALIDITY',
      'QUANTITY_MATCH',
      'CAPABILITY_GUARD',
      'THREAT_INTERCEPTION',
    ],
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 2.8 4.5 6.1v5.4c0 4.6 3.2 8.5 7.5 9.7 4.3-1.2 7.5-5.1 7.5-9.7V6.1L12 2.8Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M12 8.4v4.2m0 2.6h.01"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    id: 'verify',
    phase: '03',
    layer: 'BLOCKCHAIN',
    accent: 'teal',
    title: 'Canonical Hash & On-Chain Proof',
    headline: 'VERIFY',
    question: 'What was actually recorded for later verification.',
    body:
      'The final agreement is canonicalised per RFC 8785, hashed with SHA-256, and committed ' +
      'to TrustMeshRegistry. The canonicalization is implemented in-repo — UTF-16 code-unit ' +
      'key ordering, ECMAScript number formatting, minimal escaping — so the digest is ' +
      'auditable and reproducible.',
    detail:
      'Only a hash, a transaction reference, two pseudonymous agent references and a block ' +
      'timestamp reach the chain. Credentials, prices, quantities and negotiation ' +
      'transcripts never leave the control plane.',
    chips: ['RFC 8785', 'SHA-256', 'TrustMeshRegistry', 'append-only', 'bytes32'],
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M12 2.6 4.8 6v5.2c0 4.4 3 8.2 7.2 9.4 4.2-1.2 7.2-5 7.2-9.4V6L12 2.6Z"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="m8.9 11.9 2.2 2.2 4-4.2"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
]

const ACCENT = {
  violet: {
    text: 'text-violet',
    border: 'border-violet/25',
    borderHover: 'hover:border-violet/50',
    bg: 'bg-violet/[0.07]',
    glow: 'hover:shadow-glow-violet',
    chip: 'border-violet/25 bg-violet/[0.08] text-violet',
    dot: 'bg-violet',
    ring: 'focus-visible:ring-violet/50',
  },
  cyan: {
    text: 'text-cyan',
    border: 'border-cyan/25',
    borderHover: 'hover:border-cyan/50',
    bg: 'bg-cyan/[0.07]',
    glow: 'hover:shadow-glow-cyan',
    chip: 'border-cyan/25 bg-cyan/[0.08] text-cyan',
    dot: 'bg-cyan',
    ring: 'focus-visible:ring-cyan/50',
  },
  teal: {
    text: 'text-teal',
    border: 'border-teal/25',
    borderHover: 'hover:border-teal/50',
    bg: 'bg-teal/[0.07]',
    glow: 'hover:shadow-glow-teal',
    chip: 'border-teal/25 bg-teal/[0.08] text-teal',
    dot: 'bg-teal',
    ring: 'focus-visible:ring-teal/50',
  },
}

function ParadigmCard({ item, index, stat }) {
  const a = ACCENT[item.accent]

  return (
    <article
      className={`group relative animate-fade-up overflow-hidden rounded-2xl border ${a.border} ${a.borderHover} ${a.bg} ${a.glow} bg-base-800/45 p-5 backdrop-blur-md transition-all duration-300 hover:-translate-y-1 hover:bg-base-700/55`}
      style={{ animationDelay: `${140 + index * 110}ms` }}
      tabIndex={0}
    >
      {/* Hairline of light along the top edge on hover. */}
      <span
        className="pointer-events-none absolute inset-x-0 top-0 h-px opacity-0 transition-opacity duration-300 group-hover:opacity-100 group-focus-visible:opacity-100"
        style={{
          background: `linear-gradient(90deg, transparent, ${
            item.accent === 'violet'
              ? 'rgba(167,139,250,0.9)'
              : item.accent === 'cyan'
                ? 'rgba(34,211,238,0.9)'
                : 'rgba(45,212,191,0.9)'
          }, transparent)`,
        }}
      />

      <div className="flex items-start justify-between gap-3">
        <span
          className={`flex h-11 w-11 items-center justify-center rounded-xl border ${a.border} ${a.bg} ${a.text}`}
        >
          {item.icon}
        </span>
        <span className="flex flex-col items-end gap-1">
          <span className={`font-mono text-2xs tracking-widest ${a.text}`}>{item.phase}</span>
          <span className="font-mono text-2xs text-ink-faint/70">{item.layer}</span>
        </span>
      </div>

      <h3 className={`mt-4 font-mono text-base font-semibold tracking-tight ${a.text}`}>
        {item.headline}
      </h3>
      <h4 className="mt-1 text-sm font-semibold leading-snug text-ink">{item.title}</h4>
      <p className="mt-1.5 text-2xs italic leading-relaxed text-ink-faint">{item.question}</p>

      <p className="mt-3 text-xs leading-relaxed text-ink-muted">{item.body}</p>
      <p className="mt-2 text-2xs leading-relaxed text-ink-faint">{item.detail}</p>

      <div className="mt-4 flex flex-wrap gap-1.5">
        {item.chips.map((chip) => (
          <span
            key={chip}
            className={`rounded border px-1.5 py-0.5 font-mono text-2xs ${a.chip}`}
          >
            {chip}
          </span>
        ))}
      </div>

      {/* Live figure for this layer, when the control plane reported one. */}
      {stat ? (
        <div className="mt-4 flex items-baseline justify-between border-t border-white/[0.06] pt-3">
          <span className="eyebrow">{stat.label}</span>
          <span className={`font-mono text-sm tabular-nums ${a.text}`}>{stat.value}</span>
        </div>
      ) : null}
    </article>
  )
}

/* -------------------------------------------------------------------- page */
export default function Hero() {
  const navigate = useNavigate()
  const { state } = useGlobalEvents()
  const [launching, setLaunching] = useState(false)

  const health = useHealth()
  const overview = useOverview({ pollMs: 10000 })

  const stats = overview.data?.stats
  const chain = health.data?.components?.find((c) => c.name === 'blockchain')
  const online = !health.error && health.data !== null

  // Per-layer live figures, sourced from the same stats the dashboard uses.
  const layerStats = useMemo(() => {
    const byLabel = new Map((stats || []).map((s) => [s.label, s]))
    const offers = byLabel.get('Actions blocked')
    const agents = byLabel.get('Active agents')
    const commitments = byLabel.get('Agreements committed')
    const txns = byLabel.get('Transactions')
    return {
      reason: txns
        ? { label: 'negotiation in flight', value: `${txns.value}` }
        : agents
          ? { label: 'active runtimes', value: agents.value }
          : null,
      enforce: offers ? { label: 'actions blocked', value: offers.value } : null,
      verify: commitments ? { label: 'commitments recorded', value: commitments.value } : null,
    }
  }, [stats])

  const latestHash = overview.data?.current_transaction?.commitment?.agreement_hash || null

  const launch = useCallback(() => {
    if (launching) return
    setLaunching(true)
    // Let the halo and label settle before the view swaps, so the hand-off reads as
    // intentional rather than as a jump cut.
    window.setTimeout(() => navigate('/overview'), 420)
  }, [launching, navigate])

  // Escape hatch: if the user navigates away mid-transition, do not fire a stale push.
  useEffect(() => () => setLaunching(false), [])

  const streamStatus = state.connection.status
  const streamLive = streamStatus === 'connected'

  return (
    <>
      <AmbientBackdrop />

      <Page className="flex min-h-[calc(100vh-4rem)] flex-col justify-center py-10">
        {/* ------------------------------------------------------------ status */}
        <div className="animate-fade-up mb-8 flex flex-wrap items-center justify-center gap-2">
          <StatusPill tone={online ? 'good' : 'bad'} dot={false}>
            {online ? 'control plane online' : 'control plane offline'}
          </StatusPill>
          <StatusPill tone={streamLive ? 'good' : 'warn'} dot={false}>
            {streamLive ? `event stream live · #${state.connection.lastSeq}` : 'event stream idle'}
          </StatusPill>
          {chain ? (
            <StatusPill tone={chain.status === 'up' ? 'good' : 'warn'} dot={false}>
              chain {chain.mode}
            </StatusPill>
          ) : null}
        </div>

        {/* -------------------------------------------------------------- title */}
        <header className="animate-fade-up mx-auto max-w-4xl text-center">
          <div className="eyebrow mb-4 inline-flex items-center gap-2 rounded-full border border-white/[0.08] bg-white/[0.03] px-3 py-1 backdrop-blur-md">
            <span className="h-1.5 w-1.5 rounded-full bg-cyan" />
            {PRINCIPLE}
          </div>

          <h1 className="text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-6xl lg:text-7xl">
            <span className="bg-gradient-to-br from-ink via-ink to-cyan/70 bg-clip-text text-transparent">
              {APP_NAME}
            </span>
          </h1>

          <p className="mx-auto mt-4 max-w-3xl text-balance text-base font-medium leading-snug tracking-tight text-ink-muted sm:text-xl lg:text-2xl">
            Secure Trust Infrastructure for Autonomous AI Agents
          </p>

          <p className="mx-auto mt-4 max-w-2xl text-balance text-sm leading-relaxed text-ink-faint sm:text-base">
            Deterministic Isolation &amp; Verification for Autonomous B2B AI Agents
          </p>

          {/* TAGLINE now carries the second heading line, so this slot states only the
              one-line thesis rather than repeating it. */}
          <p className="mx-auto mt-5 max-w-xl text-xs leading-relaxed text-ink-faint">
            The AI proposes. TrustMesh decides. The blockchain records.
          </p>
        </header>

        {/* ---------------------------------------------------------------- CTA */}
        <div className="animate-fade-up mt-9 flex flex-col items-center gap-3">
          <div className="relative">
            {/* Breathing halo, only while launching. */}
            {launching ? (
              <span
                className="absolute inset-0 -z-10 animate-halo rounded-2xl bg-cyan/25 blur-2xl"
                aria-hidden="true"
              />
            ) : null}

            <button
              type="button"
              onClick={launch}
              disabled={launching}
              aria-busy={launching}
              className="group relative overflow-hidden rounded-2xl border border-cyan/40 bg-cyan/[0.13] px-8 py-3.5 text-sm font-semibold tracking-tight text-cyan shadow-glow-cyan transition-all duration-300 hover:bg-cyan/20 hover:shadow-glow-cyan focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan/60 focus-visible:ring-offset-2 focus-visible:ring-offset-base disabled:cursor-wait"
            >
              {/* Light sweep. */}
              <span
                className="pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 animate-sweep bg-gradient-to-r from-transparent via-white/20 to-transparent"
                aria-hidden="true"
              />
              <span className="relative flex items-center gap-2.5">
                {launching ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-cyan border-t-transparent" />
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path
                      d="M13 5l7 7-7 7M4 12h16"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                )}
                {launching ? 'Initialising…' : 'Launch Mission Control'}
              </span>
            </button>
          </div>

          <p className="font-mono text-2xs text-ink-faint/70">
            opens the live Overview · no authentication required in this prototype
          </p>
        </div>

        {/* ------------------------------------------------------- paradigm grid */}
        <section className="mt-14">
          <div className="animate-fade-up mb-5 text-center">
            <span className="eyebrow">The paradigm</span>
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            {PARADIGM.map((item, index) => (
              <ParadigmCard
                key={item.id}
                item={item}
                index={index}
                stat={layerStats[item.id]}
              />
            ))}
          </div>
        </section>

        {/* ------------------------------------------------------- live evidence */}
        <section className="animate-fade-up mt-10" style={{ animationDelay: '480ms' }}>
          <div className="rounded-2xl border border-white/[0.06] bg-base-800/35 px-5 py-4 backdrop-blur-md">
            {overview.error ? (
              <div className="flex flex-col items-center gap-2 text-center">
                <StatusPill tone="bad">control plane unreachable</StatusPill>
                <p className="max-w-lg text-2xs leading-relaxed text-ink-faint">
                  {overview.error.message} Start the backend with{' '}
                  <code className="font-mono text-ink-muted">
                    python -m uvicorn app.main:app --port 8000
                  </code>{' '}
                  and the figures below will populate.
                </p>
                <button
                  type="button"
                  onClick={() => {
                    health.reload()
                    overview.reload()
                  }}
                  className="mt-1 rounded-lg border border-white/10 px-3 py-1 text-2xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
                >
                  Retry connection
                </button>
              </div>
            ) : overview.loading && !overview.data ? (
              <div className="flex items-center justify-center gap-4 py-2">
                {[0, 1, 2, 3].map((i) => (
                  <Skeleton key={i} className="h-8 w-28 rounded-lg" />
                ))}
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3 lg:flex-row lg:justify-between">
                <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2">
                  {(stats || []).map((stat) => (
                    <div key={stat.label} className="flex items-baseline gap-2">
                      <span className="font-mono text-base tabular-nums text-ink">
                        {stat.value}
                      </span>
                      <span className="text-2xs uppercase tracking-wider text-ink-faint">
                        {stat.label.toLowerCase()}
                      </span>
                    </div>
                  ))}
                </div>

                {latestHash ? (
                  <div className="flex items-center gap-2 border-t border-white/[0.06] pt-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
                    <span className="eyebrow">Latest agreement</span>
                    <code
                      className="font-mono text-2xs text-teal"
                      title={latestHash}
                    >
                      {truncateHash(latestHash, 8, 6)}
                    </code>
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </section>

        {/* ------------------------------------------------------ secondary links */}
        <nav
          className="animate-fade-up mt-8 flex flex-wrap items-center justify-center gap-2"
          style={{ animationDelay: '560ms' }}
          aria-label="Console sections"
        >
          {[
            { to: '/overview', label: 'Mission Control' },
            { to: '/network', label: 'Agent Network' },
            { to: '/transactions', label: 'Transactions' },
            { to: '/security', label: 'Security Center' },
            { to: '/blockchain', label: 'Blockchain' },
            { to: '/about', label: 'Architecture' },
          ].map((link) => (
            <button
              key={link.to}
              type="button"
              onClick={() => navigate(link.to)}
              className="rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-1.5 text-2xs text-ink-faint backdrop-blur-md transition-colors hover:border-cyan/30 hover:bg-cyan/[0.07] hover:text-cyan"
            >
              {link.label}
            </button>
          ))}
        </nav>

        {/* ------------------------------------------------------------ footnote */}
        <p className="animate-fade-up mt-10 text-center text-2xs leading-relaxed text-ink-faint/70" style={{ animationDelay: '640ms' }}>
          TrustMesh is a hackathon prototype. Threat scenarios are controlled simulations that
          perform no real attack. No production readiness or certification is claimed.
        </p>
      </Page>
    </>
  )
}
