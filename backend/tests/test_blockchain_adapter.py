"""Blockchain adapter tests.

Covers the decode logic against the *real* compiled ABI shape and the degraded /
simulated paths required by spec §12 and §34.

Referenced by: ``app.blockchain.service``, ``app.blockchain.contract``.
"""

from __future__ import annotations

import pytest

from app.blockchain import contract as contract_mod
from app.blockchain.contract import ContractHandle, load_abi, transaction_explorer_url
from app.blockchain.service import (
    _split_get_commitment,
    _to_hex32,
    blockchain_service,
)
from app.core.config import settings

ZERO32 = "0x" + "00" * 32
HASH_A = "0x" + "ab" * 32
HASH_B = "0x" + "cd" * 32
REF = "0x" + "12" * 32
REGISTRANT = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

# Field order must match the Solidity struct in TrustMeshRegistry.sol.
STRUCT_FIELDS = (
    "agreementHash",
    "transactionRef",
    "buyerAgentRef",
    "supplierAgentRef",
    "committedAt",
    "commitBlock",
    "registrant",
)


def test_real_abi_is_loaded_from_the_compiled_artifact():
    abi = load_abi()
    names = {entry.get("name") or entry["type"] for entry in abi}
    assert "commitAgreement" in names
    assert "getCommitment" in names
    assert "AgreementCommitted" in names
    assert "commitmentCount" in names


def test_commit_agreement_signature_matches_the_backend_encoder():
    abi = load_abi()
    fragment = next(e for e in abi if e.get("name") == "commitAgreement")
    assert [i["type"] for i in fragment["inputs"]] == ["bytes32"] * 4
    assert [i["name"] for i in fragment["inputs"]] == [
        "agreementHash",
        "transactionRef",
        "buyerAgentRef",
        "supplierAgentRef",
    ]


def test_commitment_struct_is_never_mutable_through_the_abi():
    names = {entry.get("name") for entry in load_abi()}
    for forbidden in (
        "updateCommitment",
        "deleteCommitment",
        "revokeCommitment",
        "setCommitment",
    ):
        assert forbidden not in names


def test_decode_tuple_wrapped_abi_output():
    """The compiled ABI returns the struct as one tuple output plus the bool."""
    decoded = [
        (
            bytes.fromhex(HASH_A[2:]),
            bytes.fromhex(REF[2:]),
            bytes.fromhex(HASH_B[2:]),
            bytes.fromhex(HASH_B[2:]),
            1767225600,
            123456,
            REGISTRANT,
        ),
        True,
    ]
    record, exists = _split_get_commitment(decoded)
    assert exists is True
    assert _to_hex32(record["agreementHash"]) == HASH_A
    assert _to_hex32(record["transactionRef"]) == REF
    assert _to_hex32(record["buyerAgentRef"]) == HASH_B
    assert record["committedAt"] == 1767225600
    assert record["commitBlock"] == 123456
    assert record["registrant"] == REGISTRANT


def test_decode_dict_wrapped_abi_output():
    decoded = [{"agreementHash": HASH_A, "commitBlock": 7, "registrant": REGISTRANT}, True]
    record, exists = _split_get_commitment(decoded)
    assert exists is True
    assert _to_hex32(record["agreementHash"]) == HASH_A
    assert record["commitBlock"] == 7


def test_decode_flattened_abi_output():
    """A legacy flattened ABI must still decode correctly."""
    decoded = [
        bytes.fromhex(HASH_A[2:]),
        bytes.fromhex(REF[2:]),
        HASH_B,
        HASH_B,
        1767225600,
        123456,
        REGISTRANT,
        True,
    ]
    record, exists = _split_get_commitment(decoded)
    assert exists is True
    assert _to_hex32(record["agreementHash"]) == HASH_A
    assert _to_hex32(record["buyerAgentRef"]) == HASH_B
    assert record["committedAt"] == 1767225600
    assert record["commitBlock"] == 123456


def test_decode_reports_missing_commitment():
    decoded = [
        (
            bytes(32),
            bytes(32),
            bytes(32),
            bytes(32),
            0,
            0,
            "0x0000000000000000000000000000000000000000",
        ),
        False,
    ]
    record, exists = _split_get_commitment(decoded)
    assert exists is False
    assert _to_hex32(record["agreementHash"]) == ZERO32


def test_decode_infers_exists_when_the_flag_is_a_hex_string():
    """bool('0x00...') is True in Python, which would misreport a missing record."""
    zero_tuple = (bytes(32), bytes(32), bytes(32), bytes(32), 0, 0, REGISTRANT)
    record, exists = _split_get_commitment([zero_tuple, ZERO32])
    assert exists is False

    live_tuple = (
        bytes.fromhex(HASH_A[2:]),
        bytes.fromhex(REF[2:]),
        bytes(32),
        bytes(32),
        1,
        1,
        REGISTRANT,
    )
    record, exists = _split_get_commitment([live_tuple, ZERO32])
    assert exists is True


def test_decode_handles_empty_and_garbage_input():
    for garbage in ([], None, "nope", 42):
        record, exists = _split_get_commitment(garbage)
        assert exists is False
        assert isinstance(record, dict)


def test_to_hex32_normalises_bytes_and_short_hex():
    assert _to_hex32(bytes.fromhex("ab" * 32)) == HASH_A
    assert _to_hex32("0xABCD") == "0x" + "00" * 30 + "abcd"
    assert _to_hex32("abcd") == "0x" + "00" * 30 + "abcd"
    assert _to_hex32(None) == ZERO32


def test_explorer_urls_are_built_per_network():
    assert transaction_explorer_url("sepolia", "0xabc") == "https://sepolia.etherscan.io/tx/0xabc"
    assert transaction_explorer_url("hardhat", "0xabc") is None
    assert transaction_explorer_url("sepolia", None) is None


def test_contract_handle_reports_undeployed_state():
    handle = ContractHandle(None, 11155111, "sepolia")
    assert handle.deployed is False
    assert handle.as_dict()["address"] is None
    valid = ContractHandle("0x5FbDB2315678afecb367f032d93F642f64180aa3", 11155111, "sepolia")
    assert valid.deployed is True
    assert valid.explorer_link().endswith("/address/0x5FbDB2315678afecb367f032d93F642f64180aa3")


def test_status_never_exposes_a_private_key():
    status = blockchain_service.status()
    serialised = repr(status)
    # The key itself must never appear; only a boolean "is one configured" flag.
    if settings.blockchain_private_key:
        assert settings.blockchain_private_key not in serialised
        assert settings.blockchain_private_key[2:] not in serialised
    assert status["private_key_configured"] == bool(settings.blockchain_private_key)
    # Required keys must be present. Asserted as a subset rather than an exact set so a
    # new diagnostic field (e.g. abi_error) does not break this security check.
    assert {
        "name",
        "address",
        "chain_id",
        "network",
        "deployed",
        "ready",
        "explorer_url",
        "abi_entries",
        "abi_source",
        "abi_error",
        "source",
    } <= set(status["contract"])
    # abi_error is a diagnostic string and may name a filesystem path, never a secret.
    if status["contract"]["abi_error"]:
        assert "PRIVATE KEY" not in status["contract"]["abi_error"].upper()


@pytest.mark.asyncio
async def test_readback_is_contained_when_no_node_is_available():
    result = await blockchain_service.read_commitment("TM_missing")
    assert "available" in result
    assert "detail" in result
    assert result["matches"] in (None, ZERO32) or isinstance(result["matches"], str)


@pytest.mark.asyncio
async def test_probe_never_raises():
    result = await blockchain_service.probe()
    assert result["connection"] in {"connected", "degraded", "simulated", "unavailable"}


def test_effective_mode_resolution():
    assert settings.effective_blockchain_mode in {"live", "simulated"}
    if not settings.rpc_configured and settings.blockchain_mode == "auto":
        assert settings.effective_blockchain_mode == "simulated"
