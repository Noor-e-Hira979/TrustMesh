"""ABI binding for the TrustMeshRegistry contract.

The ABI is **read from the compiled Hardhat artifact on disk** - it is never embedded in
this file. A hardcoded copy could silently drift from the deployed bytecode, which would
produce decoding failures that look like a contract bug but are actually a stale interface.

Resolution order (first hit wins)
---------------------------------
1. ``contracts/artifacts/contracts/TrustMeshRegistry.sol/TrustMeshRegistry.json``
   The Hardhat build artifact - the single source of truth, always in sync with the
   bytecode that was actually deployed.
2. ``backend/app/blockchain/abi/TrustMeshRegistry.abi.json``
   The exported copy that ``contracts$ npm run deploy`` (or ``npm run export:abi``)
   writes. Used when the contracts workspace is not present, e.g. a backend-only
   deployment.

If neither exists, :func:`load_abi` raises :class:`AbiArtifactError` with the exact
remediation command. Callers that must not fail hard (:class:`ContractHandle`) capture the
message instead so ``/api/health`` and ``/api/blockchain/status`` degrade visibly rather
than returning a 500 or, worse, reporting a broken contract as healthy.

Referenced by: ``app.blockchain.service``, ``app.api.routes.blockchain``,
``scripts/verify_chain.py``.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("trustmesh.contract")

# ``contract.py`` lives at backend/app/blockchain/contract.py, so:
#   parents[0] = backend/app/blockchain   parents[1] = backend/app
#   parents[2] = backend                  parents[3] = <project root>
BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(__file__).resolve().parents[3]

#: Exported ABI consumed by the backend when present.
ABI_PATH = Path(__file__).resolve().parent / "abi" / "TrustMeshRegistry.abi.json"

#: The Hardhat build artifact, preferred because it cannot drift from the deployed code.
HARDHAT_ARTIFACT_PATH = (
    PROJECT_ROOT
    / "contracts"
    / "artifacts"
    / "contracts"
    / "TrustMeshRegistry.sol"
    / "TrustMeshRegistry.json"
)

CONTRACT_NAME = "TrustMeshRegistry"

#: Substrings that must be present for the ABI to be usable by the backend. Checking this
#: turns "stale or wrong contract's ABI" into an immediate, explicit error instead of an
#: opaque web3 decoding failure later.
REQUIRED_ABI_MEMBERS: frozenset[str] = frozenset(
    {
        "commitAgreement",
        "commitmentCount",
        "commitments",
        "getCommitment",
        "existsByTransactionRef",
        "AgreementCommitted",
    }
)

#: Explorer base URLs by network name. An empty string means "no public explorer", which is
#: correct for a local development chain.
EXPLORERS: dict[str, str] = {
    "sepolia": "https://sepolia.etherscan.io",
    "holesky": "https://holesky.etherscan.io",
    "mumbai": "https://mumbai.etherscan.io",
    "amoy": "https://amoy.polygonscan.com",
    "base_sepolia": "https://sepolia.basescan.org",
    "hardhat": "",
    "localhost": "",
}

#: Exactly what TrustMeshRegistry stores. Single source of truth for the data-minimisation
#: guarantee, served by both ``/api/blockchain/hashing`` and
#: ``/api/transactions/hashing/describe`` so they cannot drift apart.
ON_CHAIN_PAYLOAD: tuple[str, ...] = (
    "agreementHash (bytes32) - SHA-256 of the RFC 8785 canonical agreement",
    "transactionRef (bytes32) - SHA-256 of the TrustMesh transaction id",
    "buyerAgentRef (bytes32) - SHA-256 of the buyer agent id (pseudonymous)",
    "supplierAgentRef (bytes32) - SHA-256 of the supplier agent id (pseudonymous)",
    "committedAt (uint64) - block timestamp, so the chain is the time authority",
)

#: What the registry deliberately never stores. Mirrors
#: ``TrustMeshRegistry.neverStoredOnChain()`` in the Solidity source.
NEVER_STORED_ON_CHAIN: tuple[str, ...] = (
    "credentials",
    "API keys",
    "prices and quantities",
    "negotiation transcripts",
    "organization legal names",
    "any confidential business data",
)

_REMEDIATION = (
    "Run 'npm install && npm run compile' in contracts/, then "
    "'npm run export:abi' to regenerate backend/app/blockchain/abi/"
    f"{ABI_PATH.name}."
)


class AbiArtifactError(RuntimeError):
    """The compiled ABI could not be located or is unusable."""


def _abi_from_hardhat_artifact(path: Path) -> list[dict[str, Any]]:
    """Extract the ABI array from a Hardhat build artifact."""
    artifact = json.loads(path.read_text(encoding="utf-8"))
    abi = artifact.get("abi")
    if isinstance(abi, dict) and isinstance(abi.get("abi"), list):
        abi = abi["abi"]  # some tools wrap it
    if not isinstance(abi, list) or not abi:
        raise AbiArtifactError(f"{path} does not contain a non-empty 'abi' array.")
    return abi


def _abi_from_exported_file(path: Path) -> list[dict[str, Any]]:
    """Read a bare ABI array, tolerating a `{ "abi": [...] }` wrapper."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("abi"), list):
        payload = payload["abi"]
    if not isinstance(payload, list) or not payload:
        raise AbiArtifactError(f"{path} does not contain a non-empty ABI array.")
    return payload


def _validate(abi: list[dict[str, Any]], source: Path) -> list[dict[str, Any]]:
    """Fail loudly if this is not the ABI the backend expects."""
    names = {entry.get("name") for entry in abi if isinstance(entry, dict)}
    missing = sorted(REQUIRED_ABI_MEMBERS - names)
    if missing:
        raise AbiArtifactError(
            f"{source} is missing ABI entries the backend depends on: {', '.join(missing)}. "
            + _REMEDIATION
        )
    return abi


@lru_cache(maxsize=1)
def load_abi() -> list[dict[str, Any]]:
    """Load the compiled ABI from disk.

    Cached because the artifact cannot change while the process is running. Call
    :func:`reset_abi_cache` after recompiling or redeploying.

    Raises:
        AbiArtifactError: when no usable ABI artifact can be found.
    """
    attempts: list[tuple[Path, Any]] = [
        (HARDHAT_ARTIFACT_PATH, _abi_from_hardhat_artifact),
        (ABI_PATH, _abi_from_exported_file),
    ]

    problems: list[str] = []
    for path, reader in attempts:
        if not path.exists():
            problems.append(f"{path} not found")
            continue
        try:
            abi = _validate(reader(path), path)
        except (AbiArtifactError, json.JSONDecodeError, OSError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        logger.info(
            "loaded %d ABI entries from %s", len(abi), path.relative_to(PROJECT_ROOT)
            if PROJECT_ROOT in path.parents
            else path,
        )
        return abi

    raise AbiArtifactError(
        "TrustMeshRegistry ABI could not be loaded. Tried: "
        + "; ".join(problems)
        + ". "
        + _REMEDIATION
    )


def reset_abi_cache() -> None:
    """Clear the cached ABI. Call after recompiling or redeploying the contract."""
    load_abi.cache_clear()


def try_load_abi() -> tuple[list[dict[str, Any]], str | None]:
    """Non-raising variant. Returns ``(abi, error_message)``."""
    try:
        return load_abi(), None
    except AbiArtifactError as exc:
        return [], str(exc)


def abi_source() -> str | None:
    """Which file the cached ABI came from. Useful for diagnostics."""
    try:
        load_abi()
    except AbiArtifactError:
        return None
    if HARDHAT_ARTIFACT_PATH.exists():
        return str(HARDHAT_ARTIFACT_PATH)
    return str(ABI_PATH)


# ------------------------------------------------------------------ explorers
def explorer_url(network: str, kind: str, value: str) -> str | None:
    base = EXPLORERS.get(network, "")
    if not base or not value:
        return None
    return f"{base}/{kind}/{value}"


def transaction_explorer_url(network: str, tx_hash: str | None) -> str | None:
    return explorer_url(network, "tx", tx_hash or "")


def address_explorer_url(network: str, address: str | None) -> str | None:
    return explorer_url(network, "address", address or "")


class ContractHandle:
    """Everything needed to talk to ``TrustMeshRegistry``.

    Never raises on a missing ABI: the failure is captured in :attr:`abi_error` and
    surfaced through :meth:`as_dict`, so the API reports a precise, actionable state
    instead of crashing or silently pretending the contract is fine.
    """

    def __init__(self, address: str | None, chain_id: int, network: str) -> None:
        self.address = address
        self.chain_id = chain_id
        self.network = network
        self.abi, self.abi_error = try_load_abi()

    @property
    def deployed(self) -> bool:
        return bool(self.address and self.address.startswith("0x") and len(self.address) == 42)

    @property
    def ready(self) -> bool:
        """True only when both an address and a usable ABI are present."""
        return self.deployed and bool(self.abi) and self.abi_error is None

    def explorer_link(self) -> str | None:
        return address_explorer_url(self.network, self.address)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": CONTRACT_NAME,
            "address": self.address,
            "chain_id": self.chain_id,
            "network": self.network,
            "deployed": self.deployed,
            "ready": self.ready,
            "explorer_url": self.explorer_link(),
            "abi_entries": len(self.abi),
            "abi_source": abi_source(),
            "abi_error": self.abi_error,
            "source": "contracts/contracts/TrustMeshRegistry.sol",
        }
