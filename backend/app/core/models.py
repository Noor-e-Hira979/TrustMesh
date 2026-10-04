"""SQLAlchemy ORM models.

Mandated entities: ``Agent``, ``Transaction``, ``SecurityAlert``,
``AgreementCommitment``.

Supporting entities required to satisfy spec §2 (organization identity),
§3 (credentials), §19 (offer / counter-offer history, policy decisions) and
§31 (answerable audit questions): ``Organization``, ``Credential``, ``Offer``,
``PolicyDecision``, ``EventLog``.

Money is stored as integer cents everywhere. A float in the enforcement path is a
rounding-bypass bug waiting to happen.

Referenced by: ``app.core.database``, every route module, the bootstrap seeder and
the demo orchestrator.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------- enums
class AgentType(str, enum.Enum):
    BUYER = "buyer"
    SUPPLIER = "supplier"
    UNKNOWN = "unknown"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    OFFLINE = "offline"


class VerificationStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class CredentialStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class TransactionState(str, enum.Enum):
    CREATED = "created"
    DISCOVERING = "discovering"
    VERIFYING_IDENTITY = "verifying_identity"
    OFFERS_RECEIVED = "offers_received"
    EVALUATING = "evaluating"
    NEGOTIATING = "negotiating"
    AGREED = "agreed"
    HASHED = "hashed"
    COMMITTED = "committed"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class OfferType(str, enum.Enum):
    INITIAL = "initial"
    COUNTER = "counter"
    FINAL = "final"


class PolicyStage(str, enum.Enum):
    SCHEMA = "schema"
    IDENTITY = "identity"
    CREDENTIAL = "credential"
    CAPABILITY = "capability"
    AUTHORIZATION = "authorization"
    THREAT = "threat"
    POLICY = "policy"


class Decision(str, enum.Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class Severity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class CommitmentStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    SIMULATED = "SIMULATED"


class AlertStatus(str, enum.Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


# ------------------------------------------------------------------- entities
class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200))
    country: Mapped[str] = mapped_column(String(2), default="US")
    trust_tier: Mapped[str] = mapped_column(String(32), default="STANDARD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agents: Mapped[list["Agent"]] = relationship(back_populates="organization")


class Agent(Base):
    """A registered autonomous agent belonging to exactly one organization."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    agent_type: Mapped[str] = mapped_column(String(16), default=AgentType.SUPPLIER.value)
    model: Mapped[str] = mapped_column(String(64), default="deterministic")
    description: Mapped[str] = mapped_column(Text, default="")

    # Declared capability grant list. This is the authority ceiling for the agent.
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    # Business constraints the agent must respect (budget_cents, max_delivery_days...).
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default=AgentStatus.ACTIVE.value)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNVERIFIED.value
    )
    trust_tier: Mapped[str] = mapped_column(String(24), default="UNASSESSED")
    current_activity: Mapped[str] = mapped_column(String(160), default="idle")

    # Marks the controlled malicious / compromised simulations from spec §4.
    is_malicious_simulation: Mapped[bool] = mapped_column(Boolean, default=False)

    # Deliberately NOT a database-level ForeignKey. Agent.credential_id and
    # Credential.agent_id would form a circular constraint, which breaks flush
    # ordering when an agent is created together with its first credential. The
    # relationship below joins on the id explicitly instead.
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    organization: Mapped["Organization"] = relationship(back_populates="agents")
    credential: Mapped["Credential | None"] = relationship(
        primaryjoin="foreign(Agent.credential_id) == Credential.id",
        uselist=False,
        viewonly=True,
    )

    @property
    def org_name(self) -> str:
        return self.organization.legal_name if self.organization else self.org_id


class Credential(Base):
    """HMAC-digest-backed credential. The secret material never leaves the backend."""

    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), index=True)
    credential_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default=CredentialStatus.ACTIVE.value)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Viewonly counterpart of Agent.credential (both sides are many-to-one).
    agent: Mapped["Agent"] = relationship(foreign_keys=[agent_id], viewonly=True)


class Transaction(Base):
    """One B2B procurement transaction and its full lifecycle state."""

    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("TM"))
    intent_ref: Mapped[str] = mapped_column(String(64), default="")
    buyer_org_id: Mapped[str] = mapped_column(String(64), index=True)
    buyer_agent_id: Mapped[str] = mapped_column(String(64), index=True)
    supplier_org_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    supplier_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    product: Mapped[str] = mapped_column(String(160))
    quantity: Mapped[int] = mapped_column(Integer)
    budget_cents: Mapped[int] = mapped_column(Integer)
    max_delivery_days: Mapped[int] = mapped_column(Integer)
    certification_required: Mapped[str] = mapped_column(String(64), default="VALID")
    notes: Mapped[str] = mapped_column(Text, default="")

    state: Mapped[str] = mapped_column(String(32), default=TransactionState.CREATED.value)
    ai_mode: Mapped[str] = mapped_column(String(16), default="demo")
    negotiation_round: Mapped[int] = mapped_column(Integer, default=0)
    block_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    offers: Mapped[list["Offer"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    decisions: Mapped[list["PolicyDecision"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["SecurityAlert"]] = relationship(back_populates="transaction")
    commitment: Mapped["AgreementCommitment | None"] = relationship(
        back_populates="transaction", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Money is integer cents and quantity is a positive count. These constraints make
        # the schema enforce what the Policy Engine assumes, so a bad write cannot create
        # a row that the enforcement path would misread.
        CheckConstraint("quantity > 0", name="ck_transactions_quantity_positive"),
        CheckConstraint("budget_cents >= 0", name="ck_transactions_budget_non_negative"),
        CheckConstraint(
            "max_delivery_days > 0", name="ck_transactions_delivery_positive"
        ),
        CheckConstraint("negotiation_round >= 0", name="ck_transactions_round_non_negative"),
        CheckConstraint("block_count >= 0", name="ck_transactions_blocks_non_negative"),
        Index("ix_transactions_state_created", "state", "created_at"),
    )

    @property
    def budget_usd(self) -> float:
        """Display helper only. Never used for a policy comparison."""
        return self.budget_cents / 100


class Offer(Base):
    """An offer or counter-offer. Never stored on-chain."""

    __tablename__ = "offers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("OF"))
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str] = mapped_column(String(64))
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    price_cents: Mapped[int] = mapped_column(Integer)
    delivery_days: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    certification: Mapped[str] = mapped_column(String(64), default="VALID")
    offer_type: Mapped[str] = mapped_column(String(16), default=OfferType.INITIAL.value)
    rationale: Mapped[str] = mapped_column(Text, default="")
    ai_mode: Mapped[str] = mapped_column(String(16), default="demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transaction: Mapped["Transaction"] = relationship(back_populates="offers")

    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="ck_offers_price_non_negative"),
        CheckConstraint("delivery_days > 0", name="ck_offers_delivery_positive"),
        CheckConstraint("quantity > 0", name="ck_offers_quantity_positive"),
        CheckConstraint("round_number >= 0", name="ck_offers_round_non_negative"),
        Index("ix_offers_transaction_round", "transaction_id", "round_number"),
    )

    @property
    def price_usd(self) -> float:
        """Display helper only. Policy comparisons use ``price_cents``."""
        return self.price_cents / 100


class PolicyDecision(Base):
    """Durable record of one deterministic enforcement decision."""

    __tablename__ = "policy_decisions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("PD")
    )
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    organization_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_id: Mapped[str] = mapped_column(String(64), default="")
    capability: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(8), default=Decision.ALLOW.value)
    stage: Mapped[str] = mapped_column(String(16), default=PolicyStage.POLICY.value)
    rule_id: Mapped[str] = mapped_column(String(64), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    intent_ref: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transaction: Mapped["Transaction | None"] = relationship(back_populates="decisions")


class SecurityAlert(Base):
    """A visible, persisted security event. Drives the Security Center feed."""

    __tablename__ = "security_alerts"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("SA")
    )
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_name: Mapped[str] = mapped_column(String(120), default="")
    organization_id: Mapped[str] = mapped_column(String(64), default="")
    organization_name: Mapped[str] = mapped_column(String(160), default="")

    severity: Mapped[str] = mapped_column(String(16), default=Severity.MEDIUM.value, index=True)
    alert_type: Mapped[str] = mapped_column(String(64), index=True)
    requested_action: Mapped[str] = mapped_column(String(160), default="")
    requested_capability: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(8), default=Decision.BLOCK.value)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    policy_rule_id: Mapped[str] = mapped_column(String(64), default="")
    stage: Mapped[str] = mapped_column(String(16), default=PolicyStage.POLICY.value)
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.OPEN.value)
    is_simulation: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    transaction: Mapped["Transaction | None"] = relationship(back_populates="alerts")

    __table_args__ = (
        # The Security Center feed is ordered newest-first and filtered by severity.
        Index("ix_security_alerts_severity_created", "severity", "created_at"),
        Index("ix_security_alerts_transaction_created", "transaction_id", "created_at"),
    )


class AgreementCommitment(Base):
    """An RFC 8785 canonical agreement, its SHA-256 hash and its chain commitment.

    ``canonical_json`` is the reproducibility anchor: anyone can re-canonicalise it,
    re-hash it and compare against ``agreement_hash`` or against the chain.
    """

    __tablename__ = "agreement_commitments"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("AC")
    )
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True
    )
    agreement_hash: Mapped[str] = mapped_column(String(80), index=True)
    canonical_json: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(String(16), default="SHA-256")
    canonicalization: Mapped[str] = mapped_column(String(24), default="RFC8785")

    buyer_agent_id: Mapped[str] = mapped_column(String(64), default="")
    supplier_agent_id: Mapped[str] = mapped_column(String(64), default="")
    buyer_org_id: Mapped[str] = mapped_column(String(64), default="")
    supplier_org_id: Mapped[str] = mapped_column(String(64), default="")

    network: Mapped[str] = mapped_column(String(48), default="")
    chain_id: Mapped[int] = mapped_column(Integer, default=0)
    contract_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_timestamp: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmations: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default=CommitmentStatus.PENDING.value, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transaction: Mapped["Transaction"] = relationship(back_populates="commitment")

    __table_args__ = (
        # One commitment per transaction, mirroring the contract's append-only rule: a
        # transaction reference can be committed exactly once.
        UniqueConstraint("transaction_id", name="uq_commitment_transaction"),
        CheckConstraint("confirmations >= 0", name="ck_commitments_confirmations_non_negative"),
        CheckConstraint("chain_id >= 0", name="ck_commitments_chain_id_non_negative"),
        Index("ix_commitments_status_created", "status", "created_at"),
    )

    @property
    def is_terminal(self) -> bool:
        """True when the commitment will not change again."""
        return self.status in {
            CommitmentStatus.CONFIRMED.value,
            CommitmentStatus.FAILED.value,
            CommitmentStatus.SIMULATED.value,
        }

    @property
    def explorer_url(self) -> str | None:
        """Explorer deep link, resolved lazily so chain config stays out of the model."""
        from app.blockchain.contract import transaction_explorer_url

        return transaction_explorer_url(self.network, self.tx_hash)


class EventLog(Base):
    """Append-only event log. ``seq`` doubles as the SSE replay cursor."""

    __tablename__ = "event_log"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("EV")
    )
    seq: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    type: Mapped[str] = mapped_column(String(48), index=True)
    transaction_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_event_log_type_seq", "type", "seq"),
        # ``seq`` is the SSE replay cursor and must be monotonically increasing; the
        # unique constraint on the column is what makes duplicate cursors impossible.
        Index("ix_event_log_created", "created_at"),
    )


__all__ = [
    "Agent",
    "AgentStatus",
    "AgentType",
    "AgreementCommitment",
    "AlertStatus",
    "Base",
    "CommitmentStatus",
    "Credential",
    "CredentialStatus",
    "Decision",
    "EventLog",
    "Offer",
    "OfferType",
    "Organization",
    "PolicyDecision",
    "PolicyStage",
    "SecurityAlert",
    "Severity",
    "Transaction",
    "TransactionState",
    "VerificationStatus",
    "new_id",
    "utcnow",
]
