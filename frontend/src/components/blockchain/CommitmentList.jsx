/**
 * Agreement commitment table for the Blockchain Explorer (spec §21).
 *
 * Shows network, chain info, contract address, connection status, and per commitment
 * the agreement hash, transaction hash, timestamp, confirmation status and an explorer
 * link where one exists. Never any private key — nothing in the API can carry one.
 */

import { COMMITMENT_STATUS_META, CONNECTION_META } from '../../lib/constants'
import { formatTime, formatUsd, truncateHash, truncateId } from '../../lib/format'
import { GlassCard, HashChip, JsonBlock, StatusPill } from '../ui/Primitives'
import { CommitmentStateBanner, EmptyState, StateGate } from '../ui/States'

/** Connection + network panel. */
export function ChainStatusPanel({ status, className = '' }) {
  if (!status) return null
  const connection = CONNECTION_META[status.connection] || CONNECTION_META.unavailable

  return (
    <GlassCard className={`p-4 ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="eyebrow">TrustMeshRegistry</span>
          <StatusPill tone={connection.tone}>{connection.label}</StatusPill>
          {status.mode === 'simulated' ? <StatusPill tone="warn">simulated mode</StatusPill> : null}
        </div>
        {status.explorer_url ? (
          <a
            href={status.explorer_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-mono text-2xs text-cyan underline-offset-2 hover:underline"
          >
            contract on explorer ↗
          </a>
        ) : null}
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Network</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">{status.network}</dd>
        </div>
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Chain ID</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">{status.chain_id}</dd>
        </div>
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Latest block</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">
            {status.latest_block ?? '—'}
          </dd>
        </div>
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Confirmations needed</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">
            {status.confirmations_required}
          </dd>
        </div>
      </dl>

      <div className="mt-3">
        <div className="eyebrow mb-1.5">Contract address</div>
        {status.contract_address ? (
          <HashChip value={status.contract_address} label="0x" lead={12} tail={8} />
        ) : (
          <p className="text-2xs text-amber">
            Not deployed. Run <code className="font-mono">contracts$ npm run deploy</code> and
            set <code className="font-mono">BLOCKCHAIN_CONTRACT_ADDRESS</code>.
          </p>
        )}
      </div>

      {status.commitment_counts && Object.keys(status.commitment_counts).length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(status.commitment_counts).map(([key, count]) => {
            const meta = COMMITMENT_STATUS_META[key] || { tone: 'neutral' }
            return (
              <StatusPill key={key} tone={meta.tone}>
                {count} {key.toLowerCase()}
              </StatusPill>
            )
          })}
        </div>
      ) : null}

      {Array.isArray(status.notes) && status.notes.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {status.notes.map((note, index) => (
            <li key={index} className="flex gap-2 text-2xs leading-relaxed text-ink-faint">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-faint" />
              {note}
            </li>
          ))}
        </ul>
      ) : null}
    </GlassCard>
  )
}

/** One commitment row, expandable to show the canonical JSON that produced the hash. */
export function CommitmentCard({ commitment, onRecheck, pending = false, className = '' }) {
  const meta = COMMITMENT_STATUS_META[commitment.status] || { label: commitment.status, tone: 'neutral' }

  return (
    <GlassCard className={`p-4 ${className}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={meta.tone}>{meta.label}</StatusPill>
            <span className="font-mono text-2xs text-ink-faint">
              {truncateId(commitment.transaction_id, 14, 4)}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <HashChip value={commitment.agreement_hash} label="agreement" />
            <span className="font-mono text-2xs text-ink-faint">
              {commitment.algorithm} · {commitment.canonicalization}
            </span>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <div className="text-right">
            <time
              className="block font-mono text-2xs tabular-nums text-ink-faint"
              dateTime={commitment.created_at}
            >
              {formatTime(commitment.created_at, { withDate: true })}
            </time>
            {commitment.confirmations !== undefined ? (
              <span className="mt-0.5 block font-mono text-2xs text-ink-faint/60">
                {commitment.confirmations} conf
              </span>
            ) : null}
          </div>
          {onRecheck && commitment.status !== 'CONFIRMED' ? (
            <button
              type="button"
              disabled={pending}
              onClick={() => onRecheck(commitment.id)}
              className="rounded border border-white/10 px-2 py-0.5 text-2xs text-ink-faint transition-colors hover:border-cyan/30 hover:text-cyan disabled:opacity-40"
            >
              {pending ? 'checking…' : 'Re-check'}
            </button>
          ) : null}
        </div>
      </div>

      <dl className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Transaction hash</dt>
          <dd className="mt-1 truncate font-mono text-2xs text-ink-muted">
            {commitment.tx_hash ? truncateHash(commitment.tx_hash, 10, 8) : '—'}
          </dd>
        </div>
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Block</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">{commitment.block_number ?? '—'}</dd>
        </div>
        <div className="glass-subtle px-3 py-2">
          <dt className="eyebrow">Network</dt>
          <dd className="mt-1 font-mono text-xs text-ink-muted">
            {commitment.network} · {commitment.chain_id}
          </dd>
        </div>
      </dl>

      {commitment.error_message ? (
        <p className="mt-2 rounded-lg border border-red/20 bg-red/[0.06] px-2.5 py-1.5 font-mono text-2xs leading-relaxed text-red/85">
          {commitment.error_message}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-3">
        {commitment.explorer_url ? (
          <a
            href={commitment.explorer_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-mono text-2xs text-cyan underline-offset-2 hover:underline"
          >
            view on explorer ↗
          </a>
        ) : (
          <span className="font-mono text-2xs text-ink-faint/60">
            no explorer link for {commitment.network}
          </span>
        )}
      </div>

      {commitment.canonical_json ? (
        <JsonBlock value={commitment.canonical_json} label="Canonical JSON (RFC 8785)" className="mt-2" />
      ) : null}
    </GlassCard>
  )
}

export function CommitmentList({
  commitments = [],
  loading = false,
  error = null,
  onRetry,
  onRecheck,
  pendingId = null,
  className = '',
}) {
  return (
    <StateGate
      loading={loading}
      error={error}
      isEmpty={commitments.length === 0}
      onRetry={onRetry}
      loadingFallback={
        <div className="space-y-3">
          <div className="shimmer h-32 rounded-2xl" />
          <div className="shimmer h-32 rounded-2xl" />
        </div>
      }
      emptyTitle="No commitments recorded"
      emptyDetail="An agreement commitment appears here once the final terms are canonicalised and hashed."
      className={className}
    >
      <div className="space-y-3">
        {commitments.map((commitment) => (
          <CommitmentCard
            key={commitment.id}
            commitment={commitment}
            onRecheck={onRecheck}
            pending={pendingId === commitment.id}
          />
        ))}
      </div>
    </StateGate>
  )
}

export default CommitmentList
