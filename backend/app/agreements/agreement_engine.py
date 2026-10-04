"""Agreement engine.

Builds the final, frozen agreement structure from a locked transaction, canonicalises
it per RFC 8785 and produces the SHA-256 digest that gets committed on-chain.

Two invariants:
* The agreement contains **only** commercial terms and pseudonymous references. No
  credentials, no keys, no private negotiation transcript.
* The canonical JSON is stored alongside the hash so any third party can reproduce
  the digest byte-for-byte.

Referenced by: ``app.orchestrator.demo_orchestrator``, ``app.api.routes.transactions``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.agreements.hashing import (
    HASH_ALGORITHM,
    JCS_VERSION,
    agreement_digest,
    canonicalize,
)
from app.core.models import AgreementCommitment, CommitmentStatus, Transaction, utcnow

AGREEMENT_SCHEMA_VERSION = "trustmesh.agreement.v1"


@dataclass(slots=True)
class AgreementTerms:
    product: str
    quantity: int
    unit_price_cents: int
    total_price_cents: int
    currency: str
    delivery_days: int
    certification: str
    delivery_window_days: int


@dataclass(slots=True)
class Agreement:
    """The canonical agreement document."""

    schema_version: str
    transaction_id: str
    intent_ref: str
    buyer_org_id: str
    buyer_agent_id: str
    supplier_org_id: str
    supplier_agent_id: str
    terms: AgreementTerms
    negotiated_at: str
    policy: dict[str, Any] = field(default_factory=dict)

    def to_canonical_payload(self) -> dict[str, Any]:
        """The exact structure that is canonicalised and hashed.

        Key order in this dict is irrelevant - RFC 8785 sorts members - but the
        *set* of keys is the contract. Changing it changes every hash.
        """
        return {
            "schema_version": self.schema_version,
            "transaction_id": self.transaction_id,
            "intent_ref": self.intent_ref,
            "buyer": {
                "organization_id": self.buyer_org_id,
                "agent_id": self.buyer_agent_id,
            },
            "supplier": {
                "organization_id": self.supplier_org_id,
                "agent_id": self.supplier_agent_id,
            },
            "terms": {
                "product": self.terms.product,
                "quantity": self.terms.quantity,
                "unit_price_cents": self.terms.unit_price_cents,
                "total_price_cents": self.terms.total_price_cents,
                "currency": self.terms.currency,
                "delivery_days": self.terms.delivery_days,
                "certification": self.terms.certification,
                "delivery_window_days": self.terms.delivery_window_days,
            },
            "policy": self.policy,
            "negotiated_at": self.negotiated_at,
        }


@dataclass(slots=True)
class AgreementArtifact:
    agreement: Agreement
    canonical_json: str
    agreement_hash: str
    digest_bytes: bytes
    algorithm: str = HASH_ALGORITHM
    canonicalization: str = JCS_VERSION


def build_agreement(
    transaction: Transaction,
    *,
    supplier_agent_id: str,
    supplier_org_id: str,
    unit_price_cents: int,
    delivery_days: int,
    certification: str,
    policy: dict[str, Any] | None = None,
    negotiated_at: datetime | None = None,
) -> Agreement:
    """Assemble the agreement from a locked transaction."""
    stamp = (negotiated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return Agreement(
        schema_version=AGREEMENT_SCHEMA_VERSION,
        transaction_id=transaction.id,
        intent_ref=transaction.intent_ref,
        buyer_org_id=transaction.buyer_org_id,
        buyer_agent_id=transaction.buyer_agent_id,
        supplier_org_id=supplier_org_id,
        supplier_agent_id=supplier_agent_id,
        terms=AgreementTerms(
            product=transaction.product,
            quantity=transaction.quantity,
            unit_price_cents=unit_price_cents,
            total_price_cents=unit_price_cents * transaction.quantity,
            currency="USD",
            delivery_days=delivery_days,
            certification=certification,
            delivery_window_days=transaction.max_delivery_days,
        ),
        negotiated_at=stamp.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        policy=policy
        or {
            "engine": "deterministic-policy-engine",
            "rules_evaluated": [
                "COUNTERPARTY_ELIGIBILITY",
                "BUDGET_CEILING",
                "DELIVERY_SLA",
                "CERTIFICATION_VALIDITY",
                "QUANTITY_MATCH",
            ],
            "authority": "buyer_organization",
            "budget_cents": transaction.budget_cents,
            "max_delivery_days": transaction.max_delivery_days,
        },
    )


def canonicalize_agreement(agreement: Agreement) -> AgreementArtifact:
    """RFC 8785 canonicalise + SHA-256."""
    payload = agreement.to_canonical_payload()
    canonical_json = canonicalize(payload)
    digest = hashlib.sha256(canonical_json.encode("utf-8")).digest()
    return AgreementArtifact(
        agreement=agreement,
        canonical_json=canonical_json,
        agreement_hash="0x" + digest.hex(),
        digest_bytes=digest,
    )


def agent_reference(agent_id: str) -> str:
    """Pseudonymous, non-PII on-chain agent reference."""
    return "0x" + hashlib.sha256(agent_id.encode("utf-8")).hexdigest()


def transaction_reference(transaction_id: str) -> str:
    """32-byte on-chain reference for a TrustMesh transaction id."""
    return "0x" + hashlib.sha256(transaction_id.encode("utf-8")).hexdigest()


def persist_commitment(
    session: Session,
    transaction: Transaction,
    artifact: AgreementArtifact,
    *,
    network: str,
    chain_id: int,
    contract_address: str | None,
) -> AgreementCommitment:
    """Store the canonical agreement and its hash before any chain interaction."""
    agreement = artifact.agreement
    row = AgreementCommitment(
        transaction_id=transaction.id,
        agreement_hash=artifact.agreement_hash,
        canonical_json=artifact.canonical_json,
        algorithm=artifact.algorithm,
        canonicalization=artifact.canonicalization,
        buyer_agent_id=agreement.buyer_agent_id,
        supplier_agent_id=agreement.supplier_agent_id,
        buyer_org_id=agreement.buyer_org_id,
        supplier_org_id=agreement.supplier_org_id,
        network=network,
        chain_id=chain_id,
        contract_address=contract_address,
        status=CommitmentStatus.PENDING.value,
        created_at=utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def verify_artifact(canonical_json: str, expected_hash: str) -> tuple[bool, str]:
    """Recompute the digest from stored canonical JSON.

    This is the independent-verification path used by the UI: it does not trust the
    stored hash, it recomputes it from the bytes. ``canonical_json`` is *already*
    canonical, so it is hashed as-is - re-canonicalising it would wrap it in a JSON
    string and produce a different digest.
    """
    recomputed = "0x" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return recomputed.lower() == str(expected_hash).lower(), recomputed
