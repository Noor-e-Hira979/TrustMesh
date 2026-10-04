/**
 * Security Center (spec §20).
 *
 * A live stream of security events. Each shows severity, agent, organization, the
 * requested action, the decision, the reason and the timestamp — and the three mandated
 * scenarios can be re-triggered on demand so a presenter controls the pacing.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { SCENARIOS, SEVERITY_META } from '../lib/constants'
import { pluralize } from '../lib/format'
import { useApi, useMutation } from '../hooks/useApi'
import { useGlobalEvents } from '../state/GlobalEventContext'
import { ActionButton, Page, PageHeader } from '../components/layout/Layout'
import {
  CardHeader,
  GlassCard,
  JsonBlock,
  StatCard,
  StatusPill,
} from '../components/ui/Primitives'
import { DisconnectedState, StateGate } from '../components/ui/States'
import { AlertCard, SecurityFeed } from '../components/security/SecurityFeed'

const SEVERITY_FILTERS = ['ALL', 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

export default function Security() {
  const { state, dispatch } = useGlobalEvents()
  const [severity, setSeverity] = useState('ALL')
  const [lastScenario, setLastScenario] = useState(null)

  const alertsQuery = useApi(
    () => api.listAlerts({ limit: 60, ...(severity === 'ALL' ? {} : { severity }) }),
    [severity],
    { pollMs: 6000 },
  )
  const statsQuery = useApi(() => api.getSecurityStats(), [], { pollMs: 8000 })
  const rulesQuery = useApi(() => api.getSecurityRules(), [])

  const scenario = useMutation((name) => api.runScenario(name))
  const triage = useMutation(({ id, next }) => api.setAlertStatus(id, next))

  // Live alerts take precedence over the polled snapshot so the stream feels instant.
  const liveAlerts = useMemo(() => {
    const polled = alertsQuery.data?.items || []
    const seen = new Set(polled.map((a) => a.id))
    const fresh = state.alerts.filter((a) => !seen.has(a.id))
    return [...fresh, ...polled]
  }, [alertsQuery.data, state.alerts])

  useEffect(() => {
    if (scenario.result) setLastScenario(scenario.result)
  }, [scenario.result])

  const onScenario = async (name) => {
    const result = await scenario.mutate(name)
    setLastScenario(result)
    alertsQuery.reload()
    statsQuery.reload()
  }

  const onTriage = async (id, next) => {
    await triage.mutate({ id, next })
    alertsQuery.reload()
  }

  const connection = state.connection.status
  const isStale = ['disconnected', 'reconnecting', 'stale'].includes(connection)
  const stats = statsQuery.data

  return (
    <Page>
      <PageHeader
        eyebrow="Enforcement made visible"
        title="Security Center"
        subtitle="TrustMesh does not ask a language model whether a request is safe. It checks identity, credential, capability, authorization and deterministic policy — then it allows or blocks, and records why."
        right={
          <ActionButton tone="ghost" onClick={alertsQuery.reload} pending={alertsQuery.loading}>
            Refresh
          </ActionButton>
        }
      />

      {isStale ? (
        <DisconnectedState status={connection} attempt={state.connection.attempt} className="mb-4" />
      ) : null}

      {/* Headline numbers */}
      <section className="mb-5">
        <StateGate
          loading={statsQuery.loading && !stats}
          error={statsQuery.error}
          isEmpty={!stats}
          onRetry={statsQuery.reload}
          loadingFallback={
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="shimmer h-24 rounded-2xl" />
              ))}
            </div>
          }
        >
          {stats ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <StatCard
                label="Security events"
                value={String(stats.total)}
                detail={pluralize(stats.open_alerts, 'open')}
              />
              <StatCard
                label="Actions blocked"
                value={String(stats.blocked_actions)}
                detail="never executed"
                tone="bad"
              />
              <StatCard
                label="Critical"
                value={String(stats.by_severity?.CRITICAL ?? 0)}
                detail="capability + credential solicitation"
                tone={stats.by_severity?.CRITICAL ? 'bad' : 'neutral'}
              />
              <StatCard
                label="Simulated"
                value={String(stats.simulated_alerts ?? 0)}
                detail="controlled demonstrations"
                tone="warn"
              />
            </div>
          ) : null}
        </StateGate>
      </section>

      {/* Mandated scenarios */}
      <GlassCard className="mb-5 p-4">
        <CardHeader
          eyebrow="Mandated demonstrations"
          title="Spec §5 scenarios"
          subtitle="Each button submits a scripted unauthorised request to the real gateway. Nothing here performs a real attack — these are controlled simulations."
        />
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          {SCENARIOS.map((item) => (
            <div
              key={item.key}
              className="rounded-xl border border-white/[0.06] bg-base-900/40 p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-2xs text-ink-faint">{item.spec}</span>
                <StatusPill tone="bad">BLOCKED</StatusPill>
              </div>
              <p className="mt-2 text-xs font-medium text-ink-muted">{item.title}</p>
              <p className="mt-1 text-2xs leading-relaxed text-ink-faint">{item.detail}</p>
              <p className="mt-1.5 text-2xs leading-relaxed text-red/80">{item.expect}</p>
              <button
                type="button"
                disabled={scenario.pending}
                onClick={() => onScenario(item.key)}
                className="mt-3 w-full rounded-lg border border-red/25 bg-red/[0.08] px-3 py-1.5 text-2xs font-medium text-red transition-colors hover:bg-red/15 disabled:opacity-40"
              >
                {scenario.pending ? 'submitting…' : 'Trigger scenario'}
              </button>
            </div>
          ))}
        </div>

        {scenario.error ? (
          <p className="mt-3 text-2xs text-red">{scenario.error.message}</p>
        ) : null}

        {lastScenario ? (
          <div
            className={`mt-3 rounded-xl border px-3 py-2.5 ${
              lastScenario.decision === 'BLOCK'
                ? 'border-red/25 bg-red/[0.06]'
                : 'border-amber/25 bg-amber/[0.06]'
            }`}
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="eyebrow">{lastScenario.scenario}</span>
              <StatusPill tone={lastScenario.decision === 'BLOCK' ? 'bad' : 'warn'}>
                {lastScenario.decision}
              </StatusPill>
              {lastScenario.rule_id ? (
                <span className="font-mono text-2xs text-red/85">{lastScenario.rule_id}</span>
              ) : null}
              {lastScenario.severity ? <StatusPill tone="warn">{lastScenario.severity}</StatusPill> : null}
            </div>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">{lastScenario.reason}</p>
            {lastScenario.explanation ? (
              <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
                {lastScenario.explanation}
              </p>
            ) : null}
            {lastScenario.evidence ? (
              <JsonBlock value={lastScenario.evidence} label="Gateway evidence" className="mt-2" />
            ) : null}
          </div>
        ) : null}
      </GlassCard>

      <div className="grid gap-4 xl:grid-cols-3">
        {/* Alert stream */}
        <div className="xl:col-span-2">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <span className="eyebrow">Live alert stream</span>
            <div className="flex flex-wrap gap-1">
              {SEVERITY_FILTERS.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setSeverity(option)}
                  className={`rounded-lg border px-2 py-0.5 text-2xs font-medium transition-colors ${
                    severity === option
                      ? 'border-cyan/30 bg-cyan/12 text-cyan'
                      : 'border-white/10 text-ink-faint hover:border-white/20 hover:text-ink-muted'
                  }`}
                >
                  {option === 'ALL' ? 'all' : option.toLowerCase()}
                  {option !== 'ALL' && stats?.by_severity?.[option] ? (
                    <span className="ml-1 font-mono">{stats.by_severity[option]}</span>
                  ) : null}
                </button>
              ))}
            </div>
          </div>

          <SecurityFeed
            alerts={liveAlerts}
            loading={alertsQuery.loading && !alertsQuery.data}
            error={alertsQuery.error}
            onRetry={alertsQuery.reload}
            onStatusChange={onTriage}
            pendingStatus={triage.pending}
          />
        </div>

        {/* Rule catalogue */}
        <div className="space-y-4">
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Enforcement surface"
              title="Deterministic rules"
              subtitle="Everything TrustMesh can block on, served from the backend so this list cannot drift from the code."
            />
            <StateGate
              loading={rulesQuery.loading && !rulesQuery.data}
              error={rulesQuery.error}
              isEmpty={!rulesQuery.data?.policy_rules}
              onRetry={rulesQuery.reload}
              loadingFallback={<div className="shimmer mt-3 h-64 rounded-xl" />}
              className="mt-3"
            >
              <div className="space-y-3">
                <div>
                  <div className="eyebrow mb-1.5">Policy engine</div>
                  <ul className="space-y-1">
                    {(rulesQuery.data?.policy_rules || []).map((rule) => (
                      <li
                        key={rule.rule_id}
                        className="rounded-lg border border-cyan/20 bg-cyan/[0.05] px-2.5 py-1.5"
                      >
                        <div className="font-mono text-2xs text-cyan">{rule.rule_id}</div>
                        <div className="mt-0.5 font-mono text-2xs text-ink-faint">
                          {rule.expression}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>

                <div>
                  <div className="eyebrow mb-1.5">Threat interception</div>
                  <ul className="space-y-1">
                    {(rulesQuery.data?.threat_rules || []).map((rule) => (
                      <li
                        key={rule.code}
                        className="flex items-start gap-2 rounded-lg border border-white/[0.06] px-2.5 py-1.5"
                      >
                        <StatusPill tone={SEVERITY_META[rule.severity] ? 'bad' : 'neutral'}>
                          {rule.severity.slice(0, 4)}
                        </StatusPill>
                        <div className="min-w-0">
                          <div className="font-mono text-2xs text-ink-muted">{rule.code}</div>
                          <p className="mt-0.5 text-2xs leading-relaxed text-ink-faint">
                            {rule.description}
                          </p>
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>

                <div>
                  <div className="eyebrow mb-1.5">Never grantable capabilities</div>
                  <div className="flex flex-wrap gap-1.5">
                    {(rulesQuery.data?.sentinel_capabilities || []).map((capability) => (
                      <span
                        key={capability}
                        className="rounded border border-red/25 bg-red/[0.07] px-1.5 py-0.5 font-mono text-2xs text-red/85"
                      >
                        {capability}
                      </span>
                    ))}
                  </div>
                  <p className="mt-1.5 text-2xs leading-relaxed text-ink-faint">
                    Defence in depth: a capability on this list is refused even if a registry
                    entry were somehow corrupted to include it.
                  </p>
                </div>
              </div>
            </StateGate>
          </GlassCard>

          <GlassCard className="p-4">
            <CardHeader eyebrow="Scope" title="What this page proves" />
            <ul className="mt-3 space-y-2 text-2xs leading-relaxed text-ink-faint">
              <li>
                A capability an agent was never granted cannot be exercised, regardless of how
                the request is phrased.
              </li>
              <li>
                A price above the authorized ceiling is refused by a rule, not by a model's
                judgement.
              </li>
              <li>
                An unregistered agent cannot represent an organization, no matter how good its
                offer is.
              </li>
              <li>
                A blocked request never executes, and the legitimate transaction continues
                unaffected.
              </li>
            </ul>
          </GlassCard>
        </div>
      </div>
    </Page>
  )
}
