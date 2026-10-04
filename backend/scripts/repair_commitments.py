"""Reconcile stored commitments against the chain.

The pre-fix receipt poller recorded a permanent FAILED status for transactions that were
actually mined: web3.py v7 raises TransactionNotFound while a tx is still in the mempool
instead of returning None, and the exception escaped the polling loop. For every such row
the tx hash survives only inside error_message, so it can be recovered and re-verified.

Usage:  python -m scripts.repair_commitments
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.blockchain.service import blockchain_service  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.models import AgreementCommitment  # noqa: E402

TX_IN_ERROR = re.compile(r"0x[0-9a-fA-F]{64}")


async def run() -> None:
    recovered = 0
    rechecked = 0

    with SessionLocal() as session:
        rows = (
            session.query(AgreementCommitment)
            .filter(AgreementCommitment.status.in_(["FAILED", "PENDING"]))
            .all()
        )
        for row in rows:
            if not row.contract_address or row.network == "localhost":
                continue
            if not row.tx_hash:
                match = TX_IN_ERROR.search(row.error_message or "")
                if not match:
                    continue
                row.tx_hash = match.group(0)
                recovered += 1
                print(f"recovered tx_hash for {row.id}: {row.tx_hash}")

            before = row.status
            result = await blockchain_service.confirm(session, row)
            print(
                f"{row.id} [{row.network}]: {before} -> {result.status} "
                f"(block={result.block_number} confs={result.confirmations})"
            )
            rechecked += 1

    print(f"done: {rechecked} row(s) rechecked, {recovered} tx hash(es) recovered")


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
