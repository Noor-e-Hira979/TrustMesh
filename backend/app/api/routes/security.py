"""Security Center routes: the live alert feed and the mandated demo scenarios.

``POST /security/scenarios/{name}`` exists so a presenter can pace the story and
re-trigger one specific mandated scenario on demand (spec §23). Each scenario is a
*scripted request* from a controlled simulation submitted to the real Gateway - there is
no attack tooling anywhere in this path.

Route map
---------
``GET  /security/alerts``                    paginated feed, filterable
``GET  /security/alerts/{id}``               one alert with its evidence
``GET  /security/stats``                     counts by severity and type, block rate
``GET  /security/rules``                     the complete deterministic rule catalogue
``POST /security/scenarios/{name}``          run one mandated scenario end to end
``POST /security/alerts/{id}/status``        acknowledge or resolve an alert

Referenced by: ``app.main``, the frontend Security Center.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.agents.agent_registry import (
    BUYER_AGENT_ID,
    DEMO_INTENT,
    IMPOSTOR_AGENT_ID,
    SUPPLIER_C_AGENT_ID,
    behaviour,
    credential_for,
)
from app.agents.buyer_agent import BuyerAgent, BuyerIntent
from app.agents.impersonator_agent import ImpersonatorAgent
from app.core.database import run_db
from app.core.models import AlertStatus, SecurityAlert, Transaction
from app.core.schemas import (
    Page,
    ScenarioResultOut,
    SecurityAlertOut,
    SecurityRulesOut,
    SecurityStatsOut,
)
from app.orchestrator.demo_orchestrator import SessionProxy
from app.trust.trust_engine import ActionRequest, trust_engine

logger = logging.getLogger("trustmesh.security-api")

router = APIRouter(tags=["security"])

#: The three scenarios spec §5 requires the demo to show, and what each demonstrates.
SCENARIOS: dict[str, str] = {
    "unauthorized_capability": (
        "SCENARIO 1 - a controlled compromised agent requests the buyer's API credentials."
    ),
    "policy_violation": (
        "SCENARIO 2 - an agent proposes $2,500 against an authorized $2,000 maximum."
    ),
    "identity_mismatch": (
        "SCENARIO 3 - an unregistered agent claims to represent Supplier A."
    ),
}

#: Rule catalogues surfaced by ``GET /security/rules`` so the UI never hard-codes them.
IDENTITY_RULES: tuple[str, ...] = (
    "IDENTITY_UNKNOWN_AGENT",
    "IDENTITY_MISMATCH",
    "IDENTITY_AGENT_BLOCKED",
    "IDENTITY_NO_CREDENTIAL",
)
CREDENTIAL_RULES: tuple[str, ...] = (
    "CREDENTIAL_NOT_PRESENTED",
    "CREDENTIAL_SIGNATURE_MISMATCH",
    "CREDENTIAL_REVOKED",
    "CREDENTIAL_EXPIRED",
    "CREDENTIAL_ORG_BINDING_MISMATCH",
)
AUTHORIZATION_RULES: tuple[str, ...] = (
    "AUTHORIZATION_SCOPE_VIOLATION",
    "AUTHORIZATION_CROSS_ORG",
)
CAPABILITY_RULES: tuple[str, ...] = (
    "CAPABILITY_AUTHORIZED",
    "CAPABILITY_NOT_AUTHORIZED",
    "CAPABILITY_SENTINEL_DENY",
    "CAPABILITY_MALFORMED",
)


# ------------------------------------------------------------------------ read
@router.get(
    "/security/alerts",
    response_model=Page,
    summary="Live security alert feed",
)
async def list_alerts(
    severity: str | None = Query(default=None, pattern="^(CRITICAL|HIGH|MEDIUM|LOW)$"),
    alert_type: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    transaction_id: str | None = Query(default=None),
    open_only: bool = Query(default=False, description="Only alerts still OPEN"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page:
    """Newest first, which is the order the Security Center renders."""
    stmt = select(SecurityAlert).order_by(desc(SecurityAlert.created_at))
    if severity:
        stmt = stmt.where(SecurityAlert.severity == severity.upper())
    if alert_type:
        stmt = stmt.where(SecurityAlert.alert_type == alert_type)
    if agent_id:
        stmt = stmt.where(SecurityAlert.agent_id == agent_id)
    if transaction_id:
        stmt = stmt.where(SecurityAlert.transaction_id == transaction_id)
    if open_only:
        stmt = stmt.where(SecurityAlert.status == AlertStatus.OPEN.value)

    def _query(session: Session) -> Page:
        total = int(
            session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
            .scalar()
            or 0
        )
        rows = list(session.execute(stmt.limit(limit).offset(offset)).scalars().all())
        return Page.build(
            [SecurityAlertOut.model_validate(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    return await run_db(_query)


@router.get(
    "/security/alerts/{alert_id}",
    response_model=SecurityAlertOut,
    summary="One alert with its evidence",
)
async def get_alert(alert_id: str) -> SecurityAlertOut:
    def _query(session: Session) -> SecurityAlertOut:
        alert = session.get(SecurityAlert, alert_id)
        if alert is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Security alert '{alert_id}' not found.",
            )
        return SecurityAlertOut.model_validate(alert)

    return await run_db(_query)


@router.post(
    "/security/alerts/{alert_id}/status",
    response_model=SecurityAlertOut,
    summary="Acknowledge or resolve an alert",
)
async def set_alert_status(
    alert_id: str,
    new_status: str = Query(
        ...,
        alias="status",
        pattern="^(OPEN|ACKNOWLEDGED|RESOLVED)$",
        description="New lifecycle status for the alert",
    ),
) -> SecurityAlertOut:
    """Triage state only. It never changes what happened or clears any evidence."""

    def _apply(session: Session) -> SecurityAlertOut:
        alert = session.get(SecurityAlert, alert_id)
        if alert is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Security alert '{alert_id}' not found.",
            )
        alert.status = new_status.upper()
        session.commit()
        return SecurityAlertOut.model_validate(alert)

    return await run_db(_apply)


@router.get("/security/stats", response_model=SecurityStatsOut, summary="Alert statistics")
async def security_stats() -> SecurityStatsOut:
    def _query(session: Session) -> SecurityStatsOut:
        total = int(session.execute(select(func.count(SecurityAlert.id))).scalar() or 0)
        by_severity = {
            str(k): int(v)
            for k, v in session.execute(
                select(SecurityAlert.severity, func.count(SecurityAlert.id)).group_by(
                    SecurityAlert.severity
                )
            ).all()
        }
        by_type = {
            str(k): int(v)
            for k, v in session.execute(
                select(SecurityAlert.alert_type, func.count(SecurityAlert.id)).group_by(
                    SecurityAlert.alert_type
                )
            ).all()
        }
        blocked = int(
            session.execute(
                select(func.count(SecurityAlert.id)).where(SecurityAlert.decision == "BLOCK")
            ).scalar()
            or 0
        )
        open_alerts = int(
            session.execute(
                select(func.count(SecurityAlert.id)).where(
                    SecurityAlert.status == AlertStatus.OPEN.value
                )
            ).scalar()
            or 0
        )
        simulations = int(
            session.execute(
                select(func.count(SecurityAlert.id)).where(SecurityAlert.is_simulation.is_(True))
            ).scalar()
            or 0
        )
        return SecurityStatsOut(
            total=total,
            by_severity=by_severity,
            by_type=by_type,
            blocked_actions=blocked,
            open_alerts=open_alerts,
            simulated_alerts=simulations,
            window_events=total,
        )

    return await run_db(_query)


@router.get(
    "/security/rules",
    response_model=SecurityRulesOut,
    summary="The complete deterministic rule catalogue",
)
async def rule_catalogue() -> dict:
    """Everything TrustMesh can block on, in one payload.

    Exposed so the UI can render the enforcement surface directly instead of restating
    it, which is what makes the "no hidden rules" claim checkable.
    """
    from app.security import capability_guard, policy_engine, threat_detection

    return {
        "policy_rules": policy_engine.catalogue(),
        "threat_rules": threat_detection.catalogue(),
        "sentinel_capabilities": sorted(capability_guard.SENTINEL_CAPABILITIES),
        "identity_rules": list(IDENTITY_RULES),
        "credential_rules": list(CREDENTIAL_RULES),
        "authorization_rules": list(AUTHORIZATION_RULES),
        "capability_rules": list(CAPABILITY_RULES),
        "scenarios": dict(SCENARIOS),
        "note": (
            "Every rule above is deterministic and unit tested. There is no model-based "
            "risk score anywhere in the enforcement path (spec 8)."
        ),
    }


# -------------------------------------------------------------------- scenarios
@router.post(
    "/security/scenarios/{scenario}",
    response_model=ScenarioResultOut,
    summary="Run one mandated security scenario end to end",
)
async def trigger_scenario(scenario: str) -> ScenarioResultOut:
    """Submit a scripted unauthorised request to the real Gateway and report the verdict.

    Each scenario is attributed to its originating simulation, so the alert that appears
    in the Security Center names the agent that actually made the request.
    """
    if scenario not in SCENARIOS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Unknown scenario '{scenario}'. Available: {', '.join(sorted(SCENARIOS))}."
            ),
        )

    txn_id = await _latest_transaction_id()
    session = SessionProxy()
    try:
        if scenario == "identity_mismatch":
            summary = await _run_identity_mismatch(session, txn_id)
        elif scenario == "unauthorized_capability":
            summary = await _run_unauthorized_capability(session, txn_id)
        else:
            summary = await _run_policy_violation(session, txn_id)
    finally:
        session.close()

    return ScenarioResultOut(**summary)


async def _latest_transaction_id() -> str | None:
    def _query(session: Session) -> str | None:
        txn = session.execute(
            select(Transaction).order_by(desc(Transaction.created_at)).limit(1)
        ).scalar_one_or_none()
        return txn.id if txn else None

    return await run_db(_query)


async def _run_identity_mismatch(session: SessionProxy, txn_id: str | None) -> dict[str, Any]:
    """SCENARIO 3 - an unregistered agent claims to represent Supplier A."""
    agent = ImpersonatorAgent()
    attempt = agent.build_attempt(behaviour(IMPOSTOR_AGENT_ID))
    decision = await agent.attempt(session, attempt, txn_id)
    return _summarise(
        "identity_mismatch",
        decision,
        txn_id,
        note=(
            "An unregistered agent claimed to represent Supplier A with a better offer. "
            "TrustMesh compared the claim against the registry and blocked the interaction "
            "at the identity stage, before any capability or policy evaluation."
        ),
    )


async def _run_unauthorized_capability(
    session: SessionProxy, txn_id: str | None
) -> dict[str, Any]:
    """SCENARIO 1 - the compromised agent solicits the buyer's API credentials."""
    attack = _attack_fixture("buyer_api_credentials")
    decision = await trust_engine.request_action(
        session,
        ActionRequest(
            agent_id=SUPPLIER_C_AGENT_ID,
            capability=str(attack["capability"]),
            action=str(attack["action"]),
            presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
            claimed_org_id="org_supplier_c",
            transaction_id=txn_id,
            parameters=dict(attack.get("parameters", {})),
        ),
    )
    return _summarise(
        "unauthorized_capability",
        decision,
        txn_id,
        note=(
            "The LLM was never asked whether this was safe. Capability validation and "
            "threat interception blocked the request before execution."
        ),
    )


async def _run_policy_violation(session: SessionProxy, txn_id: str | None) -> dict[str, Any]:
    """SCENARIO 2 - an out-of-envelope price proposal."""
    attack = _attack_fixture("exceed_authorized_budget")
    params = dict(attack.get("parameters", {}))

    intent = BuyerIntent(
        product=DEMO_INTENT["product"],
        quantity=DEMO_INTENT["quantity"],
        max_budget_cents=DEMO_INTENT["max_budget_cents"],
        max_delivery_days=DEMO_INTENT["max_delivery_days"],
        certification_required=DEMO_INTENT["certification_required"],
        intent_ref=txn_id or "INTENT-SCENARIO",
    )

    decision = await trust_engine.request_action(
        session,
        ActionRequest(
            agent_id=SUPPLIER_C_AGENT_ID,
            capability="propose",
            action=str(attack["action"]),
            presented_secret=credential_for(SUPPLIER_C_AGENT_ID),
            claimed_org_id="org_supplier_c",
            transaction_id=txn_id,
            parameters={
                "price_cents": int(params.get("price_cents", 250_000)),
                "delivery_days": int(params.get("delivery_days", 5)),
                "certification": params.get("certification", "VALID"),
                "quantity": intent.quantity,
                "message": params.get("message", ""),
            },
        ),
        constraints=intent.constraints(),
    )
    return _summarise(
        "policy_violation",
        decision,
        txn_id,
        note=(
            "The deterministic Policy Engine evaluated price_cents <= budget_cents and "
            "blocked the proposal. The proposing agent could not approve its own action."
        ),
    )


def _attack_fixture(capability: str) -> dict[str, Any]:
    for attack in behaviour(SUPPLIER_C_AGENT_ID).get("scripted_attacks", []):
        if attack.get("capability") == capability:
            return dict(attack)
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Scenario fixture for capability '{capability}' is missing from the registry.",
    )


def _summarise(scenario: str, decision, txn_id: str | None, *, note: str) -> dict[str, Any]:
    """Flatten a GatewayDecision into the scenario response contract."""
    logger.info(
        "scenario %s -> %s (%s, stage=%s)",
        scenario,
        decision.decision,
        decision.rule_id,
        decision.stage,
    )
    return {
        "scenario": scenario,
        "transaction_id": txn_id,
        "decision": decision.decision,
        "rule_id": decision.rule_id,
        "reason": decision.reason,
        "evidence": {
            "stage": decision.stage,
            "capability": decision.capability,
            "action_id": decision.action_id,
            "agent_id": decision.agent_id,
            "pipeline": list(trust_engine.pipeline),
            **decision.evidence,
        },
        "alert_id": decision.alert_id,
        "severity": decision.severity,
        "events_emitted": [
            "POLICY_CHECK_STARTED",
            "SECURITY_ALERT" if decision.blocked else "POLICY_ALLOWED",
            "POLICY_BLOCKED" if decision.blocked else "AGENT_ACTION_EXECUTED",
        ],
        "explanation": note,
    }
