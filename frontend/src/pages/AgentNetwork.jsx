/**
 * Agent Network page (spec §16) — the visual signature of the project.
 *
 * The graph is live: node activity and edge state update from the SSE stream as the
 * demonstration runs, so a presenter can watch verification, negotiation and blocks
 * light up in sequence.
 */

import { useMemo } from 'react'
import { api } from '../lib/api'
import { useAgents, useMutation } from '../hooks/useApi'
import { useNetworkGraph } from '../hooks/useNetworkGraph'
import { useGlobalEvents } from '../state/GlobalEventContext'
import { Page, PageHeader, ActionButton } from '../components/layout/Layout'
import { CardHeader, GlassCard, StatCard, StatusPill } from '../components/ui/Primitives'
import { DisconnectedState, Skeleton, StateGate } from '../components/ui/States'
import AgentNetworkGraph, { AgentInspector } from '../components/network/AgentNetworkGraph'

export default function AgentNetwork() {
  const { data, error, loading, reload } = useAgents()
  const { state } = useGlobalEvents()
  const scenario = useMutation((name) => api.runScenario(name))

  const agents = data?.[0] || []
  const catalogue = data?.[1]?.agents || []

  const graph = useNetworkGraph({ agents, catalogue })
  const { stats } = graph

  const connection = state.connection.status
  const isStale = ['disconnected', 'reconnecting', 'stale'].includes(connection)

  const edgeSummary = useMemo(
    () =>
      Object.entries(stats.byState)
        .filter(([, count]) => count > 0)
        .map(([stateName, count]) => ({ state: stateName, count })),
    [stats.byState],
  )

  return (
    <Page>
      <PageHeader
        eyebrow="Cross-organizational trust"
        title="Agent Network"
        subtitle="Every node is an autonomous agent belonging to a specific organization. Edge colour is the interaction state; the ring is the verification result. Supplier C and the unregistered impostor are controlled simulations used to demonstrate enforcement."
        right={
          <ActionButton tone="ghost" onClick={reload} pending={loading && Boolean(data)}>
            Refresh
          </ActionButton>
        }
      />

      {isStale ? (
        <DisconnectedState status={connection} attempt={state.connection.attempt} className="mb-4" />
      ) : null}

      <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="Nodes" value={String(stats.nodeCount)} detail={`${stats.registered} registered`} />
        <StatCard
          label="Simulations"
          value={String(stats.simulations)}
          detail="controlled, non-operational"
          tone="warn"
        />
        <StatCard
          label="Connections"
          value={String(stats.edgeCount)}
          detail={edgeSummary.map((e) => `${e.count} ${e.state}`).join(' · ') || 'all idle'}
          tone="accent"
        />
        <StatCard
          label="Event stream"
          value={String(state.connection.lastSeq)}
          detail={`seq ${connection}`}
          tone={isStale ? 'warn' : 'good'}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <GlassCard className="xl:col-span-2">
          <CardHeader
            eyebrow="Live topology"
            title="Buyer ↔ Suppliers"
            subtitle="Edges animate while an interaction is active. Select any node for its authority envelope."
            right={
              <div className="flex flex-wrap gap-1.5">
                {['unauthorized_capability', 'policy_violation', 'identity_mismatch'].map((name) => (
                  <button
                    key={name}
                    type="button"
                    disabled={scenario.pending}
                    onClick={() => scenario.mutate(name)}
                    className="rounded border border-white/10 px-2 py-0.5 text-2xs text-ink-faint transition-colors hover:border-red/30 hover:text-red disabled:opacity-40"
                    title="Trigger this mandated security scenario against the live gateway"
                  >
                    {name === 'unauthorized_capability'
                      ? 'scenario 1'
                      : name === 'policy_violation'
                        ? 'scenario 2'
                        : 'scenario 3'}
                  </button>
                ))}
              </div>
            }
          />

          <StateGate
            loading={loading && !data}
            error={error}
            isEmpty={stats.nodeCount === 0}
            onRetry={reload}
            loadingFallback={<Skeleton className="mt-4 h-[520px] w-full rounded-xl" />}
            emptyTitle="No agents registered"
            emptyDetail="The registry seeds Buyer, Supplier A, Supplier B and Supplier C on first boot."
            className="mt-4"
          >
            <AgentNetworkGraph graph={graph} />
          </StateGate>

          {scenario.error ? (
            <p className="mt-3 text-2xs text-red">{scenario.error.message}</p>
          ) : null}
          {scenario.result ? (
            <div className="mt-3 rounded-lg border border-white/[0.06] bg-base-900/50 px-3 py-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="eyebrow">{scenario.result.scenario}</span>
                <StatusPill tone="bad">{scenario.result.decision}</StatusPill>
                <span className="font-mono text-2xs text-red/80">{scenario.result.rule_id}</span>
              </div>
              <p className="mt-1.5 text-2xs leading-relaxed text-ink-faint">{scenario.result.reason}</p>
            </div>
          ) : null}
        </GlassCard>

        <div className="space-y-4">
          <AgentInspector node={graph.selected} />

          <GlassCard className="p-4">
            <CardHeader eyebrow="Roster" title="Registered agents" />
            <ul className="mt-3 space-y-1.5">
              {agents.map((agent) => {
                const live = state.agents[agent.id]
                return (
                  <li key={agent.id}>
                    <button
                      type="button"
                      onClick={() => graph.setSelectedId(agent.id)}
                      className={`flex w-full items-center justify-between gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
                        graph.selectedId === agent.id
                          ? 'border-cyan/30 bg-cyan/[0.07]'
                          : 'border-white/[0.06] hover:border-white/15'
                      }`}
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-xs text-ink-muted">
                          {agent.display_name}
                        </span>
                        <span className="block truncate font-mono text-2xs text-ink-faint">
                          {agent.org_id}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        {agent.is_malicious_simulation ? (
                          <StatusPill tone="warn" title="Controlled simulation">
                            sim
                          </StatusPill>
                        ) : null}
                        <StatusPill
                          tone={
                            live?.verification === 'verified' || agent.verification_status === 'verified'
                              ? 'good'
                              : live?.verification === 'rejected'
                                ? 'bad'
                                : 'neutral'
                          }
                          dot={false}
                        >
                          {(live?.verification || agent.verification_status || 'unverified').slice(0, 4)}
                        </StatusPill>
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </GlassCard>

          <GlassCard className="p-4">
            <CardHeader eyebrow="Unregistered" title="Excluded from the registry" />
            <ul className="mt-3 space-y-1.5">
              {catalogue
                .filter((entry) => !entry.registered)
                .map((entry) => (
                  <li
                    key={entry.id}
                    className="rounded-lg border border-amber/25 bg-amber/[0.05] px-2.5 py-2"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-2xs text-amber">{entry.id}</span>
                      <StatusPill tone="bad">rejected</StatusPill>
                    </div>
                    <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
                      Claims to represent {entry.org_id}. Because it is genuinely not in the
                      registry, identity verification fails before any capability check runs.
                    </p>
                  </li>
                ))}
            </ul>
          </GlassCard>
        </div>
      </div>
    </Page>
  )
}
