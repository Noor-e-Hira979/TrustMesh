"""Deterministic Policy Engine.

This module is the *only* component allowed to say ALLOW or BLOCK, and it never
consults an LLM. Every rule is a pure, named, testable function over integer cents
and integer days. This is what makes the statement "the LLM is not the security
authority" mechanically true rather than aspirational.

Rules implemented (spec §3 constraints, §5 SCENARIO 2):
``BUDGET_CEILING``, ``DELIVERY_SLA``, ``CERTIFICATION_VALIDITY``, ``QUANTITY_MATCH``,
``NEGOTIATION_FLOOR``, ``COUNTERPARTY_ELIGIBILITY``.

Referenced by: ``app.trust.trust_engine``, ``tests/test_policy_engine.py``,
``scripts/verify_system.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.models import Decision, PolicyStage

# Certification ladder - a higher rank satisfies a lower requirement.
CERTIFICATION_RANK: dict[str, int] = {
    "NONE": 0,
    "SELF_DECLARED": 1,
    "PENDING": 1,
    "VALID": 2,
    "CERTIFIED": 3,
    "ISO9001": 4,
}


@dataclass(slots=True)
class TransactionConstraints:
    """The buyer's authorized envelope. The LLM never writes to this."""

    product: str
    quantity: int
    budget_cents: int
    max_delivery_days: int
    certification_required: str = "VALID"
    intent_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "quantity": self.quantity,
            "budget_cents": self.budget_cents,
            "max_delivery_days": self.max_delivery_days,
            "certification_required": self.certification_required,
            "intent_ref": self.intent_ref,
        }

    @classmethod
    def from_transaction(cls, txn: Any) -> "TransactionConstraints":
        return cls(
            product=txn.product,
            quantity=txn.quantity,
            budget_cents=txn.budget_cents,
            max_delivery_days=txn.max_delivery_days,
            certification_required=txn.certification_required,
            intent_ref=txn.intent_ref,
        )


@dataclass(slots=True)
class ProposedTerms:
    """What an agent wants to do. Untrusted input."""

    price_cents: int | None = None
    delivery_days: int | None = None
    certification: str | None = None
    quantity: int | None = None
    product: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "price_cents": self.price_cents,
            "delivery_days": self.delivery_days,
            "certification": self.certification,
            "quantity": self.quantity,
            "product": self.product,
        }


@dataclass(slots=True)
class PolicyResult:
    decision: str
    rule_id: str
    stage: str
    reason: str
    rule_name: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    constraints_evaluated: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW.value

    @property
    def blocked(self) -> bool:
        return self.decision == Decision.BLOCK.value

    def as_evidence(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "stage": self.stage,
            "decision": self.decision,
            "reason": self.reason,
            "observed": self.evidence,
            "constraints": self.constraints_evaluated,
        }


def _allow(constraints: TransactionConstraints, rule_name: str) -> PolicyResult:
    return PolicyResult(
        decision=Decision.ALLOW.value,
        rule_id=f"{rule_name}_PASS",
        stage=PolicyStage.POLICY.value,
        reason=f"All deterministic constraints satisfied ({rule_name.lower()}).",
        rule_name=rule_name,
        constraints_evaluated=constraints.as_dict(),
    )


def _block(
    constraints: TransactionConstraints,
    rule_name: str,
    reason: str,
    evidence: dict[str, Any],
) -> PolicyResult:
    return PolicyResult(
        decision=Decision.BLOCK.value,
        rule_id=rule_name,
        stage=PolicyStage.POLICY.value,
        reason=reason,
        rule_name=rule_name,
        evidence=evidence,
        constraints_evaluated=constraints.as_dict(),
    )


# --------------------------------------------------------------------- rules
def check_budget(constraints: TransactionConstraints, terms: ProposedTerms) -> PolicyResult | None:
    """price_cents <= budget_cents. The single most important comparison."""
    if terms.price_cents is None:
        return None
    if terms.price_cents <= constraints.budget_cents:
        return None
    return _block(
        constraints,
        "BUDGET_CEILING",
        (
            f"Proposed price ${terms.price_cents / 100:,.2f} exceeds the authorized maximum "
            f"budget of ${constraints.budget_cents / 100:,.2f} "
            f"(over by ${(terms.price_cents - constraints.budget_cents) / 100:,.2f})."
        ),
        {
            "proposed_price_cents": terms.price_cents,
            "authorized_budget_cents": constraints.budget_cents,
            "overage_cents": terms.price_cents - constraints.budget_cents,
            "expression": "price_cents <= budget_cents",
            "satisfied": False,
        },
    )


def check_delivery(constraints: TransactionConstraints, terms: ProposedTerms) -> PolicyResult | None:
    """delivery_days <= max_delivery_days (Supplier A's rejection reason)."""
    if terms.delivery_days is None:
        return None
    if terms.delivery_days <= constraints.max_delivery_days:
        return None
    return _block(
        constraints,
        "DELIVERY_SLA",
        (
            f"Proposed delivery of {terms.delivery_days} days exceeds the authorized maximum "
            f"delivery time of {constraints.max_delivery_days} days."
        ),
        {
            "proposed_delivery_days": terms.delivery_days,
            "authorized_max_delivery_days": constraints.max_delivery_days,
            "overage_days": terms.delivery_days - constraints.max_delivery_days,
            "expression": "delivery_days <= max_delivery_days",
            "satisfied": False,
        },
    )


def check_certification(
    constraints: TransactionConstraints, terms: ProposedTerms
) -> PolicyResult | None:
    """offered certification must satisfy the required level."""
    if terms.certification is None:
        return None
    required = (constraints.certification_required or "VALID").upper()
    offered = terms.certification.upper()
    required_rank = CERTIFICATION_RANK.get(required, CERTIFICATION_RANK["VALID"])
    offered_rank = CERTIFICATION_RANK.get(offered, -1)
    if offered_rank >= required_rank:
        return None
    return _block(
        constraints,
        "CERTIFICATION_VALIDITY",
        f"Offered certification '{offered}' does not satisfy the required level '{required}'.",
        {
            "offered_certification": offered,
            "required_certification": required,
            "offered_rank": offered_rank,
            "required_rank": required_rank,
            "expression": "rank(offered_certification) >= rank(required_certification)",
            "satisfied": False,
        },
    )


def check_quantity(constraints: TransactionConstraints, terms: ProposedTerms) -> PolicyResult | None:
    """The committed quantity must equal the authorized intent quantity."""
    if terms.quantity is None:
        return None
    if terms.quantity == constraints.quantity:
        return None
    return _block(
        constraints,
        "QUANTITY_MATCH",
        (
            f"Proposed quantity {terms.quantity} does not match the authorized intent "
            f"quantity of {constraints.quantity}."
        ),
        {
            "proposed_quantity": terms.quantity,
            "authorized_quantity": constraints.quantity,
            "expression": "quantity == intent.quantity",
            "satisfied": False,
        },
    )


def check_counterparty_eligibility(
    constraints: TransactionConstraints,
    terms: ProposedTerms,
    *,
    identity_verified: bool,
    identity_reason: str = "",
    agent_blocked: bool = False,
) -> PolicyResult | None:
    """An unverified or blocked counterparty cannot produce an executable agreement."""
    if agent_blocked:
        return _block(
            constraints,
            "COUNTERPARTY_ELIGIBILITY",
            "Counterparty agent is blocked by TrustMesh and may not enter an agreement.",
            {
                "identity_verified": identity_verified,
                "agent_blocked": True,
                "identity_reason": identity_reason,
                "expression": "identity_verified AND NOT agent_blocked",
                "satisfied": False,
            },
        )
    if not identity_verified:
        return _block(
            constraints,
            "COUNTERPARTY_ELIGIBILITY",
            identity_reason
            or "Counterparty identity could not be verified, so its terms are not executable.",
            {
                "identity_verified": False,
                "agent_blocked": False,
                "identity_reason": identity_reason,
                "expression": "identity_verified AND NOT agent_blocked",
                "satisfied": False,
            },
        )
    return None


RULES = (
    ("BUDGET_CEILING", check_budget),
    ("DELIVERY_SLA", check_delivery),
    ("CERTIFICATION_VALIDITY", check_certification),
    ("QUANTITY_MATCH", check_quantity),
)


def evaluate(
    constraints: TransactionConstraints,
    terms: ProposedTerms,
    *,
    identity_verified: bool = True,
    identity_reason: str = "",
    agent_blocked: bool = False,
    emit_start_event: bool = True,
) -> PolicyResult:
    """Run every deterministic rule. First violation wins; all are recorded.

    ``emit_start_event`` is a hint for the caller - the engine itself performs no
    I/O so it stays a pure, unit-testable function.
    """
    checked: list[str] = [name for name, _ in RULES]

    eligibility = check_counterparty_eligibility(
        constraints,
        terms,
        identity_verified=identity_verified,
        identity_reason=identity_reason,
        agent_blocked=agent_blocked,
    )
    checked.append("COUNTERPARTY_ELIGIBILITY")
    if eligibility is not None:
        return eligibility

    for name, rule in RULES:
        result = rule(constraints, terms)
        if result is not None:
            result.evidence["rules_evaluated"] = checked
            result.evidence["rules_evaluated_count"] = len(checked)
            return result

    allow = _allow(constraints, "ALL_CONSTRAINTS")
    allow.evidence = {
        "rules_evaluated": checked,
        "rules_evaluated_count": len(checked),
        "observed": terms.as_dict(),
        "satisfied": True,
    }
    return allow


def catalogue() -> list[dict[str, str]]:
    """Human-readable rule catalogue for the UI."""
    return [
        {
            "rule_id": "BUDGET_CEILING",
            "expression": "price_cents <= constraints.budget_cents",
            "description": "Proposed price must not exceed the authorized maximum budget.",
        },
        {
            "rule_id": "DELIVERY_SLA",
            "expression": "delivery_days <= constraints.max_delivery_days",
            "description": "Proposed delivery must not exceed the authorized maximum days.",
        },
        {
            "rule_id": "CERTIFICATION_VALIDITY",
            "expression": "rank(certification) >= rank(required)",
            "description": "Offered certification must satisfy the required certification level.",
        },
        {
            "rule_id": "QUANTITY_MATCH",
            "expression": "quantity == constraints.quantity",
            "description": "Committed quantity must equal the authorized intent quantity.",
        },
        {
            "rule_id": "COUNTERPARTY_ELIGIBILITY",
            "expression": "identity_verified AND NOT agent_blocked",
            "description": "Only verified, non-blocked counterparties may produce terms.",
        },
    ]


def negotiation_envelope(
    constraints: TransactionConstraints, supplier_floor_cents: int
) -> tuple[int, int]:
    """Feasible price band [floor, ceiling] for a constraint-aware negotiation.

    An empty band means no agreement is possible and the negotiation engine must
    terminate rather than emit a term the Policy Engine would certainly reject.
    """
    return (int(supplier_floor_cents), int(constraints.budget_cents))


def is_feasible(constraints: TransactionConstraints, terms: ProposedTerms) -> bool:
    return evaluate(constraints, terms).allowed
