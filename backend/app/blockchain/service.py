"""BlockchainService - the only component that touches an EVM node.

Isolation guarantees (spec §12):
* It is only ever called *after* a policy ALLOW. It cannot grant, revoke or evaluate
  anything.
* The private key exists only in this process. It is never returned by a route, never
  logged, never attached to a response.
* Every failure is contained: an unreachable RPC or a reverted transaction produces a
  ``FAILED``/``PENDING`` commitment and a UI state. It never raises into the caller
  and never aborts a TrustMesh transaction.

Modes:
* ``live``     - a real RPC is configured; submit and wait for a receipt.
* ``simulated``- no RPC (or explicitly configured). Produces a deterministic, clearly
  labelled ``SIMULATED`` receipt so the verify story still works offline. It is never
  presented as a real confirmation.

Referenced by: ``app.orchestrator.demo_orchestrator``, ``app.api.routes.blockchain``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agreements.agreement_engine import (
    agent_reference,
    transaction_reference,
)
from app.blockchain.contract import ContractHandle, transaction_explorer_url
from app.core.config import settings
from app.core.events import EventType, dispatcher
from app.core.models import AgreementCommitment, CommitmentStatus, utcnow

logger = logging.getLogger("trustmesh.blockchain")

try:  # pragma: no cover - import guard
    from web3 import Web3
    from web3.exceptions import Web3Exception

    WEB3_AVAILABLE = True
except Exception:  # pragma: no cover
    Web3 = None  # type: ignore[assignment]
    Web3Exception = Exception  # type: ignore[assignment,misc]
    WEB3_AVAILABLE = False

#: Upper bound on the miner tip. The effective tip is min(this, base_fee) so that
#: maxFeePerPerGas always exceeds maxPriorityFeePerGas on low-fee chains.
PRIORITY_FEE_CAP = 1_500_000_000

#: Hard ceiling on :meth:`BlockchainService.probe`, which every dashboard endpoint calls.
#:
#: The provider's own per-call socket timeout is multiplied by the number of sequential
#: RPC calls a probe makes and again by the HTTP retry policy, so without an outer
#: deadline a node that accepts connections but never replies can pin an open request for
#: minutes. Read-only dashboard endpoints must answer regardless: the chain is reported,
#: never awaited. Kept below the frontend's 20s request timeout so the console renders a
#: degraded status rather than an error.
PROBE_TIMEOUT_SECONDS = 5.0

#: Per-socket timeout for provider calls. Deliberately shorter than the probe deadline so
#: the provider gives up first and the outer deadline is a genuine backstop.
RPC_SOCKET_TIMEOUT_SECONDS = 4.0

#: How long one probe result is shared between callers. The dashboard endpoints poll every
#: few seconds, so this collapses many concurrent probes into one per window.
PROBE_TTL_SECONDS = 5.0


@dataclass(slots=True)
class SubmissionResult:
    ok: bool
    status: str
    tx_hash: str | None
    block_number: int | None
    block_timestamp: int | None
    confirmations: int
    mode: str
    error: str | None
    explorer_url: str | None


class BlockchainService:
    """Web3 adapter with explicit degraded and simulated operation."""

    def __init__(self) -> None:
        self.handle = ContractHandle(
            settings.blockchain_contract_address,
            settings.effective_chain_id,
            settings.blockchain_network,
        )
        self._w3: Any | None = None
        self._account: Any | None = None
        self._connection: str = "unavailable"
        self._last_error: str | None = None
        self._latest_block: int | None = None
        self._lock = asyncio.Lock()
        #: Shared probe state. ``/api/health``, ``/api/overview`` and
        #: ``/api/blockchain/status`` each poll every few seconds, so without sharing one
        #: bounded probe per window every poll would spawn its own worker thread. A wedged
        #: node cannot cancel a thread already inside a blocking socket call, so those
        #: threads accumulate and saturate the default executor - at which point every
        #: ``run_db`` call in the app queues behind them too. One probe per TTL window,
        #: with single-flight, keeps the dashboard cheap and the pool free.
        self._probe_lock = asyncio.Lock()
        self._probe_result: dict[str, Any] | None = None
        self._probe_expires_at: float = 0.0

    # ----------------------------------------------------------- connection
    def _connect(self) -> Any | None:
        if not WEB3_AVAILABLE:
            self._last_error = "web3 is not installed"
            self._connection = "unavailable"
            return None
        if self._w3 is not None:
            return self._w3
        if not settings.rpc_configured:
            self._last_error = "no RPC URL configured"
            self._connection = "simulated"
            return None
        try:
            w3 = Web3(
                Web3.HTTPProvider(
                    settings.blockchain_rpc_url,
                    request_kwargs={"timeout": RPC_SOCKET_TIMEOUT_SECONDS},
                )
            )
            if not w3.is_connected():
                self._last_error = "RPC endpoint did not accept a connection"
                self._connection = "degraded"
                return None
            self._w3 = w3
            self._connection = "connected"
            self._last_error = None
            return w3
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            self._connection = "degraded"
            logger.warning("blockchain connection failed: %s", self._last_error)
            return None

    def _account_or_none(self, w3: Any) -> Any | None:
        if self._account is not None:
            return self._account
        if not settings.blockchain_private_key:
            self._last_error = "no private key configured; cannot sign transactions"
            return None
        try:
            account = w3.eth.account.from_key(settings.blockchain_private_key)
            self._account = account
            return account
        except Exception as exc:
            self._last_error = f"invalid private key: {type(exc).__name__}"
            logger.error("blockchain account error: %s", self._last_error)
            return None

    @property
    def mode(self) -> str:
        return settings.effective_blockchain_mode

    async def probe(self) -> dict[str, Any]:
        """Bounded, shared, non-blocking health probe used by the dashboard endpoints.

        ``/api/health``, ``/api/overview`` and ``/api/blockchain/status`` all call this on
        every poll, so it must degrade to a *status* rather than hold the request open.
        The underlying provider applies its own socket timeout per RPC call and a probe
        makes several of them in sequence (is_connected, block_number, chain_id), so the
        worst case is unbounded from the caller's point of view. A wedged RPC node - a
        Hardhat process still holding the port but not answering, for instance - would
        then hang the whole Overview dashboard indefinitely, which is exactly the failure
        this module exists to prevent: a chain problem is a degraded status, never a
        broken console.

        Two things make that guarantee real:

        * a hard deadline (:data:`PROBE_TIMEOUT_SECONDS`), and
        * single-flight caching (:data:`PROBE_TTL_SECONDS`), because a thread already
          blocked in a socket call cannot be cancelled, so unshared probes would pile up
          and starve the executor that ``run_db`` also depends on.

        The result is shared rather than per-caller on purpose: a health probe answers
        "is the node answering", which cannot change meaningfully within the TTL.
        """
        cached = self._probe_result
        if cached is not None and time.monotonic() < self._probe_expires_at:
            return cached

        async with self._probe_lock:
            # Re-check: another waiter may have refreshed it while this one queued.
            cached = self._probe_result
            if cached is not None and time.monotonic() < self._probe_expires_at:
                return cached
            return await self._probe_uncached()

    async def _probe_uncached(self) -> dict[str, Any]:

        def _probe() -> dict[str, Any]:
            w3 = self._connect()
            if w3 is None:
                return {
                    "connection": self._connection,
                    "latest_block": None,
                    "error": self._last_error,
                }
            try:
                block = w3.eth.block_number
                self._latest_block = int(block)
                chain_id = w3.eth.chain_id
                self._connection = "connected"
                if int(chain_id) != settings.effective_chain_id:
                    self._connection = "degraded"
                    self._last_error = (
                        f"RPC chain id {chain_id} does not match configured "
                        f"{settings.effective_chain_id}"
                    )
                return {
                    "connection": self._connection,
                    "latest_block": self._latest_block,
                    "error": self._last_error,
                }
            except Exception as exc:
                self._connection = "degraded"
                self._last_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                return {
                    "connection": "degraded",
                    "latest_block": None,
                    "error": self._last_error,
                }

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(_probe), timeout=PROBE_TIMEOUT_SECONDS
            )
        except (asyncio.TimeoutError, TimeoutError):
            # Drop the cached handle: it is attached to a node that is not answering, so
            # the next probe reconnects instead of inheriting the stall.
            self._w3 = None
            self._connection = "degraded"
            self._last_error = (
                f"RPC probe exceeded {PROBE_TIMEOUT_SECONDS:g}s; the node accepted the "
                "connection but did not answer"
            )
            logger.warning("blockchain probe timed out after %ss", PROBE_TIMEOUT_SECONDS)
            result = {
                "connection": "degraded",
                "latest_block": self._latest_block,
                "error": self._last_error,
            }

        self._probe_result = result
        self._probe_expires_at = time.monotonic() + PROBE_TTL_SECONDS
        return result

    def status(self) -> dict[str, Any]:
        connection = self._connection
        if self.mode == "simulated" and connection != "connected":
            connection = "simulated"
        notes: list[str] = []
        if self.handle.abi_error:
            notes.append(f"Contract ABI unavailable: {self.handle.abi_error}")
        elif not self.handle.abi:
            notes.append("Contract ABI is empty; recompile and re-export it.")
        if self.mode == "simulated":
            notes.append(
                "SIMULATED mode: no EVM RPC is configured, so commitments are recorded "
                "locally and clearly labelled SIMULATED. They are not on-chain confirmations."
            )
        elif connection != "connected":
            notes.append(
                "Blockchain is in degraded mode. Core TrustMesh enforcement is unaffected "
                "(spec §12/§34)."
            )
        if not self.handle.deployed:
            notes.append(
                "TRUSTMESH_CONTRACT_ADDRESS is not set. Deploy contracts/ and copy the "
                "address into backend/.env to enable real commitments."
            )
        if self._last_error:
            notes.append(f"Last error: {self._last_error}")
        return {
            **settings.safe_blockchain_summary(),
            "contract": self.handle.as_dict(),
            "connection": connection,
            "latest_block": self._latest_block,
            "explorer_url": self.handle.explorer_link(),
            "notes": notes,
            "web3_available": WEB3_AVAILABLE,
        }

    # --------------------------------------------------------------- commit
    async def commit_agreement(
        self,
        session: Session,
        commitment: AgreementCommitment,
        *,
        agreement_hash: str,
        transaction_id: str,
        buyer_agent_id: str,
        supplier_agent_id: str,
    ) -> SubmissionResult:
        """Submit one agreement commitment. Never raises."""
        async with self._lock:
            try:
                if self.mode == "simulated":
                    result = await self._simulate(commitment)
                else:
                    result = await asyncio.to_thread(
                        self._submit_live,
                        commitment,
                        agreement_hash,
                        transaction_id,
                        buyer_agent_id,
                        supplier_agent_id,
                    )
            except Exception as exc:  # pragma: no cover - defensive containment
                logger.exception("blockchain submission failed")
                result = SubmissionResult(
                    ok=False,
                    status=CommitmentStatus.FAILED.value,
                    tx_hash=None,
                    block_number=None,
                    block_timestamp=None,
                    confirmations=0,
                    mode=self.mode,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                    explorer_url=None,
                )

            await asyncio.to_thread(self._apply_submission, session, commitment, result)
            await self._emit(commitment, result)
            return result

    @staticmethod
    def _apply_submission(
        session: Session,
        commitment: AgreementCommitment,
        result: SubmissionResult,
    ) -> None:
        """Persist a submission result. Blocking, so always called on a worker thread."""
        commitment.status = result.status
        commitment.tx_hash = result.tx_hash
        commitment.block_number = result.block_number
        commitment.block_timestamp = result.block_timestamp
        commitment.confirmations = result.confirmations
        commitment.error_message = result.error
        commitment.submitted_at = commitment.submitted_at or utcnow()
        if result.status == CommitmentStatus.CONFIRMED.value:
            commitment.confirmed_at = utcnow()
        session.commit()

    async def confirm(
        self, session: Session, commitment: AgreementCommitment
    ) -> SubmissionResult:
        """Poll a pending receipt from an async call site.

        Delegates to :meth:`confirm_sync` on a worker thread: web3.py blocks, and the
        sync core also performs the ORM write, so running it inline would stall the
        event dispatcher.
        """
        async with self._lock:
            return await asyncio.to_thread(self.confirm_sync, session, commitment)

    def confirm_sync(
        self, session: Session, commitment: AgreementCommitment
    ) -> SubmissionResult:
        """Re-poll a pending commitment and persist the outcome.

        Synchronous by design: web3.py blocks and this method also writes to the ORM, so
        callers must already be on a worker thread. ``async def confirm`` wraps this for
        async call sites; the API route calls it directly from inside ``run_db``.

        Never raises: an unreachable node leaves the commitment ``PENDING`` with an
        explanatory error, because a pending commitment is a normal state, not a fault.
        """
        if commitment.status == CommitmentStatus.SIMULATED.value:
            return SubmissionResult(
                ok=True,
                status=commitment.status,
                tx_hash=commitment.tx_hash,
                block_number=commitment.block_number,
                block_timestamp=commitment.block_timestamp,
                confirmations=commitment.confirmations,
                mode="simulated",
                error=None,
                explorer_url=commitment.tx_hash
                and transaction_explorer_url(commitment.network, commitment.tx_hash),
            )

        result = self._poll_receipt(commitment)

        commitment.status = result.status
        commitment.confirmations = result.confirmations
        if result.block_number is not None:
            commitment.block_number = result.block_number
        if result.block_timestamp is not None:
            commitment.block_timestamp = result.block_timestamp
        if result.tx_hash and not commitment.tx_hash:
            commitment.tx_hash = result.tx_hash
        if result.error and result.status == CommitmentStatus.FAILED.value:
            commitment.error_message = result.error
        if result.status == CommitmentStatus.CONFIRMED.value:
            commitment.confirmed_at = commitment.confirmed_at or utcnow()
            commitment.error_message = None
        session.commit()

        self._emit_from_thread(commitment, result)
        return result

    def _poll_receipt(self, commitment: AgreementCommitment) -> SubmissionResult:
        """Read one receipt from the node. Pure RPC, no ORM access."""
        w3 = self._connect()
        if w3 is None:
            return SubmissionResult(
                ok=False,
                status=CommitmentStatus.PENDING.value,
                tx_hash=commitment.tx_hash,
                block_number=None,
                block_timestamp=None,
                confirmations=commitment.confirmations,
                mode=self.mode,
                error=self._last_error or "node unreachable; commitment stays pending",
                explorer_url=transaction_explorer_url(commitment.network, commitment.tx_hash),
            )
        if not commitment.tx_hash:
            return SubmissionResult(
                ok=False,
                status=CommitmentStatus.FAILED.value,
                tx_hash=None,
                block_number=None,
                block_timestamp=None,
                confirmations=0,
                mode=self.mode,
                error="no transaction hash recorded for this commitment",
                explorer_url=None,
            )
        try:
            receipt = w3.eth.get_transaction_receipt(commitment.tx_hash)
        except Exception as exc:
            return SubmissionResult(
                ok=False,
                status=CommitmentStatus.PENDING.value,
                tx_hash=commitment.tx_hash,
                block_number=None,
                block_timestamp=None,
                confirmations=commitment.confirmations,
                mode=self.mode,
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
                explorer_url=transaction_explorer_url(commitment.network, commitment.tx_hash),
            )

        block_number = int(receipt["blockNumber"])
        try:
            block_timestamp = int(w3.eth.get_block(block_number)["timestamp"])
        except Exception:
            block_timestamp = None
        confirmations = max(0, int(w3.eth.block_number) - block_number + 1)
        succeeded = int(receipt["status"]) == 1
        confirmed = succeeded and confirmations >= settings.blockchain_confirmations_required

        return SubmissionResult(
            ok=succeeded,
            status=(
                CommitmentStatus.CONFIRMED.value
                if confirmed
                else CommitmentStatus.PENDING.value
            ),
            tx_hash=commitment.tx_hash,
            block_number=block_number,
            block_timestamp=block_timestamp,
            confirmations=confirmations,
            mode="live",
            error=None if succeeded else "transaction reverted on chain",
            explorer_url=transaction_explorer_url(commitment.network, commitment.tx_hash),
        )

    def _emit_from_thread(
        self, commitment: AgreementCommitment, result: SubmissionResult
    ) -> None:
        """Emit a chain event from a worker thread.

        The dispatcher is asyncio-native, so the frame is handed to the running loop with
        ``call_soon_threadsafe``. With no loop running (scripts, tests) the frame is
        skipped rather than raising: the commitment row is already persisted, so nothing
        is lost.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("no running event loop; skipping chain event emission")
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._emit(commitment, result))
        )

    # -------------------------------------------------------------- readback
    async def read_commitment(self, transaction_id: str) -> dict[str, Any]:
        """Read a commitment back from the chain - the independent verification path.

        Never raises: an unreachable node or a missing ABI yields an explanatory

        ``available: False`` result instead of an exception.

        """
        ref = transaction_reference(transaction_id)

        def _read() -> dict[str, Any]:
            if not self.handle.deployed:
                return {
                    "available": False,
                    "present": None,
                    "matches": None,
                    "detail": "contract address not configured",
                }
            if self.handle.abi_error:
                return {
                    "available": False,
                    "present": None,
                    "matches": None,
                    "detail": self.handle.abi_error,
                }

            w3 = self._connect()
            if w3 is None:
                return {
                    "available": False,
                    "present": None,
                    "matches": None,
                    "detail": self._last_error or "no node connection",
                }

            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.handle.address or ""),
                    abi=self.handle.abi,
                )
                decoded = contract.functions.getCommitment(ref).call()
                record, exists = _split_get_commitment(decoded)
                if not exists:
                    return {
                        "available": True,
                        "present": False,
                        "matches": None,
                        "transaction_ref": ref,
                        "detail": "no commitment recorded for this transaction reference",
                    }
                return {
                    "available": True,
                    "present": True,
                    "matches": _to_hex32(record["agreementHash"]),
                    "transaction_ref": ref,
                    "buyer_agent_ref": _to_hex32(record["buyerAgentRef"]),
                    "supplier_agent_ref": _to_hex32(record["supplierAgentRef"]),
                    "committed_at": int(record["committedAt"]),
                    "block": int(record["commitBlock"]),
                    "registrant": record["registrant"],
                    "detail": "read back from TrustMeshRegistry",
                }
            except Exception as exc:
                return {
                    "available": False,
                    "present": None,
                    "matches": None,
                    "detail": f"{type(exc).__name__}: {str(exc)[:160]}",
                }

        return await asyncio.to_thread(_read)

    # ------------------------------------------------------------- internals
    def _submit_live(
        self,
        commitment: AgreementCommitment,
        agreement_hash: str,
        transaction_id: str,
        buyer_agent_id: str,
        supplier_agent_id: str,
    ) -> SubmissionResult:
        w3 = self._connect()
        if w3 is None:
            raise RuntimeError(self._last_error or "no EVM connection")
        if not self.handle.deployed:
            raise RuntimeError("TrustMeshRegistry address is not configured")
        if self.handle.abi_error:
            raise RuntimeError(f"TrustMeshRegistry ABI unavailable: {self.handle.abi_error}")
        if not self.handle.abi:
            raise RuntimeError("TrustMeshRegistry ABI is empty; cannot encode a commitment")

        account = self._account_or_none(w3)
        if account is None:
            raise RuntimeError(self._last_error or "no signing account")

        contract = w3.eth.contract(
            address=Web3.to_checksum_address(self.handle.address or ""), abi=self.handle.abi
        )
        account_address = account.address
        commit_fn = contract.functions.commitAgreement(
            _bytes32(agreement_hash),
            _bytes32(transaction_reference(transaction_id)),
            _bytes32(agent_reference(buyer_agent_id)),
            _bytes32(agent_reference(supplier_agent_id)),
        )

        nonce = w3.eth.get_transaction_count(account_address)
        chain_id = int(w3.eth.chain_id)

        # Gas first, because tx_params below needs it.
        # Estimate rather than trusting a fixed cap: the correct cost moves whenever the
        # contract changes, and a cap that is too low silently runs out of gas on any write
        # touching more storage slots. A 25% headroom absorbs estimate-vs-execution drift.
        gas = settings.blockchain_gas_limit
        try:
            # web3.py v7 takes a transaction-params dict here, not a contract function.
            estimate = int(
                w3.eth.estimate_gas(
                    {
                        "from": account_address,
                        "to": self.handle.address,
                        "data": commit_fn._encode_transaction_data(),
                    }
                )
            )
            if estimate > 0:
                gas = int(estimate * 1.25) + 5_000
                logger.debug("estimated gas %s -> using %s", estimate, gas)
        except Exception as exc:
            logger.warning(
                "gas estimation failed (%s: %s); falling back to configured limit %s",
                type(exc).__name__,
                str(exc)[:120],
                gas,
            )

        # EIP-1559 fee construction. Both values are derived from the current base fee so
        # that maxFeePerGas is always strictly greater than maxPriorityFeePerGas: a fixed
        # tip (e.g. 1.5 gwei) exceeds baseFee*2 on any low-fee chain and the node rejects
        # the transaction outright.
        try:
            base_fee = int(w3.eth.get_block("latest").get("baseFeePerGas") or 0)
        except Exception:
            base_fee = 0
        if base_fee > 0:
            priority_fee = min(PRIORITY_FEE_CAP, base_fee)
            max_fee = base_fee * 2 + priority_fee
        else:
            # Legacy chain or no base fee field: a plain gas price is accepted.
            priority_fee = PRIORITY_FEE_CAP
            max_fee = 0
        logger.debug(
            "fee params: base=%s priority=%s max=%s gas=%s",
            base_fee,
            priority_fee,
            max_fee,
            gas,
        )

        tx_params: dict[str, Any] = {
            "from": account_address,
            "nonce": nonce,
            "chainId": chain_id,
            "gas": gas,
            "value": 0,
        }
        if max_fee > 0:
            tx_params["maxFeePerGas"] = max_fee
            tx_params["maxPriorityFeePerGas"] = priority_fee
        else:
            tx_params["gasPrice"] = priority_fee

        tx = commit_fn.build_transaction(tx_params)
        signed = account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw)
        tx_hash_hex = tx_hash.hex() if hasattr(tx_hash, "hex") else str(tx_hash)
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex

        deadline = time.monotonic() + settings.blockchain_timeout_seconds
        confirmations = 0
        receipt = None
        while time.monotonic() < deadline:
            try:
                receipt = w3.eth.get_transaction_receipt(tx_hash)
            except Exception as exc:
                # web3.py v7 raises TransactionNotFound while the tx is still in the
                # mempool instead of returning None, and public RPCs also emit transient
                # load/rate errors. Neither means the transaction failed, so keep polling
                # rather than letting the exception escape and poison the commitment.
                logger.debug(
                    "receipt not available yet (%s: %s)", type(exc).__name__, str(exc)[:120]
                )
                receipt = None
            if receipt is not None:
                block_number = int(receipt["blockNumber"])
                try:
                    head = int(w3.eth.block_number)
                except Exception:
                    head = block_number
                confirmations = max(0, head - block_number + 1)
                if confirmations >= settings.blockchain_confirmations_required:
                    break
            time.sleep(2.0)

        if receipt is None:
            return SubmissionResult(
                ok=True,
                status=CommitmentStatus.PENDING.value,
                tx_hash=tx_hash_hex,
                block_number=None,
                block_timestamp=None,
                confirmations=confirmations,
                mode="live",
                error=None,
                explorer_url=transaction_explorer_url(commitment.network, tx_hash_hex),
            )

        block_number = int(receipt["blockNumber"])
        try:
            block_timestamp = int(w3.eth.get_block(block_number)["timestamp"])
        except Exception:
            block_timestamp = None
        succeeded = int(receipt["status"]) == 1
        return SubmissionResult(
            ok=succeeded,
            status=(
                CommitmentStatus.CONFIRMED.value
                if succeeded
                else CommitmentStatus.FAILED.value
            ),
            tx_hash=tx_hash_hex,
            block_number=block_number,
            block_timestamp=block_timestamp,
            confirmations=confirmations,
            mode="live",
            error=None if succeeded else "commitAgreement reverted on chain",
            explorer_url=transaction_explorer_url(commitment.network, tx_hash_hex),
        )

    async def _simulate(self, commitment: AgreementCommitment) -> SubmissionResult:
        """Deterministic offline receipt. Explicitly labelled SIMULATED."""
        await asyncio.sleep(settings.blockchain_simulated_latency_seconds)
        seed = f"{commitment.transaction_id}:{commitment.agreement_hash}"
        tx_hash = "0xsim" + hashlib.sha256(seed.encode()).hexdigest()[:56]
        block_number = 1_000_000 + (int(hashlib.sha256(seed.encode()).hexdigest()[:4], 16) % 9_000)
        block_timestamp = int(utcnow().timestamp())
        return SubmissionResult(
            ok=True,
            status=CommitmentStatus.SIMULATED.value,
            tx_hash=tx_hash,
            block_number=block_number,
            block_timestamp=block_timestamp,
            confirmations=settings.blockchain_confirmations_required,
            mode="simulated",
            error=None,
            explorer_url=None,
        )

    async def _emit(
        self, commitment: AgreementCommitment, result: SubmissionResult
    ) -> None:
        payload = {
            "commitment_id": commitment.id,
            "transaction_id": commitment.transaction_id,
            "agreement_hash": commitment.agreement_hash,
            "tx_hash": result.tx_hash,
            "block_number": result.block_number,
            "block_timestamp": result.block_timestamp,
            "confirmations": result.confirmations,
            "status": result.status,
            "mode": result.mode,
            "error": result.error,
            "explorer_url": result.explorer_url,
            "network": commitment.network,
            "chain_id": commitment.chain_id,
            "contract_address": commitment.contract_address,
        }
        if result.status == CommitmentStatus.CONFIRMED.value:
            await dispatcher.publish(
                EventType.BLOCKCHAIN_CONFIRMED,
                transaction_id=commitment.transaction_id,
                payload=payload,
            )
        elif result.status == CommitmentStatus.FAILED.value:
            await dispatcher.publish(
                EventType.BLOCKCHAIN_FAILED,
                transaction_id=commitment.transaction_id,
                payload=payload,
            )
        elif result.status == CommitmentStatus.PENDING.value:
            await dispatcher.publish(
                EventType.BLOCKCHAIN_SUBMITTED,
                transaction_id=commitment.transaction_id,
                payload=payload,
            )
        else:
            await dispatcher.publish(
                EventType.BLOCKCHAIN_SUBMITTED,
                transaction_id=commitment.transaction_id,
                payload={
                    **payload,
                    "note": "simulated receipt - not an on-chain confirmation",
                },
            )

    # ---------------------------------------------------------------- counts
    @staticmethod
    def counts(session: Session) -> dict[str, int]:
        rows = session.execute(
            select(AgreementCommitment.status, func.count(AgreementCommitment.id)).group_by(
                AgreementCommitment.status
            )
        ).all()
        return {str(status): int(count) for status, count in rows}

    async def commitment_count(self) -> int:
        """Number of commitments currently recorded on-chain. Never raises."""
        if not self.handle.deployed:
            return -1

        def _read() -> int:
            w3 = self._connect()
            if w3 is None:
                return -1
            try:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(self.handle.address or ""),
                    abi=self.handle.abi,
                )
                return int(contract.functions.commitmentCount().call())
            except Exception:
                return -1

        return await asyncio.to_thread(_read)


COMMITMENT_FIELDS: tuple[str, ...] = (
    "agreementHash",
    "transactionRef",
    "buyerAgentRef",
    "supplierAgentRef",
    "committedAt",
    "commitBlock",
    "registrant",
)

_ZERO32 = "0x" + "00" * 32


def _to_hex32(value: object) -> str:
    """Normalise a decoded bytes32 to a lowercase ``0x`` hex string."""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str):
        text = value[2:] if value.startswith("0x") else value
        return "0x" + text.rjust(64, "0").lower()
    return _ZERO32


def _as_bool(value: object) -> bool | None:
    """Coerce a decoded ``bool``.

    Returns ``None`` when the value is not recognisably boolean, so the caller can fall
    back to inspecting the stored hash. This matters because ``bool("0x00" * 32)`` is
    ``True`` in Python, which would otherwise report a missing commitment as present.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1"}:
            return True
        if text in {"false", "0", "0x0", "0x00"} or set(text) <= {"0", "x"}:
            return False
    return None


def _resolve_exists(flag: bool | None, agreement_hash: object) -> bool:
    """A commitment exists iff its stored digest is non-zero.

    The contract's ``bool`` output is authoritative when true. When it reports false,
    the stored hash is re-checked anyway: a false-but-populated record would mean a
    corrupted ABI or a proxy returning mismatched values, and failing closed there would
    hide a real commitment.
    """
    from_hash = _to_hex32(agreement_hash) != _ZERO32
    if flag is True:
        return True
    return from_hash


def _split_get_commitment(decoded: object) -> tuple[dict[str, object], bool]:
    """Normalise ``getCommitment`` output across ABI shapes.

    The compiled ABI returns the struct as a single ``tuple`` output followed by the
    ``bool``; an older hand-written ABI flattened the struct into seven outputs. Both
    shapes are accepted so a stale ABI file cannot silently corrupt verification.
    """
    if not isinstance(decoded, (list, tuple)) or not decoded:
        return {}, False

    first = decoded[0]
    second = decoded[1] if len(decoded) > 1 else None
    flag = _as_bool(second)

    if isinstance(first, dict):
        record = dict(first)
        return record, _resolve_exists(flag, record.get("agreementHash"))

    if isinstance(first, (list, tuple)):
        record = dict(zip(COMMITMENT_FIELDS, first, strict=False))
        return record, _resolve_exists(flag, record.get("agreementHash"))

    if len(decoded) >= len(COMMITMENT_FIELDS) + 1:
        record = dict(zip(COMMITMENT_FIELDS, decoded[: len(COMMITMENT_FIELDS)], strict=False))
        tail = _as_bool(decoded[len(COMMITMENT_FIELDS)])
        return record, _resolve_exists(tail if flag is None else flag, record.get("agreementHash"))

    return {}, False


def _bytes32(value: str) -> bytes:
    """Coerce a hex digest (with or without 0x) into exactly 32 bytes."""
    if isinstance(value, bytes):
        raw = value
    else:
        text = str(value)
        if text.startswith("0x"):
            text = text[2:]
        if len(text) < 64:
            text = text.rjust(64, "0")
        raw = bytes.fromhex(text[:64])
    if len(raw) != 32:
        raise ValueError("expected a 32-byte value")
    return raw


blockchain_service = BlockchainService()

__all__ = ["BlockchainService", "SubmissionResult", "blockchain_service"]
