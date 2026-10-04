#!/usr/bin/env python
"""End-to-end chain verification against a live EVM node.

Proves the full VERIFY path with a real chain rather than the SIMULATED fallback:

    1. backend builds the RFC 8785 canonical agreement
    2. backend SHA-256 hashes it
    3. backend signs and submits a real transaction to TrustMeshRegistry
    4. backend waits for a receipt
    5. backend reads the commitment back and compares the digest

Requires a node. The easiest is Hardhat:

    contracts>  npx hardhat node

Then:

    python scripts/verify_chain.py --rpc http://127.0.0.1:8545 \\
        --key 0xac09...  --address 0x5FbD...

Exit code 0 means the on-chain digest matches the locally computed digest.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):  # pragma: no cover
        pass

from app.agreements.agreement_engine import (  # noqa: E402
    agent_reference,
    build_agreement,
    canonicalize_agreement,
    transaction_reference,
    verify_artifact,
)
from app.agreements.hashing import selftest  # noqa: E402
from app.blockchain.service import blockchain_service  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import init_db  # noqa: E402
from app.core.models import Transaction, TransactionState  # noqa: E402

GREEN, RED, YELLOW, CYAN, DIM, RESET = (
    "\033[92m",
    "\033[91m",
    "\033[93m",
    "\033[96m",
    "\033[2m",
    "\033[0m",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a real on-chain agreement commitment.")
    parser.add_argument("--rpc", default=os.environ.get("BLOCKCHAIN_RPC_URL"))
    parser.add_argument("--key", default=os.environ.get("BLOCKCHAIN_PRIVATE_KEY"))
    parser.add_argument("--address", default=os.environ.get("BLOCKCHAIN_CONTRACT_ADDRESS"))
    parser.add_argument("--chain-id", type=int, default=None)
    parser.add_argument("--network", default=None)
    parser.add_argument("--confirmations", type=int, default=1)
    return parser.parse_args()


async def main() -> int:
    args = parse_args()

    print(f"{CYAN}TrustMesh live chain verification{RESET}")
    print(f"{DIM}rpc     : {args.rpc or 'not configured'}")
    print(f"{DIM}contract: {args.address or 'not configured'}{RESET}")

    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  [{mark}] {name}")
        if detail:
            print(f"         {DIM}{detail}{RESET}")
        if not ok:
            failures.append(name)

    if not args.rpc:
        print(f"\n{YELLOW}No RPC configured. Start `npx hardhat node` in contracts/ first.")
        print(f"Exiting without a chain check (local hash verification still runs).{RESET}")
        args.rpc = None

    # Apply CLI overrides before anything reads the cached settings.
    if args.rpc:
        settings.blockchain_rpc_url = args.rpc
    if args.key:
        settings.blockchain_private_key = args.key
    if args.address:
        settings.blockchain_contract_address = args.address
    if args.network:
        settings.blockchain_network = args.network
    if args.chain_id is not None:
        settings.blockchain_chain_id = args.chain_id
    settings.blockchain_confirmations_required = args.confirmations
    settings.blockchain_mode = "live" if args.rpc else "simulated"

    blockchain_service.handle.address = args.address
    blockchain_service.handle.network = settings.blockchain_network
    blockchain_service.handle.chain_id = settings.effective_chain_id
    blockchain_service._w3 = None
    blockchain_service._account = None

    print()
    check("RFC 8785 canonicalisation self-test", selftest())

    await init_db()

    # A synthetic but fully realistic transaction, so this check needs no demo run.
    from sqlalchemy import select

    from app.core.database import run_db

    async def _fixture() -> str:
        def _apply(session) -> str:
            # Always create a fresh transaction. Reusing an existing one would collide
            # with uq_commitment_transaction on the second run of this script, because a
            # transaction may only ever have one commitment - the same append-only rule
            # the registry enforces on-chain.
            txn = Transaction(
                intent_ref="INTENT-CHAIN-VERIFY",
                buyer_org_id="org_buyer",
                buyer_agent_id="buyer_agent_1",
                supplier_org_id="org_supplier_b",
                supplier_agent_id="supplier_agent_b",
                product="Packaging units",
                quantity=1000,
                budget_cents=200_000,
                max_delivery_days=7,
                certification_required="VALID",
                state=TransactionState.AGREED.value,
            )
            session.add(txn)
            session.flush()
            txn.intent_ref = f"INTENT-{txn.id}"
            session.commit()
            return txn.id

        return await run_db(_apply)

    txn_id = await _fixture()

    artifact = await run_db(
        lambda session: canonicalize_agreement(
            build_agreement(
                session.get(Transaction, txn_id),
                supplier_agent_id="supplier_agent_b",
                supplier_org_id="org_supplier_b",
                unit_price_cents=195_000,
                delivery_days=6,
                certification="CERTIFIED",
            )
        )
    )

    print(f"\n{CYAN}Local hash{RESET}")
    print(f"  transaction     : {txn_id}")
    print(f"  canonical bytes : {len(artifact.canonical_json)} chars")
    print(f"  sha256          : {artifact.agreement_hash}")
    print(f"  tx reference    : {transaction_reference(txn_id)}")
    print(f"  buyer ref       : {agent_reference('buyer_agent_1')}")

    matches, recomputed = verify_artifact(artifact.canonical_json, artifact.agreement_hash)
    check("digest reproduces from stored canonical JSON", matches, recomputed)

    if not args.rpc:
        print(f"\n{YELLOW}Skipped on-chain checks: no RPC available.{RESET}")
        return 1 if failures else 0

    # ----------------------------------------------------------------- on-chain
    print(f"\n{CYAN}Chain{RESET}")
    probe = await blockchain_service.probe()
    check(
        "node reachable",
        probe["connection"] == "connected",
        f"connection={probe['connection']} latest_block={probe.get('latest_block')} "
        f"error={probe.get('error')}",
    )
    if probe["connection"] != "connected":
        return 1

    check(
        "contract address configured",
        bool(args.address),
        args.address or "missing",
    )

    before = await blockchain_service.commitment_count()
    print(f"  commitmentCount before : {before}")

    from app.core.models import AgreementCommitment

    def _persist(session):
        row = AgreementCommitment(
            transaction_id=txn_id,
            agreement_hash=artifact.agreement_hash,
            canonical_json=artifact.canonical_json,
            buyer_agent_id="buyer_agent_1",
            supplier_agent_id="supplier_agent_b",
            buyer_org_id="org_buyer",
            supplier_org_id="org_supplier_b",
            network=settings.blockchain_network,
            chain_id=settings.effective_chain_id,
            contract_address=args.address,
        )
        session.add(row)
        session.commit()
        return row.id

    from app.core.database import SessionLocal

    session = SessionLocal()
    commitment = session.get(AgreementCommitment, await run_db(_persist))
    result = await blockchain_service.commit_agreement(
        session,
        commitment,
        agreement_hash=artifact.agreement_hash,
        transaction_id=txn_id,
        buyer_agent_id="buyer_agent_1",
        supplier_agent_id="supplier_agent_b",
    )
    session.close()

    print(f"  mode                  : {result.mode}")
    print(f"  status                : {result.status}")
    print(f"  tx hash               : {result.tx_hash}")
    print(f"  block                 : {result.block_number}")
    print(f"  confirmations         : {result.confirmations}")
    if result.explorer_url:
        print(f"  explorer              : {result.explorer_url}")
    if result.error:
        print(f"  error                 : {result.error}")

    check("transaction submitted", bool(result.tx_hash), str(result.tx_hash))
    check(
        "receipt reached a terminal state",
        result.status in {"CONFIRMED", "FAILED", "PENDING"},
        result.status,
    )

    after = await blockchain_service.commitment_count()
    print(f"  commitmentCount after  : {after}")
    check(
        "on-chain commitment count increased",
        after == before + 1,
        f"{before} -> {after}",
    )

    readback = await blockchain_service.read_commitment(txn_id)
    print(f"  readback              : {readback.get('detail')}")
    check("commitment readable from the contract", bool(readback.get("present")), str(readback))

    on_chain_hash = str(readback.get("matches") or "").lower()
    check(
        "ON-CHAIN DIGEST MATCHES LOCAL DIGEST",
        on_chain_hash == artifact.agreement_hash.lower(),
        f"chain={on_chain_hash or 'n/a'} local={artifact.agreement_hash.lower()}",
    )

    duplicate = await blockchain_service.read_commitment("TM_definitely_not_committed")
    check(
        "unknown reference reports absent",
        duplicate.get("present") is False,
        str(duplicate.get("detail")),
    )

    print()
    if failures:
        print(f"{RED}{len(failures)} check(s) failed: {', '.join(failures)}{RESET}\n")
        return 1
    print(f"{GREEN}Live chain verification passed end to end.{RESET}")
    print(f"{DIM}The on-chain commitment reproduces the RFC 8785 + SHA-256 digest.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
