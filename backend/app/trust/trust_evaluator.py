"""Trust Evaluator (spec §8).

Explicitly NOT a reputation scorer. There is no float, no weighting curve and no
history-derived arithmetic that a judge cannot audit. The output is:

* a list of labelled, explained factors, and
* a tier derived from those factors by a fixed lookup.

"Previous verified interaction history" participates as a *labelled factor*
("2 prior verified interactions"), not as a score.

Referenced by: ``app.trust.trust_engine``, ``app.api.routes.agents``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.models import Agent, AgreementCommitment, SecurityAlert, Transaction

TrustTier = Literal["TRUSTED", "CONDITIONAL", "UNTRUSTED", "UNASSESSED"]

TIER_EXPLANATION: dict[str, str] = {
    "TRUSTED": "Identity, credential, capability, authorization and policy all verified.",
    "CONDITIONAL": "Identity is established but at least one factor is degraded or unproven.",
    "UNTRUSTED": "At least one blocking factor: identity, credential, capability or threat.",
    "UNASSESSED": "The agent has not yet been evaluated against a live action request.",
}


@dataclass(slots=True)
class TrustFactor:
    factor: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"factor": self.factor, "status": self.status, "detail": self.detail}


@dataclass(slots=True)
class TrustEvaluation:
    agent_id: str
    organization_id: str
    tier: TrustTier
    summary: str
    factors: list[TrustFactor] = field(default_factory=list)
    blocking_factors: list[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "organization_id": self.organization_id,
            "tier": self.tier,
            "summary": self.summary,
            "factors": [f.as_dict() for f in self.factors],
            "blocking_factors": self.blocking_factors,
            "evaluated_at": self.evaluated_at.isoformat(),
            "disclaimer": (
                "Explainable rule-based evaluation. TrustMesh does not compute opaque "
                "reputation scores (spec §8)."
            ),
        }


_BLOCKING_PREFIXES = (
    "identity",
    "credential",
    "capability",
    "threat",
    "authorization",
)


def _derive_tier(factors: Sequence[TrustFactor], blocking: Sequence[str]) -> TrustTier:
    if blocking:
        return "UNTRUSTED"
    statuses = {f.factor: f.status for f in factors}
    if statuses.get("identity") != "VERIFIED":
        return "UNASSESSED"
    positive = {"VERIFIED", "VALID", "AUTHORIZED", "GRANTED", "COMPLIANT", "CLEAN", "MATCHED"}
    if all(status in positive for status in statuses.values() if status):
        return "TRUSTED"
    return "CONDITIONAL"


def evaluate(
    session: Session,
    agent: Agent,
    *,
    identity_status: str | None = None,
    credential_status: str | None = None,
    capability_status: str | None = None,
    authorization_status: str | None = None,
    threat_status: str | None = None,
    policy_status: str | None = None,
    requested_capability: str | None = None,
    blocking_factors: list[str] | None = None,
) -> TrustEvaluation:
    """Build an explainable evaluation from named factors only."""
    factors: list[TrustFactor] = []
    blocking: list[str] = list(blocking_factors or [])

    identity_status = identity_status or agent.verification_status.upper()
    identity_detail = {
        "VERIFIED": f"Registered agent of organization '{agent.org_id}' with a matched org binding.",
        "REJECTED": "A previous identity check rejected this agent's claimed organization.",
        "UNVERIFIED": "No identity verification has been completed for this agent yet.",
    }.get(identity_status, f"Identity status reported as {identity_status}.")
    factors.append(TrustFactor("identity", identity_status, identity_detail))
    if identity_status == "REJECTED":
        blocking.append("identity_rejected")

    credential_status = credential_status or (
        "VALID" if agent.credential_id else "MISSING"
    )
    credential_detail = (
        "Active HMAC-digest credential bound to this agent and organization."
        if agent.credential_id
        else "No credential is registered for this agent."
    )
    factors.append(TrustFactor("credential", credential_status, credential_detail))
    if credential_status in {"INVALID", "REVOKED", "EXPIRED", "MISSING"}:
        blocking.append("credential_invalid")

    declared = [str(c) for c in (agent.capabilities or [])]
    capability_status = capability_status or "DECLARED"
    capability_detail = (
        f"Authorized capabilities: {', '.join(declared) or 'none'}."
        if requested_capability is None
        else f"Requested capability '{requested_capability}' against grants: "
        f"{', '.join(declared) or 'none'}."
    )
    factors.append(TrustFactor("capability", capability_status, capability_detail))
    if capability_status in {"NOT_AUTHORIZED", "DENIED"}:
        blocking.append("capability_not_authorized")

    factors.append(
        TrustFactor(
            "authorization",
            authorization_status or "NOT_EVALUATED",
            "Authorization is evaluated per action by the Gateway, not cached as a verdict."
            if authorization_status is None
            else f"Authorization for this action: {authorization_status}.",
        )
    )
    if authorization_status == "DENIED":
        blocking.append("authorization_denied")

    factors.append(
        TrustFactor(
            "threat",
            threat_status or "NOT_EVALUATED",
            "Threat interception runs on every request; no signals so far."
            if threat_status in (None, "CLEAN")
            else f"Threat interception result: {threat_status}.",
        )
    )
    if threat_status == "DETECTED":
        blocking.append("threat_detected")

    if policy_status is None:
        factors.append(
            TrustFactor(
                "policy",
                "NOT_EVALUATED",
                "Deterministic policy has not been applied to a proposal from this agent yet.",
            )
        )
    else:
        factors.append(
            TrustFactor(
                "policy",
                policy_status,
                "Last deterministic policy verdict: "
                f"{'compliant' if policy_status == 'COMPLIANT' else 'violating'}.",
            )
        )
        if policy_status == "VIOLATION":
            blocking.append("policy_violation")

    prior_txns = (
        session.execute(
            select(func.count(Transaction.id)).where(
                Transaction.id.in_(
                    select(SecurityAlert.transaction_id).where(
                        SecurityAlert.agent_id == agent.id,
                        SecurityAlert.transaction_id.is_not(None),
                    )
                )
            )
        ).scalar()
        or 0
    )
    completed = (
        session.execute(
            select(func.count(AgreementCommitment.id))
            .join(Transaction, Transaction.id == AgreementCommitment.transaction_id)
            .where(
                (Transaction.buyer_agent_id == agent.id)
                | (Transaction.supplier_agent_id == agent.id)
            )
        ).scalar()
        or 0
    )
    prior_blocks = (
        session.execute(
            select(func.count(SecurityAlert.id)).where(
                SecurityAlert.agent_id == agent.id,
                SecurityAlert.decision == "BLOCK",
            )
        ).scalar()
        or 0
    )
    factors.append(
        TrustFactor(
            "interaction_history",
            "OBSERVED" if completed else "NONE",
            (
                f"{completed} verified agreement(s) completed; {prior_blocks} blocked action(s) "
                f"on record across {prior_txns} transaction(s) involving security events."
            ),
        )
    )

    deduped_blocking: list[str] = []
    for item in blocking:
        if item not in deduped_blocking:
            deduped_blocking.append(item)

    tier = _derive_tier(factors, deduped_blocking)
    return TrustEvaluation(
        agent_id=agent.id,
        organization_id=agent.org_id,
        tier=tier,
        summary=TIER_EXPLANATION[tier],
        factors=factors,
        blocking_factors=deduped_blocking,
    )
