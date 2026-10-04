"""Agent and organization routes.

Read-only projections of the TrustMesh registry plus the explainable trust evaluation
required by spec §8.

Route map
---------
``GET /agents``                    registered agents, filterable by type
``GET /agents/registry-catalogue`` the seed catalogue, including the unregistered impostor
``GET /agents/{id}``               one agent
``GET /agents/{id}/trust``         explainable trust evaluation (no numeric score)
``GET /organizations``             organizations
``GET /organizations/{id}``        one organization with its agents

**No route here returns a credential, a credential digest or any other secret.**
Credentials are stored as HMAC digests and the only credential-derived value exposed
anywhere is ``credential_fingerprint``, which is a one-way truncation of the credential
*id* (not of its secret).

Referenced by: ``app.main``, the frontend Agent Network graph.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.agent_registry import AGENTS_BY_ID
from app.core.database import run_db
from app.core.models import Agent, Organization
from app.core.schemas import AgentOut, OrganizationOut, TrustEvaluationOut
from app.trust import trust_evaluator
from app.trust.credentials import credential_fingerprint

router = APIRouter(tags=["agents"])


def _fetch_agents(session: Session, *, agent_type: str | None = None) -> list[Agent]:
    stmt = select(Agent).order_by(Agent.agent_type, Agent.id)
    if agent_type:
        stmt = stmt.where(Agent.agent_type == agent_type)
    return list(session.execute(stmt).scalars().all())


def _require_agent(session: Session, agent_id: str) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Agent '{agent_id}' is not registered in the TrustMesh registry. "
                "An unregistered agent cannot hold capabilities."
            ),
        )
    return agent


@router.get("/agents", response_model=list[AgentOut], summary="Registered agents")
async def list_agents(
    agent_type: str | None = Query(
        default=None, pattern="^(buyer|supplier|unknown)$", description="Filter by agent type"
    ),
) -> list[AgentOut]:
    def _query(session: Session) -> list[AgentOut]:
        return [
            AgentOut.model_validate(a) for a in _fetch_agents(session, agent_type=agent_type)
        ]

    return await run_db(_query)


@router.get(
    "/agents/registry-catalogue",
    summary="Seed catalogue used to build the Agent Network graph",
)
async def registry_catalogue() -> dict:
    """Static description of every demo agent, including the ones not in the registry.

    The impostor from spec §5 SCENARIO 3 is deliberately absent from ``/agents`` because
    it is genuinely unregistered, so the graph needs this endpoint to render it as a
    rejected node.
    """
    return {
        "agents": [
            {
                "id": seed.id,
                "display_name": seed.display_name,
                "org_id": seed.org_id,
                "agent_type": seed.agent_type,
                "description": seed.description,
                "capabilities": seed.capabilities,
                "constraints": seed.constraints,
                "is_malicious_simulation": seed.is_malicious_simulation,
                "registered": seed.id != "unknown_agent_1",
            }
            for seed in AGENTS_BY_ID.values()
        ]
    }


@router.get("/agents/{agent_id}", response_model=AgentOut, summary="One agent")
async def get_agent(agent_id: str) -> AgentOut:
    def _query(session: Session) -> AgentOut:
        return AgentOut.model_validate(_require_agent(session, agent_id))

    return await run_db(_query)


@router.get(
    "/agents/{agent_id}/trust",
    response_model=TrustEvaluationOut,
    summary="Explainable trust evaluation (no numeric score)",
)
async def agent_trust(
    agent_id: str,
    capability: str | None = Query(
        default=None,
        description=(
            "Optional capability to evaluate against this agent's grants. Supplying it "
            "shows how the capability factor resolves for a concrete request."
        ),
    ),
) -> TrustEvaluationOut:
    """Evaluate an agent's trust factors.

    Passing ``capability`` scopes the evaluation to a concrete hypothetical request, so
    the UI can answer "what would happen if this agent asked for X?" without performing
    the request. Nothing here grants or approves anything.
    """

    def _query(session: Session) -> TrustEvaluationOut:
        agent = _require_agent(session, agent_id)

        capability_status = None
        if capability:
            from app.security import capability_guard

            capability_status = (
                "AUTHORIZED" if capability in capability_guard.effective_capabilities(agent) else "NOT_AUTHORIZED"
            )

        evaluation = trust_evaluator.evaluate(
            session,
            agent,
            capability_status=capability_status,
            requested_capability=capability,
        )
        payload = evaluation.as_dict()
        payload["agent_id"] = agent.id
        payload["organization_id"] = agent.org_id
        # One-way fingerprint of the credential id. Never the secret, never reversible.
        payload["credential_fingerprint"] = credential_fingerprint(agent.credential_id)
        return TrustEvaluationOut.model_validate(payload)

    return await run_db(_query)


@router.get("/organizations", response_model=list[OrganizationOut], summary="Organizations")
async def list_organizations() -> list[OrganizationOut]:
    def _query(session: Session) -> list[OrganizationOut]:
        rows = session.execute(select(Organization).order_by(Organization.id)).scalars().all()
        return [OrganizationOut.model_validate(o) for o in rows]

    return await run_db(_query)


@router.get(
    "/organizations/{org_id}",
    summary="One organization with its agents",
)
async def get_organization(org_id: str) -> dict:
    """Organization detail plus the agents it employs.

    This is the unit of cross-organizational trust in spec §2, so it is served as one
    payload rather than making the graph join client-side.
    """

    def _query(session: Session) -> dict:
        org = session.get(Organization, org_id)
        if org is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organization '{org_id}' not found.",
            )
        agents = list(
            session.execute(
                select(Agent).where(Agent.org_id == org_id).order_by(Agent.id)
            )
            .scalars()
            .all()
        )
        return {
            "organization": OrganizationOut.model_validate(org).model_dump(mode="json"),
            "agents": [AgentOut.model_validate(a).model_dump(mode="json") for a in agents],
            "agent_count": len(agents),
            "verified_count": sum(1 for a in agents if a.verification_status == "verified"),
        }

    return await run_db(_query)
