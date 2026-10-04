"""Agent and organization identity verification.

Implements spec §2 questions 1-3 and §5 SCENARIO 3 (identity mismatch /
impersonation). A counterparty may *claim* an identity; this module decides whether
the claim is consistent with the registered, credential-bound truth.

Referenced by: ``app.trust.trust_engine``, ``app.agents.agent_registry``,
``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import (
    Agent,
    AgentStatus,
    Credential,
    Organization,
    VerificationStatus,
    utcnow,
)
from app.trust.credentials import verify_credential


@dataclass(slots=True)
class IdentityResult:
    verified: bool
    agent_id: str
    claimed_org_id: str | None
    registered_org_id: str | None = None
    organization_name: str | None = None
    reason: str = ""
    rule_id: str = ""
    credential_id: str | None = None
    credential_scope: list[str] = field(default_factory=list)
    factors: dict[str, str] = field(default_factory=dict)

    def as_evidence(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "claimed_org_id": self.claimed_org_id,
            "registered_org_id": self.registered_org_id,
            "organization_name": self.organization_name,
            "credential_id": self.credential_id,
            "rule_id": self.rule_id,
            "factors": self.factors,
        }


def get_agent(session: Session, agent_id: str) -> Agent | None:
    return session.execute(select(Agent).where(Agent.id == agent_id)).scalar_one_or_none()


def get_organization(session: Session, org_id: str) -> Organization | None:
    return session.execute(
        select(Organization).where(Organization.id == org_id)
    ).scalar_one_or_none()


def verify_identity(
    session: Session,
    *,
    agent_id: str,
    claimed_org_id: str | None = None,
    presented_secret: str | None = None,
    require_credential: bool = True,
) -> IdentityResult:
    """Three checks: agent known -> org binding matches -> credential valid."""
    agent = get_agent(session, agent_id)

    if agent is None:
        return IdentityResult(
            verified=False,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            reason=(
                f"Agent '{agent_id}' is not present in the TrustMesh registry. "
                "An unknown agent cannot act on behalf of any organization."
            ),
            rule_id="IDENTITY_UNKNOWN_AGENT",
            factors={"registry_lookup": "FAILED", "org_binding": "UNKNOWN", "credential": "UNKNOWN"},
        )

    registered_org = agent.org_id
    org = get_organization(session, registered_org) if registered_org else None

    if agent.status == AgentStatus.BLOCKED.value:
        return IdentityResult(
            verified=False,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            registered_org_id=registered_org,
            organization_name=org.legal_name if org else None,
            reason=f"Agent '{agent_id}' is administratively blocked by TrustMesh.",
            rule_id="IDENTITY_AGENT_BLOCKED",
            credential_id=agent.credential_id,
            factors={"registry_lookup": "OK", "org_binding": "OK", "credential": "BLOCKED"},
        )

    if claimed_org_id and claimed_org_id != registered_org:
        return IdentityResult(
            verified=False,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            registered_org_id=registered_org,
            organization_name=org.legal_name if org else None,
            reason=(
                f"Agent claims to represent '{claimed_org_id}' but is registered to "
                f"'{registered_org}'. Agent identity does not match verified organization identity."
            ),
            rule_id="IDENTITY_MISMATCH",
            credential_id=agent.credential_id,
            factors={
                "registry_lookup": "OK",
                "org_binding": "MISMATCH",
                "credential": "NOT_CHECKED",
            },
        )

    if not require_credential:
        return IdentityResult(
            verified=True,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            registered_org_id=registered_org,
            organization_name=org.legal_name if org else None,
            reason="Agent is registered and its organization binding is consistent.",
            rule_id="IDENTITY_VERIFIED",
            credential_id=agent.credential_id,
            factors={"registry_lookup": "OK", "org_binding": "OK", "credential": "SKIPPED"},
        )

    credential_row = (
        session.get(Credential, agent.credential_id) if agent.credential_id else None
    )
    if credential_row is None:
        return IdentityResult(
            verified=False,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            registered_org_id=registered_org,
            organization_name=org.legal_name if org else None,
            reason=f"Agent '{agent_id}' has no registered credential.",
            rule_id="IDENTITY_NO_CREDENTIAL",
            factors={"registry_lookup": "OK", "org_binding": "OK", "credential": "MISSING"},
        )

    verification = verify_credential(
        session,
        agent_id=agent_id,
        presented_secret=presented_secret or "",
        org_id=registered_org,
    )
    if not verification.valid:
        return IdentityResult(
            verified=False,
            agent_id=agent_id,
            claimed_org_id=claimed_org_id,
            registered_org_id=registered_org,
            organization_name=org.legal_name if org else None,
            reason=verification.reason,
            rule_id=verification.rule_id,
            credential_id=verification.credential_id,
            factors={"registry_lookup": "OK", "org_binding": "OK", "credential": "FAILED"},
        )

    return IdentityResult(
        verified=True,
        agent_id=agent_id,
        claimed_org_id=claimed_org_id,
        registered_org_id=registered_org,
        organization_name=org.legal_name if org else None,
        reason=(
            f"Agent '{agent_id}' is registered to {org.legal_name if org else registered_org} "
            "and presented a valid, unexpired credential."
        ),
        rule_id="IDENTITY_VERIFIED",
        credential_id=verification.credential_id,
        credential_scope=list(verification.scope or []),
        factors={
            "registry_lookup": "OK",
            "org_binding": "MATCHED",
            "credential": "VALID",
            "credential_id": verification.credential_id or "unknown",
        },
    )


def mark_verified(session: Session, agent_id: str, verified: bool) -> None:
    agent = get_agent(session, agent_id)
    if agent is None:
        return
    agent.verification_status = (
        VerificationStatus.VERIFIED.value if verified else VerificationStatus.REJECTED.value
    )
    agent.last_seen_at = utcnow()
    session.flush()


def touch(session: Session, agent_id: str, activity: str) -> None:
    agent = get_agent(session, agent_id)
    if agent is None:
        return
    agent.current_activity = activity[:160]
    agent.last_seen_at = datetime.now(utcnow().tzinfo)
    session.flush()
