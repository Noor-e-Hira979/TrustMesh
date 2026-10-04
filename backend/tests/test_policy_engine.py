"""Deterministic Policy Engine unit tests.

These are the rules that make "the LLM is not the security authority" true. Every
block reason in the demo traces back to one of these functions.

Referenced by: ``app.security.policy_engine``.
"""

from __future__ import annotations

import pytest

from app.security.policy_engine import (
    ProposedTerms,
    TransactionConstraints,
    catalogue,
    evaluate,
    negotiation_envelope,
)

CONSTRAINTS = TransactionConstraints(
    product="Packaging units",
    quantity=1000,
    budget_cents=200_000,
    max_delivery_days=7,
    certification_required="VALID",
    intent_ref="INTENT-TEST",
)


def test_allows_compliant_terms():
    result = evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=195_000, delivery_days=6, certification="VALID", quantity=1000),
    )
    assert result.allowed
    assert result.rule_id == "ALL_CONSTRAINTS_PASS"


def test_blocks_over_budget():
    result = evaluate(
        CONSTRAINTS, ProposedTerms(price_cents=250_000, delivery_days=5, certification="VALID")
    )
    assert result.blocked
    assert result.rule_id == "BUDGET_CEILING"
    assert result.evidence["authorized_budget_cents"] == 200_000
    assert result.evidence["overage_cents"] == 50_000
    assert "$2,500.00" in result.reason
    assert "$2,000.00" in result.reason


def test_budget_boundary_is_inclusive():
    result = evaluate(
        CONSTRAINTS, ProposedTerms(price_cents=200_000, delivery_days=7, certification="VALID")
    )
    assert result.allowed


def test_blocks_over_delivery():
    result = evaluate(
        CONSTRAINTS, ProposedTerms(price_cents=185_000, delivery_days=10, certification="VALID")
    )
    assert result.blocked
    assert result.rule_id == "DELIVERY_SLA"
    assert "10 days" in result.reason
    assert "7 days" in result.reason


def test_delivery_boundary_is_inclusive():
    assert evaluate(
        CONSTRAINTS, ProposedTerms(price_cents=185_000, delivery_days=7, certification="VALID")
    ).allowed


def test_blocks_insufficient_certification():
    result = evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=190_000, delivery_days=5, certification="SELF_DECLARED"),
    )
    assert result.blocked
    assert result.rule_id == "CERTIFICATION_VALIDITY"


def test_higher_certification_satisfies_requirement():
    assert evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=190_000, delivery_days=5, certification="CERTIFIED"),
    ).allowed


def test_blocks_quantity_mismatch():
    result = evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=190_000, delivery_days=5, certification="VALID", quantity=999),
    )
    assert result.blocked
    assert result.rule_id == "QUANTITY_MATCH"


def test_unverified_counterparty_is_ineligible():
    result = evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=190_000, delivery_days=5, certification="VALID"),
        identity_verified=False,
        identity_reason="Agent is not present in the TrustMesh registry.",
    )
    assert result.blocked
    assert result.rule_id == "COUNTERPARTY_ELIGIBILITY"


def test_blocked_counterparty_is_ineligible():
    result = evaluate(
        CONSTRAINTS,
        ProposedTerms(price_cents=190_000, delivery_days=5, certification="VALID"),
        agent_blocked=True,
    )
    assert result.blocked
    assert result.rule_id == "COUNTERPARTY_ELIGIBILITY"


def test_missing_terms_are_tolerated():
    assert evaluate(CONSTRAINTS, ProposedTerms()).allowed


def test_catalogue_lists_every_rule():
    ids = {rule["rule_id"] for rule in catalogue()}
    assert ids == {
        "BUDGET_CEILING",
        "DELIVERY_SLA",
        "CERTIFICATION_VALIDITY",
        "QUANTITY_MATCH",
        "COUNTERPARTY_ELIGIBILITY",
    }


def test_negotiation_envelope_reports_empty_band():
    floor, ceiling = negotiation_envelope(CONSTRAINTS, 250_000)
    assert floor == 250_000 and ceiling == 200_000
    assert floor > ceiling


@pytest.mark.parametrize(
    ("price", "expected_allowed"),
    [(195_000, True), (200_000, True), (200_001, False), (100_000, True)],
)
def test_budget_sweep(price: int, expected_allowed: bool):
    result = evaluate(
        CONSTRAINTS, ProposedTerms(price_cents=price, delivery_days=6, certification="VALID")
    )
    assert result.allowed is expected_allowed
