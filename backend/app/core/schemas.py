"""Pydantic v2 request/response/serialisation contracts.

Three hard rules encoded here:

1. **No secret can leave the backend.** There is deliberately no field anywhere in this
   module for a private key, an API key or raw credential material. Every model below is
   constructed from ORM attributes or from explicit, allow-listed dictionaries.
2. **Validate at the boundary.** Every externally-supplied structure is checked before it
   can reach the trust layer, so malformed input is rejected safely rather than reaching
   the Policy Engine (spec §34).
3. **Money is integer cents.** No monetary field is a float. ``*_usd`` values exist only
   as display helpers and are never used for a policy comparison.

Referenced by: every module under ``app.api.routes``, ``app.trust.trust_evaluator``,
``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ORM = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------- shared
class CapabilityGrant(BaseModel):
    """One capability grant as issued by an organization to its agent."""

    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    scope: list[str] = Field(default_factory=list, max_length=50)
    granted_by: str = Field(default="registry", max_length=120)
    constraints: dict[str, Any] = Field(default_factory=dict)


class AgentConstraints(BaseModel):
    """Business constraints attached to a registered agent."""

    model_config = ConfigDict(extra="allow")

    max_budget_cents: int | None = Field(default=None, ge=0)
    max_delivery_days: int | None = Field(default=None, gt=0)
    min_certification: str | None = None


class Money(BaseModel):
    """Integer-cents money pair.

    ``cents`` is the value of record. ``usd`` is derived for display and must never be
    used in a Policy Engine comparison.
    """

    cents: int = Field(ge=0)

    @property
    def usd(self) -> float:
        return self.cents / 100

    @classmethod
    def format_usd(cls, cents: int | None) -> str:
        """Shared display formatter so every page renders money identically."""
        if cents is None:
            return "-"
        return f"${cents / 100:,.2f}"


# ---------------------------------------------------------------------- agents
class AgentOut(BaseModel):
    model_config = ORM

    id: str
    org_id: str
    display_name: str
    agent_type: str
    model: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    status: str
    verification_status: str
    trust_tier: str
    current_activity: str
    is_malicious_simulation: bool
    last_seen_at: datetime | None = None


class OrganizationOut(BaseModel):
    model_config = ORM

    id: str
    legal_name: str
    country: str
    trust_tier: str
    created_at: datetime


class TrustFactor(BaseModel):
    factor: str
    status: str
    detail: str
    weight_note: str = "explanatory only - not a numeric score"


class TrustEvaluationOut(BaseModel):
    agent_id: str
    organization_id: str
    tier: Literal["TRUSTED", "CONDITIONAL", "UNTRUSTED", "UNASSESSED"]
    summary: str
    factors: list[TrustFactor]
    blocking_factors: list[str] = Field(default_factory=list)
    evaluated_at: datetime
    # One-way truncation of the credential *id*. Never the secret and never reversible.
    credential_fingerprint: str = "none"
    disclaimer: str = (
        "Explainable rule-based evaluation. TrustMesh does not compute opaque "
        "reputation scores (spec 8)."
    )


# --------------------------------------------------------------- transactions
class ProcurementIntentIn(BaseModel):
    product: str = Field(min_length=1, max_length=160)
    quantity: int = Field(gt=0, le=10_000_000)
    max_budget_cents: int = Field(gt=0)
    max_delivery_days: int = Field(gt=0, le=365)
    certification_required: str = Field(default="VALID", max_length=64)
    buyer_org_id: str = "org_buyer"
    buyer_agent_id: str = "buyer_agent_1"
    notes: str = Field(default="", max_length=2000)

    @field_validator("certification_required")
    @classmethod
    def _normalise_cert(cls, value: str) -> str:
        return value.strip().upper() or "VALID"

    @model_validator(mode="after")
    def _sanity(self) -> "ProcurementIntentIn":
        if self.max_budget_cents > 100_000_000_00:
            raise ValueError("max_budget_cents exceeds the prototype ceiling of $100,000,000")
        return self


class OfferOut(BaseModel):
    model_config = ORM

    id: str
    transaction_id: str
    agent_id: str
    organization_id: str
    round_number: int
    price_cents: int
    delivery_days: int
    quantity: int
    certification: str
    offer_type: str
    rationale: str
    ai_mode: str
    created_at: datetime

    @property
    def price_usd(self) -> float:
        return self.price_cents / 100


class PolicyDecisionOut(BaseModel):
    model_config = ORM

    id: str
    transaction_id: str | None
    agent_id: str
    organization_id: str | None
    action_id: str
    capability: str
    decision: str
    stage: str
    rule_id: str
    reason: str
    evidence: dict[str, Any]
    intent_ref: str
    created_at: datetime


class SecurityAlertOut(BaseModel):
    model_config = ORM

    id: str
    transaction_id: str | None
    agent_id: str
    agent_name: str
    organization_id: str
    organization_name: str
    severity: str
    alert_type: str
    requested_action: str
    requested_capability: str
    decision: str
    reason: str
    evidence: dict[str, Any]
    policy_rule_id: str
    stage: str
    status: str
    is_simulation: bool
    created_at: datetime


class CommitmentOut(BaseModel):
    model_config = ORM

    id: str
    transaction_id: str
    agreement_hash: str
    canonical_json: str
    algorithm: str
    canonicalization: str
    buyer_agent_id: str
    supplier_agent_id: str
    buyer_org_id: str
    supplier_org_id: str
    network: str
    chain_id: int
    contract_address: str | None
    tx_hash: str | None
    block_number: int | None
    block_timestamp: int | None
    confirmations: int
    status: str
    error_message: str | None
    submitted_at: datetime | None
    confirmed_at: datetime | None
    created_at: datetime
    explorer_url: str | None = None
    contract_explorer_url: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "CommitmentOut":
        """Serialise a stored commitment with its block-explorer links resolved.

        The links are derived from the row's own ``network`` rather than the currently
        configured one, so a commitment recorded on one network still renders the correct
        explorer after ``BLOCKCHAIN_NETWORK`` changes. Both links are ``None`` for networks
        with no configured explorer.
        """
        from app.blockchain.contract import (
            address_explorer_url,
            transaction_explorer_url,
        )

        payload = cls.model_validate(row)
        payload.explorer_url = transaction_explorer_url(row.network, row.tx_hash)
        payload.contract_explorer_url = address_explorer_url(
            row.network, row.contract_address
        )
        return payload


class AgreementVerifyOut(BaseModel):
    transaction_id: str
    agreement_hash: str
    recomputed_hash: str
    hash_matches: bool
    canonicalization: str
    algorithm: str
    canonical_json: str
    on_chain_present: bool | None = None
    on_chain_matches: bool | None = None
    chain_status: str | None = None
    explorer_url: str | None = None
    verified_at: datetime
    notes: list[str] = Field(default_factory=list)


class TimelineStep(BaseModel):
    """One lifecycle beat, annotated with the three-layer attribution (§17)."""

    seq: int
    layer: Literal["AGENT", "TRUSTMESH", "BLOCKCHAIN"]
    stage: str
    title: str
    detail: str
    outcome: Literal["INFO", "ALLOW", "BLOCK", "SUCCESS", "WARNING", "ERROR"]
    agent_id: str | None = None
    event_type: str | None = None
    ts: datetime
    meta: dict[str, Any] = Field(default_factory=dict)


class TransactionDetailOut(BaseModel):
    id: str
    intent_ref: str
    buyer_org_id: str
    buyer_agent_id: str
    supplier_org_id: str | None
    supplier_agent_id: str | None
    product: str
    quantity: int
    budget_cents: int
    max_delivery_days: int
    certification_required: str
    notes: str
    state: str
    ai_mode: str
    negotiation_round: int
    block_count: int
    created_at: datetime
    updated_at: datetime
    offers: list[OfferOut] = Field(default_factory=list)
    decisions: list[PolicyDecisionOut] = Field(default_factory=list)
    alerts: list[SecurityAlertOut] = Field(default_factory=list)
    commitment: CommitmentOut | None = None


# ------------------------------------------------------------------- security
class SecurityStatsOut(BaseModel):
    total: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    blocked_actions: int
    open_alerts: int
    simulated_alerts: int = 0
    simulation_only: bool = True
    window_events: int = 0


class ScenarioResultOut(BaseModel):
    scenario: str
    transaction_id: str | None = None
    decision: str
    rule_id: str
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    alert_id: str | None = None
    severity: str | None = None
    events_emitted: list[str] = Field(default_factory=list)
    explanation: str = ""


# ----------------------------------------------------------------- blockchain
class BlockchainStatusOut(BaseModel):
    network: str
    chain_id: int
    contract_address: str | None
    mode: str
    connection: Literal["connected", "degraded", "simulated", "unavailable"]
    rpc_configured: bool
    private_key_configured: bool
    confirmations_required: int
    latest_block: int | None = None
    explorer_url: str | None = None
    commitment_counts: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    checked_at: datetime


class DemoRunRequest(BaseModel):
    step_delay_seconds: float = Field(default=0.55, ge=0.0, le=5.0)
    use_ai: bool = True
    commit_on_chain: bool = True
    fresh: bool = True


class DemoRunAccepted(BaseModel):
    transaction_id: str
    status: str
    detail: str
    stream_hint: str = "/api/events/stream"


# --------------------------------------------------------------------- health
class ComponentHealth(BaseModel):
    name: str
    status: Literal["up", "degraded", "down"]
    detail: str
    mode: str | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    environment: str
    uptime_seconds: float
    event_seq: int
    sse_subscribers: int
    ai_mode: str
    blockchain_mode: str
    database: str
    components: list[ComponentHealth] = Field(default_factory=list)
    checked_at: datetime


class ReadinessOut(BaseModel):
    ready: bool
    checks: dict[str, bool]
    checked_at: datetime


class ErrorOut(BaseModel):
    detail: str
    code: str | None = None


# --------------------------------------------------------------------- events
class EventOut(BaseModel):
    """Serialised form of one bus frame, as delivered over SSE or the REST fallback.

    ``seq`` is the monotonic replay cursor: the SSE ``id:`` field and this value are the
    same number, which is what makes ``Last-Event-ID`` reconnection lossless.
    """

    seq: int = Field(ge=0)
    type: str = Field(min_length=1, max_length=48)
    ts: datetime
    transaction_id: str | None = None
    agent_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventFeedOut(BaseModel):
    events: list[EventOut] = Field(default_factory=list)
    count: int = 0
    latest_seq: int = 0


class BusStatsOut(BaseModel):
    running: bool
    seq: int
    subscribers: int
    history_size: int
    buffer_size: int
    persist_queue_depth: int = 0
    dropped_persists: int = 0
    heartbeat_seconds: float | None = None
    observed_at: datetime | None = None


class EventTypesOut(BaseModel):
    """Answers "which events exist, which layer owns them, and which did the spec mandate?"

    ``layers`` is what lets the frontend colour and group the live feed by authority
    (spec §17) without re-deriving it from event names.
    """

    all: list[str]
    mandated_by_specification: list[str]
    additive_for_observability: list[str]
    layers: dict[str, str] = Field(default_factory=dict)
    blocking: list[str] = Field(default_factory=list)
    bus: dict[str, Any]


# ------------------------------------------------------------------------- ai
class AIStatusOut(BaseModel):
    """Provenance of the reasoning layer, so the UI can show which engine spoke.

    ``last_mode`` is ``"gemini"`` or ``"demo"``. The ``authority`` field is fixed text on
    purpose: it is the reminder that the AI never approves anything (spec §6).
    """

    configured: bool
    model: str
    prompt_version: str
    last_mode: Literal["gemini", "demo"]
    unavailable_reason: str | None = None
    authority: str = "non-authoritative: reasoning and language only, never a verdict"


class ReasoningOut(BaseModel):
    """One ``AI_REASONING`` frame: language only, plus its provenance."""

    mode: Literal["gemini", "demo"]
    model: str
    agent_id: str | None = None
    rationale: str
    highlights: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    authority: str = "non-authoritative: supplies language only, never a verdict"


# ----------------------------------------------------------------- catalogues
class RuleDescriptor(BaseModel):
    """One deterministic rule, exposed so the UI can render the rule catalogue."""

    rule_id: str
    expression: str
    description: str


class ThreatRuleDescriptor(BaseModel):
    code: str
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    description: str


class SecurityRulesOut(BaseModel):
    policy_rules: list[RuleDescriptor]
    threat_rules: list[ThreatRuleDescriptor]
    sentinel_capabilities: list[str]
    identity_rules: list[str]
    credential_rules: list[str]
    authorization_rules: list[str]
    capability_rules: list[str]
    scenarios: dict[str, str]
    note: str


class HashingInfoOut(BaseModel):
    """Machine-checkable description of how the agreement hash is produced."""

    canonicalization: str
    algorithm: str
    implementation: str
    key_order: str
    number_format: str
    string_escaping: str
    selftest_passed: bool
    vectors: list[dict[str, Any]] = Field(default_factory=list)
    on_chain_payload: list[str] = Field(default_factory=list)
    never_stored_on_chain: list[str] = Field(default_factory=list)


class ArchitectureOut(BaseModel):
    """The enforcement pipeline, machine readable, for the About page."""

    principle: str
    pipeline: list[str]
    authority: dict[str, str]
    policy_rules: list[RuleDescriptor]
    threat_rules: list[ThreatRuleDescriptor]
    sentinel_capabilities: list[str]
    known_capabilities: list[str]
    canonicalization: dict[str, str]


# ------------------------------------------------------------------ pagination
class Page(BaseModel):
    """Uniform envelope for every list endpoint.

    Bare arrays cannot express "there are more" or "this is page 3 of 9", so every
    collection route returns this instead. ``next_cursor`` is the value to pass as
    ``cursor`` for the following page; ``None`` means the end of the collection.
    """

    items: list[Any] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)
    next_offset: int | None = None
    has_more: bool = False

    @classmethod
    def build(
        cls,
        items: list[Any],
        *,
        total: int,
        limit: int,
        offset: int,
    ) -> "Page":
        consumed = offset + len(items)
        has_more = consumed < total
        return cls(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
            next_offset=consumed if has_more else None,
            has_more=has_more,
        )


class PaginationParams(BaseModel):
    """Validated ``limit``/``offset`` pair shared by every collection route."""

    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)

    @property
    def end(self) -> int:
        return self.offset + self.limit


# -------------------------------------------------------------------- overview
class OverviewStat(BaseModel):
    """One headline number plus its label and trend context."""

    label: str
    value: str
    detail: str = ""
    tone: Literal["neutral", "good", "warn", "bad", "accent"] = "neutral"


class OverviewOut(BaseModel):
    """Everything spec §18 requires on the dashboard, in one round-trip.

    The Overview page must let a judge understand the system in seconds, so this
    aggregates the status cards, active agents, current transaction, negotiation state,
    recent security events, recent commitments, live activity and system health rather
    than making the browser fan out across seven endpoints on first paint.
    """

    principle: str = "REASON -> ENFORCE -> VERIFY"

    # §18 status cards: AI / Gemini, Trust Engine, Policy Engine, Blockchain
    components: list[ComponentHealth] = Field(default_factory=list)

    stats: list[OverviewStat] = Field(default_factory=list)

    active_agents: list[AgentOut] = Field(default_factory=list)
    agent_count: int = 0
    verified_agent_count: int = 0
    blocked_agent_count: int = 0

    current_transaction: TransactionDetailOut | None = None
    negotiation_state: str = "idle"
    negotiation_round: int = 0

    recent_alerts: list[SecurityAlertOut] = Field(default_factory=list)
    recent_commitments: list[CommitmentOut] = Field(default_factory=list)
    recent_events: list[EventOut] = Field(default_factory=list)

    blockchain_connection: str = "unknown"
    ai_mode: str = "demo"
    event_seq: int = 0
    sse_subscribers: int = 0

    demo_available: bool = True
    generated_at: datetime
    disclaimer: str = (
        "Hackathon prototype. Threat scenarios are simulated. No production readiness or "
        "certification is claimed."
    )


__all__ = [
    "AIStatusOut",
    "AgentConstraints",
    "AgentOut",
    "AgreementVerifyOut",
    "ArchitectureOut",
    "BlockchainStatusOut",
    "BusStatsOut",
    "CapabilityGrant",
    "CommitmentOut",
    "ComponentHealth",
    "DemoRunAccepted",
    "DemoRunRequest",
    "ErrorOut",
    "EventFeedOut",
    "EventOut",
    "EventTypesOut",
    "HashingInfoOut",
    "HealthOut",
    "Money",
    "OfferOut",
    "OrganizationOut",
    "OverviewOut",
    "OverviewStat",
    "Page",
    "PaginationParams",
    "PolicyDecisionOut",
    "ProcurementIntentIn",
    "ReadinessOut",
    "ReasoningOut",
    "RuleDescriptor",
    "ScenarioResultOut",
    "SecurityAlertOut",
    "SecurityRulesOut",
    "SecurityStatsOut",
    "ThreatRuleDescriptor",
    "TimelineStep",
    "TransactionDetailOut",
    "TrustEvaluationOut",
    "TrustFactor",
]
