"""Controlled impersonation simulation (spec §5 SCENARIO 3).

An *unregistered* agent claims to represent Supplier A. It has no registry entry and
no credential, so the Gateway rejects it at the identity stage. Nothing is forged,
exfiltrated or attacked - the agent simply makes a claim and TrustMesh refuses it.

Referenced by: ``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.agent_registry import IMPOSTOR_AGENT_ID
from app.core.events import EventType, dispatcher
from app.trust import identity as identity_mod
from app.trust.trust_engine import ActionRequest, GatewayDecision, trust_engine

logger = logging.getLogger("trustmesh.impersonation")


@dataclass(slots=True)
class ImpersonationAttempt:
    claimed_agent_id: str
    claimed_org_id: str
    presented_agent_id: str
    action: str
    parameters: dict[str, Any]
    message: str


class ImpersonatorAgent:
    """Makes a false identity claim. It never receives an ALLOW."""

    agent_id = IMPOSTOR_AGENT_ID

    def build_attempt(self, config: dict[str, Any]) -> ImpersonationAttempt:
        return ImpersonationAttempt(
            claimed_agent_id=str(config.get("claims_agent_id", "")),
            claimed_org_id=str(config.get("claims_org_id", "")),
            presented_agent_id=self.agent_id,
            action="submit_offer_under_impersonated_identity",
            parameters=dict(config.get("offer", {})),
            message=str(config.get("message", "")),
        )

    async def attempt(
        self, session: Session, attempt: ImpersonationAttempt, transaction_id: str
    ) -> GatewayDecision:
        """Submit the impersonated offer to the Gateway and return the rejection.

        ``claimed_agent_id`` is carried in the parameters as the *claimed* identity;
        the request is presented by ``unknown_agent_1``, which is not in the registry.
        """
        await dispatcher.publish(
            EventType.AGENT_DISCOVERED,
            transaction_id=transaction_id,
            agent_id=self.agent_id,
            payload={
                "discovered_agent_id": self.agent_id,
                "discovered_agent_name": "Unknown Agent (impersonation attempt)",
                "method": "unverified_inbound_claim",
                "note": "Unregistered agent presented a claim on the network.",
            },
        )

        decision = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=self.agent_id,
                capability="request_quote",
                action=attempt.action,
                presented_secret=None,
                claimed_org_id=attempt.claimed_org_id,
                transaction_id=transaction_id,
                parameters={
                    "claimed_agent_id": attempt.claimed_agent_id,
                    "claimed_org_id": attempt.claimed_org_id,
                    "price_cents": attempt.parameters.get("price_cents"),
                    "delivery_days": attempt.parameters.get("delivery_days"),
                    "certification": attempt.parameters.get("certification"),
                    "message": attempt.message,
                },
            ),
            constraints=None,
        )
        logger.info(
            "impersonation attempt blocked: %s (%s)", decision.decision, decision.rule_id
        )
        return decision
