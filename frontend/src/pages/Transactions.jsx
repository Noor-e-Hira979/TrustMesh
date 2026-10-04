/**
 * Transactions page (spec §19).
 *
 * Shows the full lifecycle — intent, discovery, identity verification, offers,
 * negotiation, policy checks, security events, agreement, hash and blockchain
 * commitment — with the three-layer attribution kept visible at all times.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import {
  formatRelative,
  formatTime,
  formatUsd,
  truncateId,
  humanize,
} from '../lib/format'
import { TRANSACTION_STATE_LABELS, TRANSACTION_STATE_TONES } from '../lib/constants'
import { useApi, useMutation, useTimeline, useTransactions } from '../hooks/useApi'
import { useGlobalEvents } from '../state/GlobalEventContext'
import { ActionButton, Page, PageHeader } from '../components/layout/Layout'
import {
  CardHeader,
  DataRow,
  GlassCard,
  HashChip,
  JsonBlock,
  StatusPill,
} from '../components/ui/Primitives'
import {
  CommitmentStateBanner,
  DisconnectedState,
  Skeleton,
  StateGate,
} from '../components/ui/States'
import { Timeline } from '../components/transactions/Timeline'
import { AlertCard } from '../components/security/SecurityFeed'

function TransactionList({ items, selectedId, onSelect, loading, error, onRetry }) {
  return (
    <StateGate
      loading={loading}
      error={error}
      isEmpty={items.length === 0}
      onRetry={onRetry}
      loadingFallback={
        <div className="space-y-2">
          <Skeleton className="h-16 w-full rounded-xl" />
          <Skeleton className="h-16 w-full rounded-xl" />
          <Skeleton className="h-16 w-full rounded-xl" />
        </div>
      }
      emptyTitle="No transactions"
      emptyDetail="Run the demonstration to create one."
    >
      <ul className="space-y-1.5">
        {items.map((txn) => (
          <li key={txn.id}>
            <button
              type="button"
              onClick={() => onSelect(txn.id)}
              className={`w-full rounded-xl border px-3 py-2.5 text-left transition-colors ${
                selectedId === txn.id
                  ? 'border-cyan/30 bg-cyan/[0.06]'
                  : 'border-white/[0.06] hover:border-white/15'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-xs font-medium text-ink-muted">{txn.product}</span>
                <StatusPill tone={TRANSACTION_STATE_TONES[txn.state] || 'neutral'}>
                  {TRANSACTION_STATE_LABELS[txn.state] || txn.state}
                </StatusPill>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-ink-faint">
                <span className="font-mono">{truncateId(txn.id, 12, 4)}</span>
                <span>{txn.quantity.toLocaleString()} units</span>
                <span>{formatUsd(txn.budget_cents)}</span>
                {txn.block_count > 0 ? (
                  <span className="text-red">{txn.block_count} blocked</span>
                ) : null}
                <span className="ml-auto">{formatRelative(txn.created_at)}</span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </StateGate>
  )
}

function OfferTable({ offers = [] }) {
  if (offers.length === 0) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[520px] text-left">
        <thead>
          <tr className="border-b border-white/[0.06]">
            {['Round', 'Agent', 'Price', 'Delivery', 'Cert', 'Type', ''].map((header) => (
              <th
                key={header}
                className="whitespace-nowrap px-2 py-1.5 text-2xs font-semibold uppercase tracking-wider text-ink-faint"
              >
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {offers.map((offer) => (
            <tr key={offer.id} className="border-b border-white/[0.04] last:border-0">
              <td className="px-2 py-1.5 font-mono text-2xs text-ink-faint">{offer.round_number}</td>
              <td className="px-2 py-1.5 font-mono text-2xs text-ink-muted">{offer.agent_id}</td>
              <td className="px-2 py-1.5 font-mono text-2xs text-ink-muted">
                {formatUsd(offer.price_cents)}
              </td>
              <td className="px-2 py-1.5 font-mono text-2xs text-ink-muted">
                {offer.delivery_days}d
              </td>
              <td className="px-2 py-1.5 font-mono text-2xs text-ink-muted">{offer.certification}</td>
              <td className="px-2 py-1.5">
                <StatusPill
                  tone={offer.offer_type === 'final' ? 'good' : offer.offer_type === 'initial' ? 'neutral' : 'agent'}
                  dot={false}
                >
                  {offer.offer_type}
                </StatusPill>
              </td>
              <td className="max-w-[220px] px-2 py-1.5">
                <span
                  className="line-clamp-2 text-2xs leading-relaxed text-ink-faint"
                  title={offer.rationale}
                >
                  {offer.rationale}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function DecisionTable({ decisions = [] }) {
  const blocks = decisions.filter((d) => d.decision === 'BLOCK')
  if (blocks.length === 0) return null
  return (
    <div className="space-y-1.5">
      {blocks.map((decision) => (
        <div
          key={decision.id}
          className="rounded-lg border border-red/20 bg-red/[0.05] px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone="bad">BLOCK</StatusPill>
            <span className="font-mono text-2xs text-red/85">{decision.rule_id}</span>
            <span className="font-mono text-2xs text-ink-faint">{decision.agent_id}</span>
            {decision.capability ? (
              <span className="font-mono text-2xs text-ink-faint/70">
                cap: {decision.capability}
              </span>
            ) : null}
            <time className="ml-auto font-mono text-2xs text-ink-faint/60">
              {formatTime(decision.created_at)}
            </time>
          </div>
          <p className="mt-1 text-2xs leading-relaxed text-red/80">{decision.reason}</p>
        </div>
      ))}
    </div>
  )
}

export default function Transactions() {
  const { state } = useGlobalEvents()
  const [selectedId, setSelectedId] = useState(null)
  const [verification, setVerification] = useState(null)

  const { data, error, loading, reload } = useTransactions({ limit: 30 })
  const verify = useMutation((id) => api.verifyTransaction(id))
  const runDemo = useMutation(() => api.runDemo({ step_delay_seconds: 0.4 }))

  const items = useMemo(() => data?.items || [], [data])

  // Default to the newest transaction, and follow the live one if it changes.
  useEffect(() => {
    if (state.lastTransactionId && (!selectedId || !items.some((t) => t.id === selectedId))) {
      setSelectedId(state.lastTransactionId)
      return
    }
    if (!selectedId && items.length > 0) setSelectedId(items[0].id)
  }, [items, state.lastTransactionId, selectedId])

  const detail = useApi(() => api.getTransaction(selectedId), [selectedId], {
    enabled: Boolean(selectedId),
    pollMs: 2500,
  })
  const timeline = useTimeline(selectedId)

  const txn = detail.data
  const connection = state.connection.status
  const isStale = ['disconnected', 'reconnecting', 'stale'].includes(connection)

  const onVerify = async () => {
    if (!selectedId) return
    const result = await verify.mutate(selectedId)
    setVerification(result)
  }

  return (
    <Page>
      <PageHeader
        eyebrow="Lifecycle"
        title="Transactions"
        subtitle="The complete chain of custody for one procurement: what was asked, what was authorized, what was refused, and what was finally committed."
        right={
          <>
            <ActionButton tone="ghost" onClick={reload} pending={loading && Boolean(data)}>
              Refresh list
            </ActionButton>
            <ActionButton onClick={() => runDemo.mutate()} pending={runDemo.pending}>
              Run demonstration
            </ActionButton>
          </>
        }
      />

      {isStale ? (
        <DisconnectedState status={connection} attempt={state.connection.attempt} className="mb-4" />
      ) : null}

      <div className="grid gap-4 xl:grid-cols-4">
        {/* List */}
        <GlassCard className="p-4 xl:col-span-1">
          <CardHeader eyebrow="All transactions" title={`${items.length} recorded`} />
          <div className="mt-3">
            <TransactionList
              items={items}
              selectedId={selectedId}
              onSelect={(id) => {
                setSelectedId(id)
                setVerification(null)
              }}
              loading={loading}
              error={error}
              onRetry={reload}
            />
          </div>
        </GlassCard>

        {/* Detail */}
        <div className="space-y-4 xl:col-span-3">
          <GlassCard className="p-4">
            <CardHeader
              eyebrow={txn ? txn.id : 'Transaction'}
              title={txn ? txn.product : 'Select a transaction'}
              subtitle={
                txn
                  ? `${txn.quantity.toLocaleString()} units · budget ${formatUsd(txn.budget_cents)} · delivery ≤ ${txn.max_delivery_days} days · certification ${txn.certification_required}`
                  : 'Choose one from the list to inspect its full lifecycle.'
              }
              right={
                txn ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusPill tone={TRANSACTION_STATE_TONES[txn.state] || 'neutral'}>
                      {TRANSACTION_STATE_LABELS[txn.state] || txn.state}
                    </StatusPill>
                    {txn.commitment ? (
                      <ActionButton tone="ghost" onClick={onVerify} pending={verify.pending}>
                        Verify hash
                      </ActionButton>
                    ) : null}
                  </div>
                ) : null
              }
            />

            <StateGate
              loading={detail.loading}
              error={detail.error}
              isEmpty={!txn}
              onRetry={detail.reload}
              loadingFallback={<Skeleton className="mt-4 h-40 w-full rounded-xl" />}
              emptyTitle="No transaction selected"
              emptyDetail="Pick a transaction from the list."
              className="mt-3"
            >
              {txn ? (
                <>
                  <div className="mt-3 grid gap-4 md:grid-cols-2">
                    <div className="divide-y divide-white/[0.05]">
                      <DataRow label="Intent ref" value={txn.intent_ref} mono />
                      <DataRow label="Buyer" value={`${txn.buyer_agent_id}`} mono />
                      <DataRow label="Buyer org" value={txn.buyer_org_id} mono />
                      <DataRow
                        label="Supplier"
                        value={txn.supplier_agent_id || 'not yet agreed'}
                        mono
                      />
                      <DataRow label="AI mode" value={txn.ai_mode} mono />
                    </div>
                    <div className="divide-y divide-white/[0.05]">
                      <DataRow label="State" value={txn.state} mono />
                      <DataRow label="Negotiation round" value={String(txn.negotiation_round)} mono />
                      <DataRow label="Actions blocked" value={String(txn.block_count)} mono />
                      <DataRow label="Offers" value={String(txn.offers?.length ?? 0)} mono />
                      <DataRow label="Created" value={formatTime(txn.created_at, { withDate: true })} />
                    </div>
                  </div>

                  {/* Agreement + chain */}
                  {txn.commitment ? (
                    <div className="mt-4 space-y-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="eyebrow">Agreement commitment</span>
                        <HashChip value={txn.commitment.agreement_hash} />
                        <span className="font-mono text-2xs text-ink-faint">
                          {txn.commitment.algorithm} · {txn.commitment.canonicalization}
                        </span>
                      </div>
                      <CommitmentStateBanner
                        status={txn.commitment.status}
                        detail={
                          txn.commitment.status === 'CONFIRMED'
                            ? `Confirmed in block ${txn.commitment.block_number} with ${txn.commitment.confirmations} confirmation(s).`
                            : txn.commitment.error_message || undefined
                        }
                        onRetry={onVerify}
                      />
                    </div>
                  ) : (
                    <p className="mt-4 text-2xs text-ink-faint">
                      No agreement commitment yet — the run has not reached the hashing stage.
                    </p>
                  )}

                  {/* Verification result */}
                  {verification ? (
                    <div
                      className={`mt-3 rounded-xl border px-3 py-2.5 ${
                        verification.hash_matches
                          ? 'border-teal/25 bg-teal/[0.06]'
                          : 'border-red/25 bg-red/[0.06]'
                      }`}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusPill tone={verification.hash_matches ? 'good' : 'bad'}>
                          {verification.hash_matches ? 'digest reproduced' : 'digest mismatch'}
                        </StatusPill>
                        <span className="font-mono text-2xs text-ink-faint">
                          {verification.canonicalization} → {verification.algorithm}
                        </span>
                        {verification.on_chain_matches !== null &&
                        verification.on_chain_matches !== undefined ? (
                          <StatusPill tone={verification.on_chain_matches ? 'good' : 'bad'}>
                            chain {verification.on_chain_matches ? 'matches' : 'differs'}
                          </StatusPill>
                        ) : null}
                      </div>
                      <ul className="mt-1.5 space-y-0.5">
                        {(verification.notes || []).map((note, index) => (
                          <li key={index} className="text-2xs leading-relaxed text-ink-faint">
                            {note}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </>
              ) : null}
            </StateGate>
          </GlassCard>

          {/* Timeline */}
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Complete lifecycle"
              title="Intent → commitment"
              subtitle="Each step is attributed to the authority that produced it."
            />
            <div className="mt-3">
              <StateGate
                loading={timeline.loading}
                error={timeline.error}
                isEmpty={(timeline.data || []).length === 0}
                onRetry={timeline.reload}
                loadingFallback={<Skeleton className="h-64 w-full rounded-xl" />}
                emptyTitle="No timeline yet"
                emptyDetail="The lifecycle is recorded as the run progresses."
              >
                <Timeline steps={timeline.data || []} />
              </StateGate>
            </div>
          </GlassCard>

          {/* Offers + decisions */}
          <div className="grid gap-4 lg:grid-cols-2">
            <GlassCard className="p-4">
              <CardHeader
                eyebrow="Negotiation history"
                title={`Offers (${txn?.offers?.length ?? 0})`}
                subtitle="Counter-offers are clamped to the feasible band by construction."
              />
              <div className="mt-3">
                {txn?.offers?.length ? (
                  <OfferTable offers={txn.offers} />
                ) : (
                  <p className="text-2xs text-ink-faint">No offers recorded yet.</p>
                )}
              </div>
            </GlassCard>

            <GlassCard className="p-4">
              <CardHeader
                eyebrow="Policy decisions"
                title="What TrustMesh refused"
                subtitle="Every block carries the rule that produced it."
              />
              <div className="mt-3">
                {txn?.decisions?.length ? (
                  <DecisionTable decisions={txn.decisions} />
                ) : (
                  <p className="text-2xs text-ink-faint">No decisions recorded yet.</p>
                )}
              </div>
            </GlassCard>
          </div>

          {/* Security events for this transaction */}
          <GlassCard className="p-4">
            <CardHeader
              eyebrow="Security events"
              title={`Alerts on this transaction (${txn?.alerts?.length ?? 0})`}
              subtitle="Honest counterparties can also be policy-blocked — a delivery constraint or a budget ceiling is a normal refusal, not an attack."
            />
            <div className="mt-3 space-y-3">
              {txn?.alerts?.length ? (
                txn.alerts.map((alert) => <AlertCard key={alert.id} alert={alert} />)
              ) : (
                <p className="text-2xs text-ink-faint">No security events on this transaction.</p>
              )}
            </div>
          </GlassCard>
        </div>
      </div>
    </Page>
  )
}
