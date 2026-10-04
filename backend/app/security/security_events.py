"""Security event creation and emission.

Single funnel for every alert so that persistence, event emission and the
Security Center feed can never diverge. Called only from the TrustMesh Gateway -
agents never create alerts themselves, because an agent cannot be the judge of its
own conduct.

Referenced by: ``app.trust.trust_engine``, ``app.api.routes.security``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.events import EventType, dispatcher
from app.core.models import AlertStatus, Decision, SecurityAlert, Severity, new_id

logger = logging.getLogger("trustmesh.security")


@dataclass(slots=True)
class AlertDraft:
    agent_id: str
    agent_name: str
    organization_id: str
    organization_name: str
    alert_type: str
    severity: str
    requested_action: str
    requested_capability: str
    reason: str
    rule_id: str
    stage: str
    evidence: dict[str, Any]
    transaction_id: str | None = None
    decision: str = Decision.BLOCK.value
    is_simulation: bool = True


async def raise_alert(session: Session, draft: AlertDraft) -> SecurityAlert:
    """Persist an alert and push it onto the event bus.

    The alert is committed immediately: an alert must survive whatever happens to the
    surrounding transaction, and committing also releases SQLite's single-writer lock
    before the SSE frame is emitted.
    """
    alert = SecurityAlert(
        id=new_id("SA"),
        transaction_id=draft.transaction_id,
        agent_id=draft.agent_id,
        agent_name=draft.agent_name,
        organization_id=draft.organization_id,
        organization_name=draft.organization_name,
        severity=draft.severity,
        alert_type=draft.alert_type,
        requested_action=draft.requested_action[:160],
        requested_capability=draft.requested_capability[:64],
        decision=draft.decision,
        reason=draft.reason,
        evidence=draft.evidence,
        policy_rule_id=draft.rule_id,
        stage=draft.stage,
        status=AlertStatus.OPEN.value,
        is_simulation=draft.is_simulation,
    )
    session.add(alert)
    session.commit()

    await dispatcher.publish(
        EventType.SECURITY_ALERT,
        transaction_id=draft.transaction_id,
        agent_id=draft.agent_id,
        payload={
            "alert_id": alert.id,
            "severity": alert.severity,
            "alert_type": alert.alert_type,
            "agent_id": alert.agent_id,
            "agent_name": alert.agent_name,
            "organization_id": alert.organization_id,
            "organization_name": alert.organization_name,
            "requested_action": alert.requested_action,
            "requested_capability": alert.requested_capability,
            "decision": alert.decision,
            "reason": alert.reason,
            "policy_rule_id": alert.policy_rule_id,
            "stage": alert.stage,
            "status": alert.status,
            "is_simulation": alert.is_simulation,
            "evidence": alert.evidence,
            "ts": alert.created_at.isoformat() if alert.created_at else None,
        },
    )
    logger.info(
        "security alert %s [%s] %s :: %s",
        alert.id,
        alert.severity,
        alert.policy_rule_id,
        alert.reason,
    )
    return alert


def severity_for_rule(rule_id: str) -> str:
    """Map a blocking rule to a severity. Centralised so UI colours stay consistent."""
    mapping = {
        "IDENTITY_MISMATCH": Severity.HIGH.value,
        "IDENTITY_UNKNOWN_AGENT": Severity.HIGH.value,
        "IDENTITY_AGENT_BLOCKED": Severity.HIGH.value,
        "IDENTITY_NO_CREDENTIAL": Severity.HIGH.value,
        "CREDENTIAL_SIGNATURE_MISMATCH": Severity.HIGH.value,
        "CREDENTIAL_REVOKED": Severity.HIGH.value,
        "CREDENTIAL_EXPIRED": Severity.MEDIUM.value,
        "CREDENTIAL_UNKNOWN": Severity.HIGH.value,
        "CAPABILITY_NOT_AUTHORIZED": Severity.CRITICAL.value,
        "CAPABILITY_SENTINEL_DENY": Severity.CRITICAL.value,
        "CAPABILITY_MALFORMED": Severity.MEDIUM.value,
        "AUTHORIZATION_SCOPE_VIOLATION": Severity.HIGH.value,
        "AUTHORIZATION_CROSS_ORG": Severity.CRITICAL.value,
        "BUDGET_CEILING": Severity.HIGH.value,
        "DELIVERY_SLA": Severity.HIGH.value,
        "CERTIFICATION_VALIDITY": Severity.HIGH.value,
        "QUANTITY_MATCH": Severity.HIGH.value,
        "COUNTERPARTY_ELIGIBILITY": Severity.HIGH.value,
    }
    return mapping.get(rule_id, Severity.MEDIUM.value)
