"""Supplier agent runtimes.

Three variants share one base class:
* ``SupplierAgent``        - honest counterparties (A and B).
* ``CompromisedAgent``     - spec §4 Supplier C, the controlled malicious simulation.

Every offer a supplier produces is submitted through the Gateway. The supplier
never decides whether its own offer is acceptable - that is the Policy Engine's job,
and in the Supplier C case the point of the demo is that the Policy Engine wins.

Referenced by: ``app.orchestrator.demo_orchestrator``, ``app.agents.negotiation``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.agent_registry import behaviour, credential_for
from app.core.events import EventType, dispatcher
from app.security.policy_engine import ProposedTerms, TransactionConstraints
from app.trust import identity as identity_mod
from app.trust.trust_engine import ActionRequest, GatewayDecision, trust_engine

logger = logging.getLogger("trustmesh.supplier")


@dataclass(slots=True)
class OfferTerms:
    price_cents: int
    delivery_days: int
    certification: str
    quantity: int
    round_number: int = 0
    offer_type: str = "initial"
    rationale: str = ""

    def to_proposed(self) -> ProposedTerms:
        return ProposedTerms(
            price_cents=self.price_cents,
            delivery_days=self.delivery_days,
            certification=self.certification,
            quantity=self.quantity,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_cents": self.price_cents,
            "delivery_days": self.delivery_days,
            "certification": self.certification,
            "quantity": self.quantity,
            "round_number": self.round_number,
            "offer_type": self.offer_type,
            "rationale": self.rationale,
        }


class SupplierAgent:
    """Honest counterparty with a price floor and a production slot."""

    def __init__(self, agent_id: str, org_id: str, quantity: int) -> None:
        self.agent_id = agent_id
        self.org_id = org_id
        self.quantity = quantity
        self._config = behaviour(agent_id)
        self.floor_cents = int(self._config.get("price_floor_cents", 0))
        self._round = 0

    # ------------------------------------------------------------- behaviour
    def initial_offer(self, *, ai_rationale: str | None = None) -> OfferTerms:
        return OfferTerms(
            price_cents=int(self._config["initial_price_cents"]),
            delivery_days=int(self._config["initial_delivery_days"]),
            certification=str(self._config.get("initial_certification", "VALID")),
            quantity=self.quantity,
            round_number=0,
            offer_type="initial",
            rationale=ai_rationale or str(self._config.get("rationale", "")),
        )

    def counter_offer(self, buyer_price_cents: int) -> OfferTerms:
        """Concede along the supplier's own schedule, never below its floor.

        The supplier does not snap to the buyer's number - it moves by its own
        concession policy, which is what makes the resulting agreement reproducible.
        """
        self._round += 1
        concession = int(self._config.get("concession_per_round_cents", 0))
        scheduled = int(self._config["initial_price_cents"]) - (concession * self._round)
        target = max(self.floor_cents, scheduled)
        accept_at_or_below = self._config.get("accept_at_or_below_cents")
        offer_type = (
            "final" if accept_at_or_below and target <= int(accept_at_or_below) else "counter"
        )
        return OfferTerms(
            price_cents=target,
            delivery_days=int(self._config.get("fixed_delivery_days", 6)),
            certification=str(self._config.get("initial_certification", "VALID")),
            quantity=self.quantity,
            round_number=self._round,
            offer_type=offer_type,
            rationale=(
                f"Round {self._round}: our commercial policy moves us to "
                f"${target / 100:,.2f} on volume while holding the "
                f"{self._config.get('fixed_delivery_days', 6)}-day certified slot."
            ),
        )

    def accepts(self, price_cents: int) -> bool:
        accept_at_or_below = self._config.get("accept_at_or_below_cents")
        if accept_at_or_below is None:
            return price_cents >= self.floor_cents
        return price_cents <= int(accept_at_or_below)

    # ----------------------------------------------------------- enforcement
    async def submit_offer(
        self,
        session: Session,
        transaction_id: str,
        terms: OfferTerms,
        *,
        buyer_agent_id: str,
        buyer_org_id: str,
        constraints: TransactionConstraints,
    ) -> GatewayDecision:
        """Submit an offer for policy evaluation. Returns the GatewayDecision."""
        identity_mod.touch(session, self.agent_id, f"submitting {terms.offer_type} offer")
        session.commit()

        decision = await trust_engine.request_action(
            session,
            ActionRequest(
                agent_id=self.agent_id,
                capability="negotiate" if terms.offer_type != "initial" else "propose",
                action=f"submit_{terms.offer_type}_offer",
                presented_secret=credential_for(self.agent_id),
                claimed_org_id=self.org_id,
                transaction_id=transaction_id,
                intent_ref=constraints.intent_ref,
                parameters={
                    "price_cents": terms.price_cents,
                    "delivery_days": terms.delivery_days,
                    "certification": terms.certification,
                    "quantity": terms.quantity,
                    "counterparty_agent_id": buyer_agent_id,
                    "target_org_id": buyer_org_id,
                    "inbound_to_agent": True,
                    "rationale": terms.rationale,
                },
            ),
            constraints=constraints,
        )

        event_type = EventType.OFFER_RECEIVED if terms.offer_type == "initial" else EventType.OFFER_UPDATED
        await dispatcher.publish(
            event_type,
            transaction_id=transaction_id,
            agent_id=self.agent_id,
            payload={
                "action_id": decision.action_id,
                "offer": terms.as_dict(),
                "decision": decision.decision,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "severity": decision.severity,
            },
        )
        if decision.allowed:
            await dispatcher.publish(
                EventType.AGENT_ACTION_EXECUTED,
                transaction_id=transaction_id,
                agent_id=self.agent_id,
                payload={
                    "action": f"submit_{terms.offer_type}_offer",
                    "capability": decision.capability,
                    "decision": decision.decision,
                    "rule_id": decision.rule_id,
                    "result": terms.as_dict(),
                    "why": decision.reason,
                },
            )
        return decision


class CompromisedAgent(SupplierAgent):
    """Controlled malicious/compromised simulation (spec §4 Supplier C).

    This class contains **no attack capability**. Every method below merely builds a
    scripted ``ActionRequest`` and submits it to the Gateway, which is exactly the
    behaviour a legitimate agent has. The requests are unauthorised, so the Gateway
    blocks them and raises the security events the demo needs.
    """

    def __init__(self, agent_id: str, org_id: str, quantity: int) -> None:
        super().__init__(agent_id, org_id, quantity)
        self._attacks = list(self._config.get("scripted_attacks", []))
        self._attempted: set[str] = set()

    @property
    def attack_count(self) -> int:
        return len(self._attacks)

    def pending_attacks(self) -> list[dict[str, Any]]:
        return [a for i, a in enumerate(self._attacks) if i not in self._attempted]

    async def run_next_attack(
        self, session: Session, transaction_id: str
    ) -> GatewayDecision | None:
        """Submit the next scripted unauthorised request. Returns its decision."""
        for index, attack in enumerate(self._attacks):
            if index in self._attempted:
                continue
            self._attempted.add(index)
            identity_mod.touch(session, self.agent_id, "attempting unauthorised capability request")
            session.commit()

            decision = await trust_engine.request_action(
                session,
                ActionRequest(
                    agent_id=self.agent_id,
                    capability=str(attack["capability"]),
                    action=str(attack["action"]),
                    presented_secret=credential_for(self.agent_id),
                    claimed_org_id=self.org_id,
                    transaction_id=transaction_id,
                    parameters=dict(attack.get("parameters", {})),
                ),
                constraints=None,
            )
            logger.info(
                "simulated attack %s -> %s (%s)",
                attack["action"],
                decision.decision,
                decision.rule_id,
            )
            return decision
        return None

    def impersonation_attempt(self) -> dict[str, Any]:
        """The scripted impostor claim used for spec §5 SCENARIO 3."""
        return dict(self._config.get("offer", {}))


def make_supplier(agent_id: str, org_id: str, quantity: int) -> SupplierAgent:
    """Factory that returns the honest or compromised variant as appropriate."""
    from app.agents.agent_registry import AGENTS_BY_ID

    seed = AGENTS_BY_ID.get(agent_id)
    if seed is not None and seed.is_malicious_simulation and seed.agent_type == "supplier":
        return CompromisedAgent(agent_id, org_id, quantity)
    return SupplierAgent(agent_id, org_id, quantity)
