/**
 * About page.
 *
 * Explains the architecture honestly, including what this prototype does *not* claim.
 * The `/health/architecture` and `/security/rules` payloads are rendered directly, so the
 * documented pipeline is the one the code actually runs.
 */

import { api } from '../lib/api'
import { LAYER_META, PRINCIPLE } from '../lib/constants'
import { useApi } from '../hooks/useApi'
import { Page, PageHeader } from '../components/layout/Layout'
import { CardHeader, GlassCard, LayerBadge, StatusPill } from '../components/ui/Primitives'
import { Skeleton, StateGate } from '../components/ui/States'

const PIPELINE_STAGES = [
  {
    stage: 'schema',
    title: 'Schema validation',
    detail:
      'The action request is validated before anything else. A malformed request is refused at the boundary rather than reaching the enforcement logic.',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'identity',
    title: 'Identity verification',
    detail:
      'Is the agent in the registry, and does the organization it claims to represent match the registered binding? An unknown agent is refused here.',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'credential',
    title: 'Credential verification',
    detail:
      'Is the presented credential active, unexpired, unrevoked and bound to this agent and organization? Comparison is constant-time against an HMAC digest.',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'capability',
    title: 'Capability validation',
    detail:
      'Is the requested capability inside the agent’s declared grant list? A protected capability is refused even if a registry entry were corrupted to include it.',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'authorization',
    title: 'Authorization',
    detail:
      'Is this agent allowed to perform this action, on this resource, within its organization scope?',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'threat',
    title: 'Threat interception',
    detail:
      'Named deterministic rules screen for credential solicitation, instruction override, cross-tenant access, constraint tampering and impersonation.',
    layer: 'TRUSTMESH',
  },
  {
    stage: 'policy',
    title: 'Deterministic policy',
    detail:
      'Budget ceiling, delivery SLA, certification level, quantity match and counterparty eligibility. This stage — not the model — returns ALLOW or BLOCK.',
    layer: 'TRUSTMESH',
  },
]

const NOT_BUILT = [
  'Cryptocurrency, tokens, NFTs, DAO mechanics or payments',
  'On-chain negotiation content, prices or quantities',
  'Reputation algorithms or fabricated trust scores',
  'Machine-learned threat detection',
  'Kafka, Redis, Kubernetes or microservices',
  'Real credential theft, exploitation or destructive attack code',
  'A human authentication system — agent credentials are the primitive that matters',
]

export default function About() {
  const architecture = useApi(() => api.getArchitecture(), [])
  const health = useApi(() => api.getHealth(), [])

  const arch = architecture.data
  const stages = arch?.pipeline || []

  return (
    <Page>
      <PageHeader
        eyebrow="Architecture"
        title="About TrustMesh"
        subtitle="Secure trust infrastructure for autonomous AI agents that belong to different organizations and want to transact with each other."
      />

      {/* Principle */}
      <GlassCard className="mb-5 p-6">
        <div className="eyebrow">Core principle</div>
        <div className="mt-2 font-mono text-xl font-semibold tracking-tight text-ink sm:text-2xl">
          {PRINCIPLE}
        </div>
        <p className="mt-3 max-w-3xl text-xs leading-relaxed text-ink-muted">
          AI can negotiate. TrustMesh makes sure it stays within authority. Blockchain makes
          the commitment verifiable. Each layer has a distinct job, and only one of them can
          say yes.
        </p>

        <div className="mt-5 grid gap-3 md:grid-cols-3">
          {['AGENT', 'TRUSTMESH', 'BLOCKCHAIN'].map((layer) => (
            <div
              key={layer}
              className={`rounded-xl border p-3 ${
                layer === 'AGENT'
                  ? 'border-violet/25 bg-violet/[0.05]'
                  : layer === 'TRUSTMESH'
                    ? 'border-cyan/25 bg-cyan/[0.05]'
                    : 'border-teal/25 bg-teal/[0.05]'
              }`}
            >
              <LayerBadge
                layer={layer}
                label={LAYER_META[layer].label}
                question={LAYER_META[layer].question}
              />
              <p className="mt-2 text-2xs leading-relaxed text-ink-faint">
                {LAYER_META[layer].question}
              </p>
              {arch?.authority?.[layer.toLowerCase() === 'agent' ? 'ai' : layer.toLowerCase()] ? (
                <p className="mt-2 text-2xs leading-relaxed text-ink-muted">
                  {arch.authority[layer.toLowerCase() === 'agent' ? 'ai' : layer.toLowerCase()]}
                </p>
              ) : null}
            </div>
          ))}
        </div>
      </GlassCard>

      {/* The pipeline */}
      <GlassCard className="mb-5 p-4">
        <CardHeader
          eyebrow="Enforcement"
          title="The seven-stage gateway"
          subtitle="Every consequential action runs these stages in order. The pipeline fails closed: an unexpected error becomes a BLOCK, never an ALLOW."
          right={
            <StatusPill tone={health.data?.status === 'ok' ? 'good' : 'warn'}>
              control plane {health.data?.status || 'unknown'}
            </StatusPill>
          }
        />

        <StateGate
          loading={architecture.loading}
          error={architecture.error}
          isEmpty={stages.length === 0}
          onRetry={architecture.reload}
          loadingFallback={<Skeleton className="mt-4 h-64 w-full rounded-xl" />}
          errorTitle="Could not read the architecture description"
          className="mt-4"
        >
          <ol className="space-y-2">
            {PIPELINE_STAGES.filter((s) => stages.includes(s.stage)).map((item, index) => (
              <li
                key={item.stage}
                className="flex gap-3 rounded-xl border border-cyan/15 bg-cyan/[0.03] px-3 py-2.5"
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-cyan/25 bg-cyan/10 font-mono text-2xs text-cyan">
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium text-ink">{item.title}</span>
                    <span className="font-mono text-2xs text-ink-faint">{item.stage}</span>
                  </div>
                  <p className="mt-1 text-2xs leading-relaxed text-ink-faint">{item.detail}</p>
                </div>
              </li>
            ))}
          </ol>

          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-teal/20 bg-teal/[0.05] px-3 py-2.5">
            <span className="text-xs font-medium text-teal">ALLOW</span>
            <span className="text-2xs text-ink-faint">
              → the action executes → an event is emitted → an audit row is written
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-red/20 bg-red/[0.05] px-3 py-2.5">
            <span className="text-xs font-medium text-red">BLOCK</span>
            <span className="text-2xs text-ink-faint">
              → the action never executes → a security alert is raised with the rule that
              refused it
            </span>
          </div>
        </StateGate>
      </GlassCard>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Policy rules */}
        <GlassCard className="p-4">
          <CardHeader
            eyebrow="Deterministic policy"
            title="Rules in the enforcement path"
            subtitle="Served live from the backend."
          />
          <StateGate
            loading={architecture.loading}
            error={architecture.error}
            isEmpty={!arch?.policy_rules?.length}
            onRetry={architecture.reload}
            loadingFallback={<Skeleton className="mt-3 h-48 rounded-xl" />}
            className="mt-3"
          >
            <ul className="space-y-1.5">
              {(arch?.policy_rules || []).map((rule) => (
                <li key={rule.rule_id} className="rounded-lg border border-white/[0.06] px-3 py-2">
                  <div className="font-mono text-2xs text-cyan">{rule.rule_id}</div>
                  <div className="mt-0.5 font-mono text-2xs text-ink-muted">{rule.expression}</div>
                  <p className="mt-1 text-2xs leading-relaxed text-ink-faint">
                    {rule.description}
                  </p>
                </li>
              ))}
            </ul>
          </StateGate>
        </GlassCard>

        {/* Canonicalisation */}
        <GlassCard className="p-4">
          <CardHeader
            eyebrow="Verifiability"
            title="Agreement hashing"
            subtitle="Implemented in the backend, not pulled from a dependency, because the digest is the product’s guarantee."
          />
          <StateGate
            loading={architecture.loading}
            error={architecture.error}
            isEmpty={!arch?.canonicalization}
            onRetry={architecture.reload}
            loadingFallback={<Skeleton className="mt-3 h-32 rounded-xl" />}
            className="mt-3"
          >
            <div className="rounded-xl border border-white/[0.06] bg-base-900/50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                {['Final agreement', 'RFC 8785 canonical JSON', 'SHA-256', 'On-chain'].map(
                  (step, index) => (
                    <span key={step} className="flex items-center gap-2">
                      {index > 0 ? <span className="text-ink-faint">→</span> : null}
                      <span
                        className={`rounded px-1.5 py-0.5 font-mono text-2xs ${
                          index === 3 ? 'bg-teal/12 text-teal' : 'bg-white/5 text-ink-muted'
                        }`}
                      >
                        {step}
                      </span>
                    </span>
                  ),
                )}
              </div>
              <div className="mt-3 divide-y divide-white/[0.05] text-2xs">
                <div className="flex justify-between gap-3 py-1">
                  <span className="text-ink-faint">scheme</span>
                  <span className="font-mono text-ink-muted">
                    {arch?.canonicalization?.scheme}
                  </span>
                </div>
                <div className="flex justify-between gap-3 py-1">
                  <span className="text-ink-faint">hash</span>
                  <span className="font-mono text-ink-muted">{arch?.canonicalization?.hash}</span>
                </div>
                <div className="flex justify-between gap-3 py-1">
                  <span className="text-ink-faint">implementation</span>
                  <span className="font-mono text-ink-muted">
                    {arch?.canonicalization?.implementation}
                  </span>
                </div>
              </div>
            </div>
          </StateGate>
        </GlassCard>

        {/* Sentinel capabilities */}
        <GlassCard className="p-4">
          <CardHeader
            eyebrow="Defence in depth"
            title="Never grantable capabilities"
            subtitle="Refused even if a registry entry were corrupted to include them."
          />
          <StateGate
            loading={architecture.loading}
            error={architecture.error}
            isEmpty={!arch?.sentinel_capabilities?.length}
            onRetry={architecture.reload}
            loadingFallback={<Skeleton className="mt-3 h-24 rounded-xl" />}
            className="mt-3"
          >
            <div className="flex flex-wrap gap-1.5">
              {(arch?.sentinel_capabilities || []).map((capability) => (
                <span
                  key={capability}
                  className="rounded border border-red/25 bg-red/[0.07] px-1.5 py-0.5 font-mono text-2xs text-red/85"
                >
                  {capability}
                </span>
              ))}
            </div>
          </StateGate>
        </GlassCard>

        {/* Explicit non-goals */}
        <GlassCard className="p-4">
          <CardHeader
            eyebrow="Honest scope"
            title="Deliberately not built"
            subtitle="Features that would have cost implementation time without justifying it."
          />
          <ul className="mt-3 space-y-1.5">
            {NOT_BUILT.map((item) => (
              <li key={item} className="flex gap-2 text-2xs leading-relaxed text-ink-faint">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
                {item}
              </li>
            ))}
          </ul>
        </GlassCard>
      </div>

      {/* The story */}
      <GlassCard className="mt-4 p-5">
        <CardHeader eyebrow="The demo story" title="One narrative, end to end" />
        <ol className="mt-3 space-y-2 text-xs leading-relaxed text-ink-muted">
          <li>
            1. A buyer gives an autonomous agent authority to procure 1,000 packaging units
            within a $2,000 budget and a seven-day delivery limit.
          </li>
          <li>2. The agent discovers suppliers and verifies their identities against the registry.</li>
          <li>
            3. Supplier A offers a lower price but cannot meet the delivery constraint — the
            deterministic policy engine refuses it.
          </li>
          <li>
            4. Supplier B meets the delivery window but opens above budget, so the agents
            negotiate inside the feasible band.
          </li>
          <li>
            5. Meanwhile a compromised Supplier C agent attempts an unauthorized capability
            and then an out-of-envelope price. TrustMesh blocks both and raises security events.
          </li>
          <li>6. The legitimate agents complete their negotiation.</li>
          <li>
            7. The final agreement is canonicalized, hashed, and committed to a public
            blockchain so it can be independently verified.
          </li>
        </ol>

        <div className="mt-5 flex items-baseline gap-3 rounded-xl border border-cyan/20 bg-cyan/[0.05] px-3 py-3">
          <span className="eyebrow shrink-0 text-cyan">In one line</span>
          <p className="text-sm font-medium leading-relaxed text-ink">
            Autonomous negotiation without autonomous authority.
          </p>
        </div>

        <div className="mt-3 rounded-xl border border-amber/25 bg-amber/[0.06] px-3 py-2.5">
          <p className="text-2xs leading-relaxed text-amber">
            <span className="font-medium">Prototype disclaimer.</span> TrustMesh is a hackathon
            prototype. The threat scenarios are controlled simulations and perform no real
            attack. No production readiness, certification or security assurance is claimed.
          </p>
        </div>
      </GlassCard>
    </Page>
  )
}
