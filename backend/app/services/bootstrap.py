"""First-boot seeder.

Idempotently creates the organizations, agents and credentials from
``app.agents.agent_registry`` so the demo has a populated registry on first start.
Credentials are issued from the deterministic fixture secrets so the in-process demo
runtimes can verify; only the HMAC digest is stored.

Referenced by: ``app.main`` lifespan.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.agent_registry import (
    AGENTS,
    ORGANIZATIONS,
    credential_for,
)
from app.core.database import run_db
from app.core.models import Agent, Credential, Organization, VerificationStatus
from app.trust.credentials import issue_credential_with_secret, verify_credential

logger = logging.getLogger("trustmesh.bootstrap")


def _seed(session: Session) -> dict[str, int]:
    created_orgs = 0
    for seed in ORGANIZATIONS:
        exists = session.execute(
            select(Organization).where(Organization.id == seed.id)
        ).scalar_one_or_none()
        if exists is None:
            session.add(
                Organization(
                    id=seed.id,
                    legal_name=seed.legal_name,
                    country=seed.country,
                    trust_tier=seed.trust_tier,
                )
            )
            created_orgs += 1
    # Flush organizations before any agent or credential so the unit of work never has
    # to order Organization -> Agent -> Credential in a single pass.
    session.flush()

    created_agents = 0
    created_credentials = 0
    rotated_credentials = 0
    for seed in AGENTS:
        if seed.agent_type == "unknown":
            # The impersonation simulation is deliberately NOT in the registry.
            # Scenario 3 depends on the agent being genuinely unknown, so that
            # verification fails with IDENTITY_UNKNOWN_AGENT rather than a softer error.
            # The UI still renders the node from /api/agents/registry-catalogue.
            continue
        agent = session.execute(select(Agent).where(Agent.id == seed.id)).scalar_one_or_none()
        if agent is None:
            agent = Agent(
                id=seed.id,
                org_id=seed.org_id,
                display_name=seed.display_name,
                agent_type=seed.agent_type,
                model=seed.model,
                description=seed.description,
                capabilities=list(seed.capabilities),
                constraints=dict(seed.constraints),
                status="active",
                verification_status=VerificationStatus.UNVERIFIED.value,
                trust_tier="UNASSESSED",
                current_activity="registered, awaiting first contact",
                is_malicious_simulation=seed.is_malicious_simulation,
            )
            session.add(agent)
            created_agents += 1
            session.flush()

        if agent.credential_id is None:
            secret = credential_for(seed.id)
            if not secret:
                # The impostor is deliberately NOT registered, so it never receives a
                # credential. That absence is the point of scenario 3.
                continue
            credential = issue_credential_with_secret(
                session,
                agent_id=seed.id,
                org_id=seed.org_id,
                secret=secret,
                scope=list(seed.capabilities),
            )
            agent.credential_id = credential.id
            created_credentials += 1
            continue

        # Self-healing credential rotation.
        #
        # Credentials are stored as HMAC digests keyed by CREDENTIAL_HMAC_SECRET. Changing
        # that secret - which is exactly what happens the first time someone copies
        # .env.example to .env and sets a value - correctly invalidates every previously
        # issued credential. Without this check the registry would come up fully seeded
        # but unable to verify a single agent, and the demo would fail at identity
        # verification with no obvious cause. Re-issue whenever the stored digest no
        # longer matches the current secret.
        if verify_credential(
            session, agent_id=seed.id, presented_secret=credential_for(seed.id)
        ).valid:
            continue

        logger.warning(
            "credential for %s no longer verifies under the current HMAC secret; re-issuing",
            seed.id,
        )
        agent.credential_id = None
        session.flush()
        credential = issue_credential_with_secret(
            session,
            agent_id=seed.id,
            org_id=seed.org_id,
            secret=credential_for(seed.id),
            scope=list(seed.capabilities),
        )
        agent.credential_id = credential.id
        rotated_credentials += 1

    session.commit()
    return {
        "organizations": created_orgs,
        "agents": created_agents,
        "credentials": created_credentials,
        "credentials_rotated": rotated_credentials,
        "total_agents": int(session.execute(select(func.count(Agent.id))).scalar() or 0),
    }


async def seed_registry() -> dict[str, int]:
    result = await run_db(_seed)
    logger.info(
        "registry seeded: %s new agents, %s new credentials, %s agents total",
        result["agents"],
        result["credentials"],
        result["total_agents"],
    )
    return result


def reset_registry(session: Session) -> None:
    """Wipe demo data. Used by the ``--reset`` tooling and tests."""
    from app.core.models import (
        AgreementCommitment,
        Credential,
        EventLog,
        Offer,
        PolicyDecision,
        SecurityAlert,
        Transaction,
    )

    for model in (
        EventLog,
        AgreementCommitment,
        Offer,
        PolicyDecision,
        SecurityAlert,
        Transaction,
        Credential,
        Agent,
        Organization,
    ):
        session.execute(model.__table__.delete())
    session.commit()
    logger.warning("registry reset")


def credential_for_agent(session: Session, agent_id: str) -> Credential | None:
    agent = session.get(Agent, agent_id)
    if agent is None or agent.credential_id is None:
        return None
    return session.get(Credential, agent.credential_id)
