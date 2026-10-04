/**
 * Overview dashboard (spec §18).
 *
 * A judge should understand what the system does within seconds, so the page answers
 * in this order: what the four subsystems are, what just happened, what is blocked, and
 * what got committed. Everything comes from `/overview` in one round-trip, then stays
 * live via the SSE stream.
 */

import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { formatRelative, formatUsd, truncateHash } from '../lib/format'
import { TRANSACTION_STATE_LABELS, TRANSACTION_STATE_TONES } from '../lib/constants'
import { useMutation, useOverview } from '../hooks/useApi'
import { useGlobalEvents } from '../state/GlobalEventContext'
import { ActionButton, Page, PageHeader } from '../components/layout/Layout'
import {
  CardHeader,
  ComponentCard,
  DataRow,
  GlassCard,
  HashChip,
  StatCard,
  StatusPill,
} from '../components/ui/Primitives'
import { DisconnectedState, EmptyState, Skeleton, StateGate } from '../components/ui/States'
import { ActivityFeed } from '../components/security/ActivityFeed'

const COMPONENT_ORDER = ['ai_reasoning', 'policy_engine', 'trustmesh', 'blockchain']

export default function Overview() {
  const { state, dispatch } = useGlobalEvents()
  const { data, error, loading, reload, lastUpdated } = useOverview()
  const runDemo = useMutation(() => api.runDemo({ step_delay_seconds: 0.5, use_ai: true }))

  const components = useMemo(() => {
    if (!data?.components) return []
    const byName = new Map(data.components.map((c) => [c.name, c]))
    return COMPONENT_ORDER.map((name) => byName.get(name)).filter(Boolean)
  }, [data])

  const connection = state.connection.status
  const isStale = ['disconnected', 'reconnecting', 'stale', 'connecting'].includes(connection)

  const txn = data?.current_transaction
  const commitment = txn?.commitment

  return (
    <Page>
      <PageHeader
        eyebrow="TrustMesh control plane"
        title="Overview"
        subtitle="Autonomous agents from different organizations negotiate. TrustMesh verifies identity, capability and authorization, applies a deterministic policy engine, and commits the resulting agreement hash to a public chain."
        right={
          <>
            <ActionButton
              tone="ghost"
              onClick={reload}
              pending={loading && Boolean(data)}
              title="Refresh the dashboard snapshot"
            >
              Refresh
            </ActionButton>
            <ActionButton onClick={() => runDemo.mutate()} pending={runDemo.pending}>
              Run demonstration
            </ActionButton>
          </>
        }
      />

      {isStale ? (
        <DisconnectedState
          status={connection}
          attempt={state.connection.attempt}
          className="mb-4"
          onRetry={reload}
        />
      ) : null}

      {runDemo.error ? (
        <GlassCard className="mb-4 border-red/25 p-3">
          <p className="text-xs text-red">{runDemo.error.message}</p>
        </GlassCard>
      ) : null}

      {/* §18 status cards: AI / Gemini, Trust Engine, Policy Engine, Blockchain */}
      <section className="mb-5">
        <div className="mb-2.5 flex items-center justify-between gap-3">
          <span className="eyebrow">Subsystems</span>
          {lastUpdated ? (
            <span className="font-mono text-2xs text-ink-faint/70">
              snapshot {formatRelative(lastUpdated)}
            </span>
          ) : null}
        </div>
        <StateGate
          loading={loading && !data}
          error={error}
          isEmpty={components.length === 0}
          onRetry={reload}
          loadingFallback={
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-28 rounded-2xl" />
              ))}
            </div>
          }
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {components.map((component) => (
              <ComponentCard
                key={component.name}
                name={component.name.replace(/_/g, ' ')}
                status={component.status}
                detail={component.detail}
                mode={component.mode}
              />
            ))}
          </div>
        </StateGate>
      </section>

      {/* Headline numbers */}
      <section className="mb-5">
        <StateGate
          loading={loading && !data}
          error={error}
          isEmpty={false}
          onRetry={reload}
          loadingFallback={
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-24 rounded-2xl" />
              ))}
            </div>
          }
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            {(data?.stats || []).map((stat) => (
              <StatCard
                key={stat.label}
                label={stat.label}
                value={stat.value}
                detail={stat.detail}
                tone={stat.tone}
              />
            ))}
          </div>
        </StateGate>
      </section>

      <div className="grid gap-4 xl:grid-cols-3">
        {/* Left: current transaction + recent alerts */}
        <div className="space-y-4 xl:col-span-2">
          {/* Current transaction */}
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Current transaction"
              title={txn ? txn.product : 'No transaction yet'}
              subtitle={
                txn
                  ? `${txn.quantity.toLocaleString()} units · budget ${formatUsd(txn.budget_cents)} · delivery ≤ ${txn.max_delivery_days} days`
                  : 'Start the demonstration to create one.'
              }
              right={
                txn ? (
                  <StatusPill tone={TRANSACTION_STATE_TONES[txn.state] || 'neutral'}>
                    {TRANSACTION_STATE_LABELS[txn.state] || txn.state}
                  </StatusPill>
                ) : null
              }
            />

            {txn ? (
              <>
                <div className="mt-3 divide-y divide-white/[0.05]">
                  <DataRow label="Transaction" value={txn.id} mono />
                  <DataRow label="Intent ref" value={txn.intent_ref} mono />
                  <DataRow
                    label="Buyer"
                    value={`${txn.buyer_agent_id} · ${txn.buyer_org_id}`}
                    mono
                  />
                  <DataRow
                    label="Supplier"
                    value={txn.supplier_agent_id ? `${txn.supplier_agent_id} · ${txn.supplier_org_id}` : 'not yet agreed'}
                    mono
                  />
                  <DataRow label="Negotiation" value={`round ${txn.negotiation_round}`} mono />
                  <DataRow label="Actions blocked" value={String(txn.block_count)} mono />
                  <DataRow label="Offers recorded" value={String(txn.offers?.length ?? 0)} mono />
                  <DataRow label="State" value={txn.state} mono />
                </div>

                {commitment ? (
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-white/[0.06] bg-base-900/50 px-3 py-2">
                    <span className="eyebrow">Agreement hash</span>
                    <HashChip value={commitment.agreement_hash} />
                    <StatusPill
                      tone={
                        commitment.status === 'CONFIRMED'
                          ? 'good'
                          : commitment.status === 'FAILED'
                            ? 'bad'
                            : 'warn'
                      }
                    >
                      {commitment.status.toLowerCase()}
                    </StatusPill>
                  </div>
                ) : null}

                <div className="mt-3 flex flex-wrap gap-2">
                  <Link
                    to="/transactions"
                    className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
                  >
                    Open transaction →
                  </Link>
                  {commitment ? (
                    <Link
                      to="/blockchain"
                      className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
                    >
                      Verify commitment →
                    </Link>
                  ) : null}
                </div>
              </>
            ) : (
              <EmptyState
                title="No transaction in flight"
                detail="Press “Run demonstration” to walk the buyer intent through discovery, identity verification, negotiation, two blocked attacks and a committed agreement."
              />
            )}
          </GlassCard>

          {/* Recent security events */}
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Recent security events"
              title="Blocked actions"
              subtitle="Every one of these was refused by TrustMesh before execution."
              right={
                <Link
                  to="/security"
                  className="rounded-lg border border-white/10 px-2.5 py-1 text-2xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
                >
                  Security Center →
                </Link>
              }
            />
            <StateGate
              loading={loading && !data}
              error={error}
              isEmpty={(data?.recent_alerts || []).length === 0}
              onRetry={reload}
              emptyTitle="Nothing blocked yet"
              emptyDetail="Blocked actions will appear here the moment the Policy Engine refuses one."
              className="mt-3"
            >
              <ul className="space-y-1.5">
                {(data?.recent_alerts || []).slice(0, 6).map((alert) => (
                  <li
                    key={alert.id}
                    className="flex flex-wrap items-center gap-x-2 gap-y-1 border-l-2 border-red/40 pl-3"
                  >
                    <StatusPill tone={alert.severity === 'CRITICAL' ? 'bad' : 'warn'}>
                      {alert.severity}
                    </StatusPill>
                    <span className="font-mono text-2xs text-ink-muted">{alert.agent_id}</span>
                    <span className="min-w-0 flex-1 truncate text-2xs text-ink-faint">
                      {alert.reason}
                    </span>
                    <time className="font-mono text-2xs text-ink-faint/60">
                      {formatRelative(alert.ts)}
                    </time>
                  </li>
                ))}
              </ul>
            </StateGate>
          </GlassCard>

          {/* Recent commitments */}
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Recent commitments"
              title="Blockchain"
              subtitle="SHA-256 over the RFC 8785 canonical agreement. No sensitive data on-chain."
              right={
                <Link
                  to="/blockchain"
                  className="rounded-lg border border-white/10 px-2.5 py-1 text-2xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
                >
                  Explorer →
                </Link>
              }
            />
            <StateGate
              loading={loading && !data}
              error={error}
              isEmpty={(data?.recent_commitments || []).length === 0}
              onRetry={reload}
              emptyTitle="No commitments yet"
              emptyDetail="A commitment is created once final terms are agreed and hashed."
              className="mt-3"
            >
              <ul className="space-y-1.5">
                {(data?.recent_commitments || []).map((commitmentRow) => (
                  <li
                    key={commitmentRow.id}
                    className="flex flex-wrap items-center gap-x-2 gap-y-1 border-l-2 border-teal/40 pl-3"
                  >
                    <HashChip value={commitmentRow.agreement_hash} />
                    <StatusPill
                      tone={
                        commitmentRow.status === 'CONFIRMED'
                          ? 'good'
                          : commitmentRow.status === 'FAILED'
                            ? 'bad'
                            : 'warn'
                      }
                    >
                      {commitmentRow.status.toLowerCase()}
                    </StatusPill>
                    <time className="ml-auto font-mono text-2xs text-ink-faint/60">
                      {formatRelative(commitmentRow.created_at)}
                    </time>
                  </li>
                ))}
              </ul>
            </StateGate>
          </GlassCard>
        </div>

        {/* Right: live feed */}
        <div className="space-y-4">
          <ActivityFeed frames={state.feed} limit={40} className="max-h-[720px]" />

          <GlassCard className="p-4">
            <CardHeader eyebrow="How it works" title="REASON → ENFORCE → VERIFY" />
            <ol className="mt-3 space-y-2 text-2xs leading-relaxed text-ink-faint">
              <li>
                <span className="font-medium text-violet">AI</span> proposes offers and
                counter-offers. It never approves its own action.
              </li>
              <li>
                <span className="font-medium text-cyan">TrustMesh</span> verifies identity,
                credential, capability and authorization, then applies a deterministic
                policy engine that emits ALLOW or BLOCK.
              </li>
              <li>
                <span className="font-medium text-teal">Blockchain</span> records a SHA-256
                digest of the RFC 8785 canonical agreement so anyone can verify it later.
              </li>
            </ol>
            <Link
              to="/about"
              className="mt-3 inline-block rounded-lg border border-white/10 px-2.5 py-1 text-2xs text-ink-muted transition-colors hover:border-cyan/30 hover:text-cyan"
            >
              Architecture & limits →
            </Link>
          </GlassCard>
        </div>
      </div>
    </Page>
  )
}
