"""Constraint-aware negotiation engine.

Design rule: the negotiator only ever emits terms inside the feasible region
``[supplier_floor, buyer_ceiling]``. That keeps legitimate negotiation budget-safe
by construction - a counter-offer can never be a term the Policy Engine would
certainly reject.

The single deliberate exception is spec §5 SCENARIO 2, where the compromised agent
emits an out-of-envelope proposal *precisely so the Policy Engine can block it*.
That path is explicit and marked.

Referenced by: ``app.orchestrator.demo_orchestrator``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.supplier_agent import OfferTerms, SupplierAgent
from app.security.policy_engine import (
    CERTIFICATION_RANK,
    ProposedTerms,
    TransactionConstraints,
    negotiation_envelope,
)


@dataclass(slots=True)
class NegotiationOutcome:
    agreed: bool
    round_number: int
    buyer_terms: OfferTerms | None
    supplier_terms: OfferTerms | None
    final_terms: OfferTerms | None
    feasible_band: tuple[int, int]
    transcript: list[dict[str, Any]]
    termination_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "agreed": self.agreed,
            "round_number": self.round_number,
            "feasible_band_cents": list(self.feasible_band),
            "final_terms": self.final_terms.as_dict() if self.final_terms else None,
            "termination_reason": self.termination_reason,
            "rounds": self.transcript,
        }


class NegotiationEngine:
    """Turn-based, constraint-aware negotiation between a buyer and one supplier."""

    def __init__(self, constraints: TransactionConstraints, supplier: SupplierAgent) -> None:
        self.constraints = constraints
        self.supplier = supplier
        self.floor_cents, self.ceiling_cents = negotiation_envelope(
            constraints, supplier.floor_cents
        )

    # ------------------------------------------------------------------ utils
    def clamp_to_envelope(self, price_cents: int) -> int:
        """Clamp a price into the feasible band."""
        return max(self.floor_cents, min(int(price_cents), self.ceiling_cents))

    def buyer_counter(
        self, supplier_terms: OfferTerms, round_number: int, *, rationale: str = ""
    ) -> OfferTerms:
        """Buyer opens below the midpoint, then converges on it.

        Deterministic: round 1 sits a quarter of the way up the feasible band, and
        every later round moves to the midpoint. Nothing here consults an LLM, so the
        numbers the Policy Engine judges are reproducible.
        """
        width = self.ceiling_cents - self.floor_cents
        if round_number <= 1:
            price = self.clamp_to_envelope(self.floor_cents + (width // 4))
        else:
            price = self.clamp_to_envelope((self.floor_cents + self.ceiling_cents) // 2)
        return OfferTerms(
            price_cents=price,
            delivery_days=self.constraints.max_delivery_days,
            certification=self.constraints.certification_required,
            quantity=self.constraints.quantity,
            round_number=round_number,
            offer_type="counter",
            rationale=rationale
            or (
                f"Buyer authority ceiling is ${self.ceiling_cents / 100:,.2f} and this "
                f"supplier's floor is ${self.floor_cents / 100:,.2f}. Proposing "
                f"${price / 100:,.2f} with {self.constraints.max_delivery_days}-day delivery "
                "to close inside both envelopes."
            ),
        )

    def infeasible(self, terms: ProposedTerms) -> tuple[bool, str]:
        """Cheap pre-check so the negotiator can explain an empty band."""
        if self.floor_cents > self.ceiling_cents:
            return (
                True,
                (
                    f"Supplier floor ${self.floor_cents / 100:,.2f} exceeds the buyer ceiling "
                    f"${self.ceiling_cents / 100:,.2f}; no agreement is possible."
                ),
            )
        if (
            terms.delivery_days is not None
            and terms.delivery_days > self.constraints.max_delivery_days
        ):
            return (
                True,
                (
                    f"Delivery of {terms.delivery_days} days exceeds the authorized maximum of "
                    f"{self.constraints.max_delivery_days} days and cannot be negotiated."
                ),
            )
        if terms.certification is not None:
            required = (self.constraints.certification_required or "VALID").upper()
            offered = terms.certification.upper()
            if CERTIFICATION_RANK.get(offered, -1) < CERTIFICATION_RANK.get(
                required, CERTIFICATION_RANK["VALID"]
            ):
                return (
                    True,
                    (
                        f"Certification '{offered}' does not meet the required level "
                        f"'{required}'."
                    ),
                )
        return (False, "")

    # --------------------------------------------------------------- session
    def run(self, *, max_rounds: int = 3, ai_rationale: str | None = None) -> NegotiationOutcome:
        """Run the negotiation to agreement or to a justified stop.

        Every offer emitted here is still submitted to the Gateway by the caller -
        this engine proposes, it does not authorise.
        """
        transcript: list[dict[str, Any]] = []
        supplier_offer = self.supplier.initial_offer(ai_rationale=ai_rationale)
        transcript.append(
            {
                "round": 0,
                "actor": self.supplier.agent_id,
                "role": "supplier",
                "offer": supplier_offer.as_dict(),
                "note": "initial offer",
            }
        )

        infeasible, why = self.infeasible(supplier_offer.to_proposed())
        if infeasible:
            return NegotiationOutcome(
                agreed=False,
                round_number=0,
                buyer_terms=None,
                supplier_terms=supplier_offer,
                final_terms=None,
                feasible_band=self.band(),
                transcript=transcript,
                termination_reason=why,
            )

        buyer_terms: OfferTerms | None = None
        current_supplier = supplier_offer

        for round_number in range(1, max_rounds + 1):
            buyer_terms = self.buyer_counter(current_supplier, round_number)
            transcript.append(
                {
                    "round": round_number,
                    "actor": "buyer_agent_1",
                    "role": "buyer",
                    "offer": buyer_terms.as_dict(),
                    "note": "counter-offer inside the feasible band",
                }
            )

            current_supplier = self.supplier.counter_offer(buyer_terms.price_cents)
            transcript.append(
                {
                    "round": round_number,
                    "actor": self.supplier.agent_id,
                    "role": "supplier",
                    "offer": current_supplier.as_dict(),
                    "note": "counter-offer",
                }
            )

            infeasible, why = self.infeasible(current_supplier.to_proposed())
            if infeasible:
                return NegotiationOutcome(
                    agreed=False,
                    round_number=round_number,
                    buyer_terms=buyer_terms,
                    supplier_terms=current_supplier,
                    final_terms=None,
                    feasible_band=self.band(),
                    transcript=transcript,
                    termination_reason=why,
                )

            if self.supplier.accepts(buyer_terms.price_cents) and self.supplier.accepts(
                current_supplier.price_cents
            ):
                final = current_supplier
                final.offer_type = "final"
                final.rationale = (
                    f"Agreed at ${final.price_cents / 100:,.2f} for {self.constraints.quantity} "
                    f"{self.constraints.product.lower()} in {final.delivery_days} days with "
                    f"{final.certification} certification. Inside both authority envelopes."
                )
                return NegotiationOutcome(
                    agreed=True,
                    round_number=round_number,
                    buyer_terms=buyer_terms,
                    supplier_terms=final,
                    final_terms=final,
                    feasible_band=self.band(),
                    transcript=transcript,
                    termination_reason=(
                        "Mutual acceptance inside the buyer ceiling and the supplier floor."
                    ),
                )

        last = current_supplier
        return NegotiationOutcome(
            agreed=self.supplier.accepts(last.price_cents),
            round_number=max_rounds,
            buyer_terms=buyer_terms,
            supplier_terms=last,
            final_terms=last if self.supplier.accepts(last.price_cents) else None,
            feasible_band=self.band(),
            transcript=transcript,
            termination_reason=(
                f"Reached the {max_rounds}-round negotiation limit with the supplier's final "
                f"position at ${last.price_cents / 100:,.2f}."
            ),
        )

    def band(self) -> tuple[int, int]:
        return (self.floor_cents, self.ceiling_cents)


def scripted_over_budget_offer(constraints: TransactionConstraints, price_cents: int) -> OfferTerms:
    """Spec §5 SCENARIO 2 fixture: an intentionally out-of-envelope proposal.

    Documented as a deliberate exception to the clamping rule, produced only by the
    compromised simulation so the Policy Engine can demonstrate a real block.
    """
    return OfferTerms(
        price_cents=price_cents,
        delivery_days=max(1, constraints.max_delivery_days - 2),
        certification=constraints.certification_required,
        quantity=constraints.quantity,
        round_number=0,
        offer_type="counter",
        rationale=(
            "SIMULATED malicious proposal. Deliberately outside the authorized envelope so "
            "the deterministic Policy Engine blocks it (spec §5 SCENARIO 2)."
        ),
    )
