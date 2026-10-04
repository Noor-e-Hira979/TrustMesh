"""Blockchain routes: status, commitments, manual confirmation, ABI, hashing.

Two invariants enforced here:

1. **No response can contain a secret.** ``blockchain_service.status()`` returns an
   explicitly allow-listed summary, and there is deliberately no field anywhere in this
   module for a private key or an API key.
2. **A chain failure is a status, never a 500.** Spec §22 and §34 require blockchain
   problems to be visible without making the whole application look broken, so every
   failure path here returns a payload describing the state.

Every database call runs through ``app.core.database.run_db``, i.e. on a worker thread,
so no SQLite I/O ever blocks the event loop.

Referenced by: ``app.main``, the frontend Blockchain Explorer page.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agreements.hashing import describe as describe_hashing
from app.agreements.hashing import rfc8785_test_vectors, selftest as hashing_selftest
from app.blockchain.contract import (
    NEVER_STORED_ON_CHAIN,
    ON_CHAIN_PAYLOAD,
    load_abi,
    transaction_explorer_url,
)
from app.blockchain.service import blockchain_service
from app.core.config import settings
from app.core.database import run_db
from app.core.models import AgreementCommitment
from app.core.schemas import BlockchainStatusOut, CommitmentOut, HashingInfoOut, Page

logger = logging.getLogger("trustmesh.blockchain-api")

router = APIRouter(tags=["blockchain"])


@router.get("/blockchain/status", response_model=BlockchainStatusOut)
async def blockchain_status() -> BlockchainStatusOut:
    """Connection state, chain identity and commitment counts.

    Probes the node on every call, so the UI reflects an RPC that has gone away without
    needing a restart.
    """
    probe = await blockchain_service.probe()
    current = blockchain_service.status()
    current["connection"] = probe.get("connection", current["connection"])
    if probe.get("latest_block") is not None:
        current["latest_block"] = probe["latest_block"]
    if probe.get("error"):
        current["notes"].append(f"Probe: {probe['error']}")

    counts = await run_db(blockchain_service.counts)
    return BlockchainStatusOut(
        network=current["network"],
        chain_id=current["chain_id"],
        contract_address=current["contract"]["address"],
        mode=current["mode"],
        connection=current["connection"],
        rpc_configured=current["rpc_configured"],
        private_key_configured=current["private_key_configured"],
        confirmations_required=current["confirmations_required"],
        latest_block=current["latest_block"],
        explorer_url=current["explorer_url"],
        commitment_counts=counts,
        notes=current["notes"],
        checked_at=datetime.now(timezone.utc),
    )


@router.get("/blockchain/commitments", response_model=Page, summary="Agreement commitments")
async def list_commitments(
    status_filter: str | None = Query(
        default=None,
        alias="status",
        pattern="^(PENDING|CONFIRMED|FAILED|SIMULATED)$",
        description="Filter by commitment status",
    ),
    transaction_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page:
    stmt = select(AgreementCommitment).order_by(desc(AgreementCommitment.created_at))
    if status_filter:
        stmt = stmt.where(AgreementCommitment.status == status_filter.upper())
    if transaction_id:
        stmt = stmt.where(AgreementCommitment.transaction_id == transaction_id)

    def _query(session: Session) -> Page:
        from sqlalchemy import func

        total = int(
            session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
            .scalar()
            or 0
        )
        rows = list(
            session.execute(stmt.limit(limit).offset(offset)).scalars().all()
        )
        return Page.build(
            [CommitmentOut.from_row(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    return await run_db(_query)


@router.get(
    "/blockchain/commitments/{commitment_id}",
    response_model=CommitmentOut,
    summary="One commitment",
)
async def get_commitment(commitment_id: str) -> CommitmentOut:
    def _query(session: Session) -> CommitmentOut:
        row = session.get(AgreementCommitment, commitment_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commitment '{commitment_id}' not found.",
            )
        return CommitmentOut.from_row(row)

    return await run_db(_query)


@router.post(
    "/blockchain/commitments/{commitment_id}/confirm",
    summary="Re-poll a pending commitment",
)
async def confirm_commitment(commitment_id: str) -> dict:
    """Force a receipt re-check.

    Always returns 200 with the resulting state. An unreachable node leaves the
    commitment ``PENDING`` with an explanatory error, because a pending commitment is a
    normal condition rather than a request failure (spec §34).
    """

    def _confirm(session: Session) -> dict:
        row = session.get(AgreementCommitment, commitment_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Commitment '{commitment_id}' not found.",
            )

        # ``BlockchainService.confirm_sync`` owns the receipt poll and the row update.
        # web3.py is blocking, so it is synchronous by design; ``run_db`` has already put
        # this callback on a worker thread, which is what makes calling it inline safe.
        result = blockchain_service.confirm_sync(session, row)

        return {
            "commitment_id": row.id,
            "transaction_id": row.transaction_id,
            "status": result.status,
            "ok": result.ok,
            "mode": result.mode,
            "tx_hash": result.tx_hash,
            "block_number": result.block_number,
            "block_timestamp": result.block_timestamp,
            "confirmations": result.confirmations,
            "error": result.error,
            "explorer_url": result.explorer_url
            or transaction_explorer_url(row.network, row.tx_hash),
            "note": (
                "A chain failure never stops TrustMesh enforcement. The UI shows the pending "
                "or failed state and the rest of the system keeps working (spec 34)."
            ),
        }

    return await run_db(_confirm)


@router.get("/blockchain/abi", summary="TrustMeshRegistry ABI")
async def contract_abi() -> dict:
    return {
        "contract": "TrustMeshRegistry",
        "source": "contracts/contracts/TrustMeshRegistry.sol",
        "network": settings.blockchain_network,
        "chain_id": settings.blockchain_chain_id,
        "address": settings.blockchain_contract_address,
        "deployed": bool(settings.blockchain_contract_address),
        "abi": load_abi(),
    }


@router.get(
    "/blockchain/hashing",
    response_model=HashingInfoOut,
    summary="Canonicalisation and hash self-test",
)
async def hashing_info() -> dict:
    """Exposes the RFC 8785 self-test so the UI can prove the hash is reproducible."""
    return {
        **describe_hashing(),
        "selftest_passed": hashing_selftest(),
        "vectors": rfc8785_test_vectors(),
        "on_chain_payload": list(ON_CHAIN_PAYLOAD),
        "never_stored_on_chain": list(NEVER_STORED_ON_CHAIN),
    }
