/**
 * Blockchain Commitment Explorer (spec §21).
 *
 * Shows network, chain information, contract address, connection status, and every
 * agreement commitment with its hashes, timestamp, confirmation status and explorer
 * link where one exists. Also demonstrates independent verification: recompute the
 * digest locally from the canonical JSON and compare it with the stored value.
 *
 * A chain failure is presented as a state, never as a broken page.
 */

import { useState } from 'react'
import { api } from '../lib/api'
import { formatTime } from '../lib/format'
import { useApi, useCommitments, useMutation } from '../hooks/useApi'
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
  BlockchainFailedState,
  BlockchainPendingState,
  BlockchainSimulatedState,
  DisconnectedState,
  Skeleton,
  StateGate,
} from '../components/ui/States'
import { ChainStatusPanel, CommitmentCard, CommitmentList } from '../components/blockchain/CommitmentList'

function HashingExplainer() {
  const hashing = useApi(() => api.getBlockchainHashing(), [])
  const data = hashing.data

  return (
    <GlassCard className="p-4">
      <CardHeader
        eyebrow="Reproducibility"
        title="How the digest is produced"
        subtitle="The hash is a pure function of the canonical bytes, so anyone can recompute it."
        right={
          data ? (
            <StatusPill tone={data.selftest_passed ? 'good' : 'bad'}>
              {data.selftest_passed ? 'self-test passed' : 'self-test failed'}
            </StatusPill>
          ) : null
        }
      />
      <StateGate
        loading={hashing.loading}
        error={hashing.error}
        isEmpty={!data}
        onRetry={hashing.reload}
        loadingFallback={<Skeleton className="mt-3 h-48 w-full rounded-xl" />}
        className="mt-3"
      >
        {data ? (
          <>
            <div className="divide-y divide-white/[0.05]">
              <DataRow label="Canonicalisation" value={data.canonicalization} mono />
              <DataRow label="Hash" value={data.algorithm} mono />
              <DataRow label="Key ordering" value={data.key_order} />
              <DataRow label="Numbers" value={data.number_format} />
              <DataRow label="Strings" value={data.string_escaping} />
            </div>

            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <div>
                <div className="eyebrow mb-1.5">Stored on-chain</div>
                <ul className="space-y-1">
                  {(data.on_chain_payload || []).map((item) => (
                    <li
                      key={item}
                      className="flex gap-2 border-l-2 border-teal/40 pl-2.5 text-2xs leading-relaxed text-ink-faint"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <div className="eyebrow mb-1.5">Never stored on-chain</div>
                <ul className="space-y-1">
                  {(data.never_stored_on_chain || []).map((item) => (
                    <li
                      key={item}
                      className="flex gap-2 border-l-2 border-red/40 pl-2.5 text-2xs leading-relaxed text-ink-faint"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>

            <JsonBlock
              value={data.vectors}
              label={`RFC 8785 self-test vectors (${(data.vectors || []).length})`}
              className="mt-3"
            />
          </>
        ) : null}
      </StateGate>
    </GlassCard>
  )
}

export default function Blockchain() {
  const { state } = useGlobalEvents()
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [pendingId, setPendingId] = useState(null)

  const chain = useApi(() => api.getBlockchainStatus(), [], { pollMs: 8000 })
  const commitments = useCommitments({
    limit: 30,
    status: statusFilter === 'ALL' ? undefined : statusFilter,
  })
  const recheck = useMutation((id) => api.confirmCommitment(id))

  // Merge live commitment frames so a confirmation appears without waiting for a poll.
  const liveCommitments = state.commitments
  const rows = commitments.data?.items || []

  const onRecheck = async (id) => {
    setPendingId(id)
    await recheck.mutate(id)
    setPendingId(null)
    commitments.reload()
    chain.reload()
  }

  const connection = state.connection.status
  const isStale = ['disconnected', 'reconnecting', 'stale'].includes(connection)

  const STATUS_FILTERS = ['ALL', 'CONFIRMED', 'SIMULATED', 'PENDING', 'FAILED']

  return (
    <Page>
      <PageHeader
        eyebrow="Tamper-evident commitment layer"
        title="Blockchain"
        subtitle="Blockchain is not the security engine. TrustMesh enforces in real time; the chain provides a public, verifiable witness that an agreement existed in a particular form. Only a hash and pseudonymous references are stored."
        right={
          <>
            <ActionButton
              tone="ghost"
              onClick={() => {
                chain.reload()
                commitments.reload()
              }}
              pending={chain.loading || commitments.loading}
            >
              Refresh
            </ActionButton>
          </>
        }
      />

      {isStale ? (
        <DisconnectedState status={connection} attempt={state.connection.attempt} className="mb-4" />
      ) : null}

      {/* Chain status */}
      <StateGate
        loading={chain.loading && !chain.data}
        error={chain.error}
        isEmpty={!chain.data}
        onRetry={chain.reload}
        loadingFallback={<Skeleton className="h-56 w-full rounded-2xl" />}
        errorTitle="Could not read the blockchain status"
        className="mb-5"
      >
        {chain.data ? (
          <div className="space-y-3">
            <ChainStatusPanel status={chain.data} />
            {chain.data.connection === 'degraded' ? (
              <BlockchainFailedState
                detail="The EVM node is unreachable. Commitment submission will fail or stay pending — core TrustMesh enforcement continues to work normally."
              />
            ) : null}
            {chain.data.connection === 'simulated' ? (
              <BlockchainSimulatedState />
            ) : null}
          </div>
        ) : null}
      </StateGate>

      {/* Live commitment activity */}
      {liveCommitments.length > 0 ? (
        <GlassCard className="mb-5 p-4">
          <CardHeader
            eyebrow="Streaming now"
            title="Chain activity"
            subtitle="Commitment events observed on the live stream during this session."
          />
          <ul className="mt-3 space-y-1.5">
            {liveCommitments.slice(0, 6).map((commitment) => (
              <li
                key={commitment.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 border-l-2 border-teal/40 pl-3"
              >
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
                  {String(commitment.status || '').toLowerCase()}
                </StatusPill>
                <time className="ml-auto font-mono text-2xs text-ink-faint/60">
                  {formatTime(commitment.ts)}
                </time>
              </li>
            ))}
          </ul>
        </GlassCard>
      ) : null}

      {/* Commitments */}
      <section className="mb-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <span className="eyebrow">
            Agreement commitments ({commitments.data?.total ?? 0})
          </span>
          <div className="flex flex-wrap gap-1">
            {STATUS_FILTERS.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setStatusFilter(option)}
                className={`rounded-lg border px-2 py-0.5 text-2xs font-medium transition-colors ${
                  statusFilter === option
                    ? 'border-cyan/30 bg-cyan/12 text-cyan'
                    : 'border-white/10 text-ink-faint hover:border-white/20 hover:text-ink-muted'
                }`}
              >
                {option.toLowerCase()}
              </button>
            ))}
          </div>
        </div>

        {recheck.error ? (
          <GlassCard className="mb-3 border-red/25 p-3">
            <p className="text-xs text-red">{recheck.error.message}</p>
          </GlassCard>
        ) : null}

        <CommitmentList
          commitments={rows}
          loading={commitments.loading && !commitments.data}
          error={commitments.error}
          onRetry={commitments.reload}
          onRecheck={onRecheck}
          pendingId={pendingId}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <HashingExplainer />

        <GlassCard className="p-4">
          <CardHeader
            eyebrow="Verify independently"
            title="Anyone can check a commitment"
            subtitle="No TrustMesh service is required to verify an agreement."
          />
          <ol className="mt-3 space-y-2 text-2xs leading-relaxed text-ink-faint">
            <li>
              <span className="font-mono text-ink-muted">1.</span> Take the canonical JSON —
              it is stored off-chain alongside the commitment.
            </li>
            <li>
              <span className="font-mono text-ink-muted">2.</span> Canonicalise it per
              RFC 8785: members sorted by UTF-16 code units, minimal string escaping,
              ECMAScript number formatting, no insignificant whitespace.
            </li>
            <li>
              <span className="font-mono text-ink-muted">3.</span> SHA-256 the UTF-8 bytes.
            </li>
            <li>
              <span className="font-mono text-ink-muted">4.</span> Compare with the
              <span className="font-mono text-teal"> agreementHash</span> on-chain.
            </li>
          </ol>
          <p className="mt-3 text-2xs leading-relaxed text-ink-faint">
            The <span className="font-mono text-ink-muted">Verify hash</span> action on the
            Transactions page performs exactly steps 2–4, and additionally reads the
            commitment back from the contract when a node is reachable.
          </p>
          <div className="mt-3 rounded-lg border border-white/[0.06] bg-base-900/50 px-3 py-2">
            <p className="font-mono text-2xs text-ink-muted">
              cast call &lt;address&gt;{' '}
              &quot;getCommitment(bytes32)(tuple,bool)&quot; &lt;transactionRef&gt; --rpc-url
              &lt;rpc&gt;
            </p>
          </div>
        </GlassCard>
      </div>
    </Page>
  )
}
