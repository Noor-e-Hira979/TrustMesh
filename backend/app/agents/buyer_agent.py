"""Buyer agent runtime.

Holds the buyer's *intent* and its *authority envelope*. It has no policy logic and
no credential access: every action it wants to take is submitted to
``app.trust.trust_engine.TrustEngine.request_action`` and only executed on ALLOW.

Referenced by: ``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.agent_registry import BUYER_AGENT_ID, credential_for, name_of
from app.core.events import EventType, dispatcher
from app.security.policy_engine import ProposedTerms, TransactionConstraints
from app.trust import identity as identity_mod
from app.trust.trust_engine import ActionRequest, GatewayDecision, trust_engine


@dataclass(slots=True)
class BuyerIntent:
    product: str
    quantity: int
    max_budget_cents: int
    max_delivery_days: int
    certification_required: str = "VALID"
    intent_ref: str = ""
    transaction_id: str | None = None

    def constraints(self) -> TransactionConstraints:
        return TransactionConstraints(
            product=self.product,
            quantity=self.quantity,
            budget_cents=self.max_budget_cents,
            max_delivery_days=self.max_delivery_days,
            certification_required=self.certification_required,
            intent_ref=self.intent_ref,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "intent_ref": self.intent_ref,
            "product": self.product,
            "quantity": self.quantity,
            "max_budget_cents": self.max_budget_cents,
            "max_delivery_days": self.max_delivery_days,
            "certification_required": self.certification_required,
            "authority_note": (
                "These constraints were issued by the buyer organization. The agent may "
                "propose anything; only these bounds are enforceable."
            ),
        }


class BuyerAgent:
    """Persona wrapper around the buyer agent's registered identity."""

    agent_id = BUYER_AGENT_ID

    def __init__(self, intent: BuyerIntent) -> None:
        self.intent = intent
        self.constraints = intent.constraints()

    # ------------------------------------------------------------- discovery
    async def search_suppliers(self, session: Session, counterparty_ids: list[str]) -> None:
        """Capability ``search``. Enforcement happens even for read actions."""
        for counterparty_id in counterparty_ids:
            decision = await trust_engine.request_action(
                session,
                ActionRequest(
                    agent_id=self.agent_id,
                    capability="search",
                    action="discover_suppliers",
                    presented_secret=credential_for(self.agent_id),
                    claimed_org_id="org_buyer",
                    transaction_id=self.intent.transaction_id,
                    intent_ref=self.intent.intent_ref,
                    parameters={"counterparty_ids": counterparty_ids},
                ),
            )
            if decision.allowed:
                identity_mod.touch(session, self.agent_id, f"discovering suppliers ({decision.action_id})")
                session.commit()
                await dispatcher.publish(
                    EventType.AGENT_DISCOVERED,
                    transaction_id=self.intent.transaction_id,
                    agent_id=self.agent_id,
                    payload={
                        "discovered_agent_id": counterparty_id,
                        "discovered_agent_name": name_of(counterparty_id),
                        "action_id": decision.action_id,
                        "method": "capability:search",
                    },
                )
                await dispatcher.publish(
                    EventType.AGENT_ACTION_EXECUTED,
                    transaction_id=self.intent.transaction_id,
                    agent_id=self.agent_id,
                    payload={
                        "action": "discover_suppliers",
                        "capability": "search",
                        "decision": decision.decision,
                        "rule_id": decision.rule_id,
                        "result": [counterparty_id],
                        "why": decision.reason,
                    },
                )

    # ------------------------------------------------------------ evaluation
    def evaluate_offer(self, terms: ProposedTerms) -> ProposedTerms:
        """Pure comparison against the authority envelope. No side effects."""
        return terms

    async def request_quote(
        self, session: Session, supplier_agent_id: str, transaction_id: str
    ) -> GatewayDecision:
        """Capability ``request_quote`` - submitted to the Gateway, never self-approved."""
        return await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=self.agent_id,
                capability="request_quote",
                action="request_quote",
                presented_secret=credential_for(self.agent_id),
                claimed_org_id="org_buyer",
                transaction_id=transaction_id,
                intent_ref=self.intent.intent_ref,
                parameters={"supplier_agent_id": supplier_agent_id},
            ),
        )

    async def propose_terms(
        self,
        session: Session,
        transaction_id: str,
        *,
        price_cents: int,
        delivery_days: int,
        certification: str,
        quantity: int | None = None,
        supplier_agent_id: str | None = None,
    ) -> GatewayDecision:
        """Capability ``propose`` / ``negotiate`` - the buyer's counter-offer."""
        return await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=self.agent_id,
                capability="negotiate",
                action="submit_counter_offer",
                presented_secret=credential_for(self.agent_id),
                claimed_org_id="org_buyer",
                transaction_id=transaction_id,
                intent_ref=self.intent.intent_ref,
                parameters={
                    "price_cents": price_cents,
                    "delivery_days": delivery_days,
                    "certification": certification,
                    "quantity": quantity or self.intent.quantity,
                    "product": self.intent.product,
                    "counterparty_agent_id": supplier_agent_id,
                },
            ),
            constraints=self.constraints,
        )

    async def accept_agreement(
        self, session: Session, transaction_id: str, terms: ProposedTerms
    ) -> GatewayDecision:
        """Accept the final terms.

        Uses the buyer's ``propose`` capability - the buyer organization grants exactly
        four capabilities (search, request_quote, negotiate, propose), so acceptance is
        a proposal that both sides bind to. It is still policy-checked before it binds.
        """
        return await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=self.agent_id,
                capability="propose",
                action="accept_final_agreement",
                presented_secret=credential_for(self.agent_id),
                claimed_org_id="org_buyer",
                transaction_id=transaction_id,
                intent_ref=self.intent.intent_ref,
                parameters={
                    "price_cents": terms.price_cents,
                    "delivery_days": terms.delivery_days,
                    "certification": terms.certification,
                    "quantity": terms.quantity or self.intent.quantity,
                    "product": self.intent.product,
                },
            ),
            constraints=self.constraints,
        )
